import path from 'node:path'
import fs from 'node:fs'
import { spawn } from 'node:child_process'
import type { ChildProcess, SpawnOptions } from 'node:child_process'
import { winToWsl, wslToWin } from '../../config.js'
import { BLENDER_EXE } from '../../paths.js'
import { PLUGIN_VERSION, pathAudit, sceneHygieneAudit } from '../../core/evidence.js'
import { noInputFileWarning } from '../../file_guard.js'
import { makeStageScanner, startHeartbeat } from '../../core/progress.js'
import { baseChildEnv, withWslEnv, workdirStagingWarning } from '../../proc_env.js'
import { errorMessage } from '../../core/json.js'
import type { JobsDeps, JobRecord } from './types.js'
import { buildJobScript } from './script.js'
import { jobArtifacts, jobSnapshot } from './snapshot.js'

export interface JobStartDeps {
  E: JobsDeps
  jobs: Map<string, JobRecord>
  ledgerAppend: (rec: Record<string, unknown>) => void
}

export function startJob(
  d: JobStartDeps,
  opts: Record<string, unknown> = {},
): Record<string, unknown> {
  const p = buildJobScript(d.E, opts)
  const jobs = d.jobs
  const { E } = d
  const so = fs.createWriteStream(p.outPath),
    se = fs.createWriteStream(p.errPath)
  const j: JobRecord = {
    kind: 'job',
    id: p.id,
    runId: p.runId,
    child: null,
    pid: null,
    startedAt: Date.now(),
    status: 'running',
    exitCode: null,
    signal: null,
    timeoutMs: p.timeoutMs,
    outdir: p.outdirWin,
    outdirWsl: p.outdirWsl,
    dir: p.dir,
    engineMode: p.engineMode,
    parsed: null,
    parseError: null,
    lastException: null,
    script: p.sp.win,
    scriptStaging: p.sp.staging || null,
    scriptFile: p.scriptFileInfo,
    inputFile: p.inputFileInfo,
    args: p.args,
    stage: null,
    stageAt: null,
    stageName: null,
    lastOutputAt: Date.now(),
    lines: 0,
    logBytes: 0,
    resultPath: null,
    resultBytes: 0,
    resultTruncated: false,
    pathWarnings: [],
    shots: null,
  }
  if (j.scriptStaging && j.scriptStaging.substituted) {
    const w = workdirStagingWarning(j.scriptStaging)
    if (w) j.pathWarnings.push(w)
  }
  const spawnOpts: SpawnOptions = {
    env: withWslEnv(
      baseChildEnv(Object.assign({}, p.envPairs)),
      opts.env as Record<string, unknown> | undefined,
    ),
    stdio: ['ignore', 'pipe', 'pipe'],
    detached: false,
    windowsHide: true,
  }
  const child: ChildProcess = spawn(BLENDER_EXE as string, p.args, spawnOpts)
  j.child = child
  j.pid = child.pid
  // v0.9.3（D5）：stage 扫描 + 行数/字节计数（stdout 仍然 pipe 到日志文件）
  const scanOut = makeStageScanner(j),
    scanErr = makeStageScanner(j)
  if (child.stdout)
    child.stdout.on('data', (d: Buffer) => {
      try {
        j.logBytes = (j.logBytes || 0) + d.length
        scanOut(d.toString('utf8'))
      } catch (e) {
        /* ignore */
      }
    })
  if (child.stderr)
    child.stderr.on('data', (d: Buffer) => {
      try {
        scanErr(d.toString('utf8'))
      } catch (e) {
        /* ignore */
      }
    })
  if (child.stdout) child.stdout.pipe(so)
  if (child.stderr) child.stderr.pipe(se)
  jobs.set(p.id, j)
  if (jobs.size > 20) {
    const k = jobs.keys().next().value
    if (k !== undefined && k !== p.id) jobs.delete(k)
  }
  d.ledgerAppend({
    id: p.id,
    kind: 'job',
    status: 'running',
    startedAt: j.startedAt,
    pid: j.pid,
    script: p.sp.win,
    runId: p.runId,
    outdir: p.outdirWin,
    engine: p.engineMode,
    timeoutMs: p.timeoutMs,
    stage: null,
    file: p.fileOpt === undefined ? null : String(p.fileOpt), // F-09（A3）：台账记 file，事后可区分"没给档"与"给了没开"
    dir: wslToWin(p.dir),
    stdoutLog: wslToWin(p.outPath),
    stderrLog: wslToWin(p.errPath),
  })
  j.timer = setTimeout(() => {
    j.status = 'killed'
    try {
      child.kill('SIGKILL')
    } catch (e) {}
  }, p.timeoutMs)
  const stopHb = startHeartbeat(j, p.dir)
  child.on('close', (code, sig) => {
    if (j.timer) clearTimeout(j.timer)
    stopHb()
    j.exitCode = code
    j.signal = sig
    j.finishedAt = Date.now()
    if (j.status !== 'killed') j.status = code === 0 ? 'done' : 'failed'
    try {
      const txt = fs.readFileSync(p.outPath, 'utf8')
      // v0.9.3（D3）：解析失败不再覆盖 raw —— 单独记 resultParseError，stdoutTail 保留原文
      const lines = txt.split('\n').filter((l) => l.indexOf('HEADLESS ') === 0)
      if (lines.length) {
        const lastHead = lines[lines.length - 1].slice('HEADLESS '.length).trim()
        try {
          j.parsed = JSON.parse(lastHead)
        } catch (e) {
          j.parseError = { message: errorMessage(e).slice(0, 200), line: lastHead.slice(0, 400) }
        }
      }
      const sl = txt.split('\n').filter((l) => l.indexOf('DSH_SHOTS ') === 0)
      if (sl.length) {
        try {
          j.shots = JSON.parse(sl[sl.length - 1].slice('DSH_SHOTS '.length).trim())
        } catch (e) {
          /* ignore */
        }
      }
      j.stdoutTail = txt.length > 4000 ? txt.slice(-4000) : txt
      j.pathWarnings = pathAudit(txt, '')
      // F-09：作业路径同样不许"成功但没开档"无声（与 sync 路径同一份守卫语义）
      j.warnings = (j.pathWarnings || [])
        .concat(sceneHygieneAudit(txt, ''))
        .concat(!p.fileOpt || p.fileLooksPython ? [noInputFileWarning(p.fileLooksPython)] : [])
    } catch (e) {
      /* ignore */
    }
    try {
      const er = fs.readFileSync(p.errPath, 'utf8')
      j.stderrTail = er.length > 2000 ? er.slice(-2000) : er
      const m = er.match(/^[\w.]*(?:Error|Exception|Warning)\b.*$/gm)
      if (m && m.length) j.lastException = m[m.length - 1].slice(0, 300)
      if (j.pathWarnings.length === 0) j.pathWarnings = pathAudit('', er)
    } catch (e) {
      /* ignore */
    }
    // v0.9.6（D3）：pathAudit 会整体覆盖 —— 把"workDir 不共享、脚本改落别处"这条找回来（追加在末尾）
    if (
      j.scriptStaging &&
      j.scriptStaging.substituted &&
      !(j.pathWarnings || []).some((w) => w && w.code === 'WORKDIR_NOT_SHARED')
    ) {
      const w = workdirStagingWarning(j.scriptStaging)
      if (w) j.pathWarnings = (j.pathWarnings || []).concat([w])
    }
    // v0.9.3（D2）：作业结果也走 out_json 落盘（>4KB 自动落），与 headless 同一套
    try {
      if (j.parsed !== null && j.parsed !== undefined) {
        const s = JSON.stringify(j.parsed)
        j.resultBytes = s.length
        if (opts.outJson) {
          const t = E.readScriptPath(String(opts.outJson))
          fs.mkdirSync(path.dirname(t.wsl), { recursive: true })
          fs.writeFileSync(t.wsl, s, 'utf8')
          j.resultPath = t.win
          j.resultPathWsl = t.wsl
        } else if (s.length > 4000) {
          const dd = d.E.dumpResult(j.parsed, p.id)
          j.resultPath = dd.path
          j.resultPathWsl = dd.path ? winToWsl(dd.path) : null
        }
        j.resultTruncated = !!(j.resultPath && s.length > 4000)
      }
    } catch (e) {
      /* ignore */
    }
    // v0.8.10（D4）：终态摘要落盘 + 台账追加（后端重启后仍可查）
    try {
      const arts = jobArtifacts(j).slice(0, 20)
      const summary = {
        id: p.id,
        kind: 'job',
        status: j.status,
        exitCode: j.exitCode,
        signal: j.signal,
        startedAt: j.startedAt,
        finishedAt: Date.now(),
        ms: Date.now() - j.startedAt,
        outdir: p.outdirWin,
        script: p.sp.win,
        engine: p.engineMode,
        runId: p.runId,
        file: p.fileOpt === undefined ? null : String(p.fileOpt),
        inputFile: j.inputFile, // F-09（A3）
        stage: j.stageName || null,
        artifacts: arts.map((a) => a.name + '(' + a.bytes + 'B)'),
        resultSummary: j.parsed ? JSON.stringify(j.parsed).slice(0, 400) : null,
        lastException: j.lastException || null,
        pluginVersion: PLUGIN_VERSION,
      }
      try {
        fs.writeFileSync(path.join(j.dir, 'summary.json'), JSON.stringify(summary, null, 1), 'utf8')
      } catch (e) {
        /* ignore */
      }
      d.ledgerAppend(Object.assign({}, summary, { artifacts: arts.slice(0, 8).map((a) => a.name) }))
    } catch (e) {
      /* ignore */
    }
  })
  child.on('error', (e) => {
    j.status = 'failed'
    j.lastException = errorMessage(e)
  })
  return jobSnapshot(j)
}
