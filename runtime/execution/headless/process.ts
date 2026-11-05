import fs from 'node:fs'
import path from 'node:path'
import { spawn } from 'node:child_process'
import type { ChildProcess } from 'node:child_process'
import { headlessLogNames } from '../../file_guard.js'
import { wslToWin } from '../../config.js'
import { makeStageScanner } from '../../core/progress.js'
import { errorMessage } from '../../core/json.js'
import { BLENDER_EXE } from '../../paths.js'
import type { HeadlessRunRec, SpawnResult } from './types.js'

export interface HeadlessLogStreams {
  logs: HeadlessRunRec['logs']
  soStream: fs.WriteStream | null
  seStream: fs.WriteStream | null
  stamp: string
}

/**
 * Open the per-run stdout/stderr logs. Returning null streams is intentional: a read-only
 * outdir must not fail the run, it only loses live logging (the caller has a fallback).
 */
export function createHeadlessLogStreams(
  logDirWsl: string,
  t0: number,
  runId: string,
): HeadlessLogStreams {
  const stamp = new Date(t0).toISOString().replace(/[:.]/g, '-')
  let logs: HeadlessRunRec['logs']
  let soStream: fs.WriteStream | null = null
  let seStream: fs.WriteStream | null = null
  try {
    fs.mkdirSync(logDirWsl, { recursive: true })
    const logNames = headlessLogNames(stamp, runId) // F-09：同毫秒两个 run 不再互写同一个 log
    const soPath = path.join(logDirWsl, logNames.stdout)
    const sePath = path.join(logDirWsl, logNames.stderr)
    soStream = fs.createWriteStream(soPath)
    seStream = fs.createWriteStream(sePath)
    logs = {
      stdout: wslToWin(soPath),
      stderr: wslToWin(sePath),
      stdoutWsl: soPath,
      stderrWsl: sePath,
    }
  } catch (e) {
    logs = null
  }
  return { logs, soStream, seStream, stamp }
}

/**
 * Spawn Blender and capture its streams until close. The function never rejects for a
 * spawn error: it returns spawnErr so the caller can classify it into a receipt.
 */
export async function spawnHeadless(
  args: string[],
  childEnv: NodeJS.ProcessEnv,
  timeoutMs: number,
  runRec: HeadlessRunRec,
  streams: HeadlessLogStreams,
): Promise<SpawnResult> {
  return new Promise<SpawnResult>((resolve) => {
    let so = '',
      se = ''
    let exitCode: number | null = null
    let signal: NodeJS.Signals | null = null
    let spawnErr: Error | null = null
    let timedOut = false
    const CAP = 262144
    let child: ChildProcess | null
    const { soStream, seStream } = streams
    try {
      child = spawn(BLENDER_EXE as string, args, {
        env: childEnv,
        stdio: ['ignore', 'pipe', 'pipe'],
        windowsHide: true,
      })
      runRec.child = child
      runRec.pid = child.pid
    } catch (e) {
      return resolve({
        so: so,
        se: se,
        exitCode: null,
        signal: null,
        spawnErr: e instanceof Error ? e : new Error(errorMessage(e)),
        timedOut: false,
      })
    }
    // v0.9.3：stage 心跳扫描 + 增量落盘（run 运行期间 op=status.stage / 日志就有内容）
    const scanOut = makeStageScanner(runRec),
      scanErr = makeStageScanner(runRec)
    const push = (buf: Buffer, which: string): void => {
      const t = buf.toString('utf8')
      if (which === 'o') {
        so += t
        if (so.length > CAP) so = so.slice(-CAP)
        scanOut(t)
        try {
          if (soStream) soStream.write(t)
        } catch (e) {
          /* ignore */
        }
      } else {
        se += t
        if (se.length > CAP) se = se.slice(-CAP)
        scanErr(t)
        try {
          if (seStream) seStream.write(t)
        } catch (e) {
          /* ignore */
        }
      }
    }
    const proc = child
    if (!proc)
      return resolve({
        so: so,
        se: se,
        exitCode: null,
        signal: null,
        spawnErr: new Error('spawn 未返回进程'),
        timedOut: false,
      })
    proc.stdout?.on('data', (d: Buffer) => push(d, 'o'))
    proc.stderr?.on('data', (d: Buffer) => push(d, 'e'))
    proc.on('error', (e) => {
      spawnErr = e
    })
    const timer = setTimeout(() => {
      timedOut = true
      try {
        proc.kill('SIGKILL')
      } catch (e) {
        /* ignore */
      }
    }, timeoutMs)
    proc.on('close', (code, sig) => {
      clearTimeout(timer)
      exitCode = code
      signal = sig
      resolve({
        so: so,
        se: se,
        exitCode: exitCode,
        signal: signal,
        spawnErr: spawnErr,
        timedOut: timedOut,
      })
    })
  })
}
