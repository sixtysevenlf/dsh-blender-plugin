// 作业记录/产物/落盘
import path from 'node:path'
import fs from 'node:fs'
import { winToWsl } from '../../../config.js'
import { PLUGIN_VERSION, pathAudit, sceneHygieneAudit } from '../../../core/evidence.js'
import { noInputFileWarning } from '../../../file_guard.js'
import { workdirStagingWarning } from '../../../proc_env.js'
import { errorMessage } from '../../../core/json.js'
import { jobArtifacts } from '../snapshot.js'
import type { JobRecord } from '../types.js'
import type { PreparedJob } from '../script/index.js'
import type { JobStartDeps } from './index.js'

export function buildJobRecord(p: PreparedJob): JobRecord {
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
  return j
}

/** stdout/stderr 收尾解析（HEADLESS / DSH_SHOTS / 路径体检） */
export function collectJobOutputs(j: JobRecord, p: PreparedJob): void {
  try {
    const txt = fs.readFileSync(p.outPath, 'utf8')
    // v0.9.3：解析失败不再覆盖 raw —— 单独记 resultParseError，stdoutTail 保留原文
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
  // v0.9.6：pathAudit 会整体覆盖 —— 把"workDir 不共享、脚本改落别处"这条找回来（追加在末尾）
  if (
    j.scriptStaging &&
    j.scriptStaging.substituted &&
    !(j.pathWarnings || []).some((w) => w && w.code === 'WORKDIR_NOT_SHARED')
  ) {
    const w = workdirStagingWarning(j.scriptStaging)
    if (w) j.pathWarnings = (j.pathWarnings || []).concat([w])
  }
}

/** 结果落盘 + 终态摘要 + 台账（后端重启后仍可查） */
export function persistJobResult(
  d: JobStartDeps,
  j: JobRecord,
  p: PreparedJob,
  opts: Record<string, unknown>,
): void {
  const E = d.E
  // v0.9.3：作业结果也走 out_json 落盘（>4KB 自动落），与 headless 同一套
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
  // v0.8.10：终态摘要落盘 + 台账追加（后端重启后仍可查）
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
      inputFile: j.inputFile, // F-09
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
}
