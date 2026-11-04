import path from 'node:path'
import fs from 'node:fs'
import { winToWsl, wslToWin } from '../../config.js'
import { WIN_TMP } from '../../paths.js'
import { pidAlive } from '../../core/evidence.js'
import type { JobRecord } from './types.js'

export function jobDir(id: string, outdir: string | null | undefined): string {
  const base = outdir ? winToWsl(String(outdir)) : winToWsl(WIN_TMP)
  const d = path.join(base, 'jobs', id)
  fs.mkdirSync(d, { recursive: true })
  return d
}

export function jobArtifacts(
  j: JobRecord,
): Array<{ name: string; bytes: number; mtimeMs: number }> {
  const dir = j.outdir ? winToWsl(String(j.outdir)) : null
  if (!dir) return []
  try {
    const isNoise = (n: string) =>
      n.indexOf('__pycache__') === 0 ||
      /\.pyc$/.test(n) ||
      /\.blend[12]$/.test(n) ||
      n.indexOf('tmp') === 0
    const out: Array<{ name: string; bytes: number; mtimeMs: number }> = []
    for (const n of fs.readdirSync(dir)) {
      const p = path.join(dir, n)
      let st: fs.Stats | null = null
      try {
        st = fs.statSync(p)
      } catch (e) {
        /* skip */
      }
      if (st && st.isFile() && !isNoise(n))
        out.push({ name: n, bytes: st.size, mtimeMs: st.mtimeMs })
    }
    return out.sort((a, b) => b.mtimeMs - a.mtimeMs).slice(0, 40)
  } catch (e) {
    return []
  }
}

export function jobSnapshot(j: JobRecord): Record<string, unknown> {
  // v0.8.9：作业结束后 ms 必须冻结（旧版用 Date.now()，done 的作业放一会儿再看会显示成分钟级）
  const now = Date.now()
  const running = j.status === 'running'
  const outWsl = j.outdirWsl || (j.outdir ? winToWsl(String(j.outdir)) : winToWsl(WIN_TMP))
  let logBytes = j.logBytes || 0
  if (running) {
    try {
      logBytes = fs.statSync(path.join(j.dir, 'stdout.log')).size
    } catch (e) {
      /* 还没建 */
    }
  }
  return {
    kind: 'job',
    id: j.id,
    runId: j.runId || null,
    status: j.status,
    pid: j.pid,
    pidAlive: running ? pidAlive(j.pid) : false,
    ms: (j.finishedAt || now) - j.startedAt,
    timeoutMs: j.timeoutMs,
    outdir: j.outdir || WIN_TMP,
    outdirWsl: outWsl,
    logDir: wslToWin(j.dir),
    logDirWsl: j.dir,
    stdoutLog: wslToWin(path.join(j.dir, 'stdout.log')),
    stderrLog: wslToWin(path.join(j.dir, 'stderr.log')),
    stdoutLogWsl: path.join(j.dir, 'stdout.log'),
    stderrLogWsl: path.join(j.dir, 'stderr.log'),
    exitCode: j.exitCode,
    signal: j.signal,
    engine: j.engineMode,
    // ---- v0.9.3：运行中就能看到进度（不再"6 分钟纯黑盒"）
    stage: j.stage || null,
    stageName: j.stageName || null,
    stageAt: j.stageAt || null,
    stageAgoMs: j.stageAt ? now - j.stageAt : null,
    lastOutputAt: j.lastOutputAt || null,
    idleMs: j.lastOutputAt ? now - j.lastOutputAt : now - j.startedAt,
    lines: j.lines || 0,
    logBytes: logBytes,
    // ---- v0.9.3：结构化结果（不必再手写正则从 stdout 里抠）
    resultJson: j.parsed || null,
    result: j.parsed || null,
    resultParseError: j.parseError || null,
    resultPath: j.resultPath || null,
    resultPathWsl: j.resultPathWsl || null,
    resultBytes: j.resultBytes || 0,
    resultTruncated: !!j.resultTruncated,
    stdoutTail: j.stdoutTail || null,
    stderrTail: j.stderrTail || null,
    pathWarnings: j.pathWarnings || [],
    warnings: j.warnings || j.pathWarnings || [],
    shots: j.shots || null,
    progress: j.progress || null,
    progressAt: j.progressAt || null,
    progressAgeMs: j.progressAt ? now - j.progressAt : null,
    heartbeat: j.heartbeat || null,
    progressHistory: j.progressHistory || [],
    scriptStaging: j.scriptStaging || null,
    inputFile: j.inputFile || null,
    scriptFile: j.scriptFile || null,
    lastException: j.lastException || null,
    artifacts: running ? [] : jobArtifacts(j),
  }
}

/** 从日志文件里取尾部（run 与 job 通用） */
export function tailFile(p: string, tail: number): string | null {
  try {
    return fs.readFileSync(p, 'utf8').slice(-tail)
  } catch (e) {
    return null
  }
}
