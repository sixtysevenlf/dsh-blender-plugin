// 作业子进程 spawn 与输出接线
import fs from 'node:fs'
import { spawn } from 'node:child_process'
import type { ChildProcess, SpawnOptions } from 'node:child_process'
import { BLENDER_EXE } from '../../../paths.js'
import { assertBlenderExe, blenderExeSource } from '../../exe-guard.js'
import { makeStageScanner, startHeartbeat } from '../../../core/progress.js'
import { baseChildEnv, withWslEnv } from '../../../proc_env.js'
import { wslToWin } from '../../../config.js'
import type { JobRecord } from '../types.js'
import type { PreparedJob } from '../script/index.js'
import type { JobStartDeps } from './index.js'

export function spawnJobChild(
  d: JobStartDeps,
  j: JobRecord,
  p: PreparedJob,
  so: fs.WriteStream,
  se: fs.WriteStream,
  opts: Record<string, unknown>,
): { child: ChildProcess; stopHb: () => void } {
  const jobs = d.jobs
  // job 与 worker 共用预检；为空时立即抛 blender_exe_missing，
  // 不再让 spawn(null) 抛 Node 的原始 TypeError（路由/工具层拿不到可执行指引）。
  assertBlenderExe(BLENDER_EXE, blenderExeSource(), 'blender_run op=start')
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
  // v0.9.3：stage 扫描 + 行数/字节计数（stdout 仍然 pipe 到日志文件）
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
    file: p.fileOpt === undefined ? null : String(p.fileOpt), // F-09：台账记 file，事后可区分"没给档"与"给了没开"
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
  return { child, stopHb }
}
