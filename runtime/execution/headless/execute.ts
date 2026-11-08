import { finished } from 'node:stream/promises'
import type { WriteStream } from 'node:fs'
import { IS_MAC, IS_WIN } from '../../config.js'
import { BLENDER_EXE } from '../../paths.js'
import { spawnHeadless } from './process.js'
import type { HeadlessLogStreams } from './process.js'
import type { BuiltHeadlessScript } from './script.js'
import type { HeadlessRunRec, PreparedHeadless, SpawnResult } from './types.js'

/** End both log streams and wait until their handles are released, so callers can delete the run directory safely. */
export async function closeHeadlessLogStreams(streams: HeadlessLogStreams): Promise<void> {
  await closeLogStream(streams.soStream)
  await closeLogStream(streams.seStream)
}

/** End a log stream and wait until its handle is released. */
async function closeLogStream(stream: WriteStream | null): Promise<void> {
  if (!stream) return
  const closed = finished(stream).catch(() => {
    /* the run result already carries the failure detail */
  })
  stream.end()
  await closed
}

export interface ExecutionOutcome {
  res: SpawnResult
  stdout: string
  stderr: string
  ms: number
  /** Resolves once both log streams have flushed and released their handles. */
  logsClosed: Promise<void>
}

/** Spawn Blender, close the log streams, and turn a spawn-level failure into the public error. */
export async function executeHeadless(input: {
  p: PreparedHeadless
  built: BuiltHeadlessScript
  childEnv: Record<string, string | undefined>
  runRec: HeadlessRunRec
  logStreams: HeadlessLogStreams
  t0: number
}): Promise<ExecutionOutcome> {
  const { p, built, childEnv, runRec, logStreams, t0 } = input
  const res = await spawnHeadless(built.args, childEnv, p.timeoutMs, runRec, logStreams)
  const logsClosed = closeHeadlessLogStreams(logStreams)
  const ms = Date.now() - t0
  if (res.spawnErr) {
    await logsClosed
    const msg =
      '起不来 Blender 进程（' +
      BLENDER_EXE +
      '）：' +
      String((res.spawnErr && res.spawnErr.message) || res.spawnErr)
    const hint =
      '自动探测没找到 blender 可执行文件。三种配置方式（任选一种）：' +
      '① 环境变量 DSH_BLENDER_EXE=' +
      (IS_WIN
        ? 'D:\\...\\blender.exe'
        : IS_MAC
          ? '/Applications/Blender.app/Contents/MacOS/Blender'
          : '/mnt/d/.../blender.exe') +
      ' ② 包根写 dsh-blender.config.json: {"blenderExe":"..."}' +
      ' ③ ~/.dsh/dsh-blender.config.json。当前探测结果见 blender_session op=doctor 的 config 字段。'
    throw Object.assign(new Error(msg), { code: 'blender_exe_missing', hint: hint })
  }
  return { res, stdout: String(res.so || ''), stderr: String(res.se || ''), ms, logsClosed }
}
