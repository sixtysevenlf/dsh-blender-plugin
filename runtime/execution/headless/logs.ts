import fs from 'node:fs'
import path from 'node:path'
import { wslToWin } from '../../config.js'
import { headlessLogNames } from '../../file_guard.js'
import type { HeadlessRunRec } from './types.js'

/** Fallback when live log streams could not be opened: write the captured output once at the end. */
export function writeFallbackLogs(
  logDirWsl: string,
  stamp: string,
  runId: string,
  stdout: string,
  stderr: string,
): HeadlessRunRec['logs'] {
  try {
    fs.mkdirSync(logDirWsl, { recursive: true })
    const logNames = headlessLogNames(stamp, runId)
    const soPath = path.join(logDirWsl, logNames.stdout)
    const sePath = path.join(logDirWsl, logNames.stderr)
    fs.writeFileSync(soPath, stdout, 'utf8')
    fs.writeFileSync(sePath, stderr, 'utf8')
    return {
      stdout: wslToWin(soPath),
      stderr: wslToWin(sePath),
      stdoutWsl: soPath,
      stderrWsl: sePath,
    }
  } catch (e) {
    return null
  }
}
