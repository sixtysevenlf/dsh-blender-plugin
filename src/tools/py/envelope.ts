/** blender_py 的回执装配（ACT 通道）。 */
import { text } from '../../contracts/dynamic.js'
import { redactSecrets } from '../errors.js'

const LF = String.fromCharCode(10)

export function formatActText(r: Record<string, unknown>): string[] {
  const parts: string[] = []
  parts.push('PY ok · ' + text(r.ms) + 'ms')
  if (Number(r.mainThreadMs) > 1000)
    parts.push(
      '⚠️ 本次占用 Blender 主线程约 ' +
        text(r.mainThreadMs) +
        'ms —— 超过 ~1s 的重活改走 blender_run(kind=headless)（不卡 GUI）',
    )
  if (r.stderr && String(r.stderr).trim())
    parts.push('stderr: ' + redactSecrets(r.stderr).slice(0, 1200))
  if (r.traceback) parts.push('--- traceback ---' + LF + redactSecrets(r.traceback).slice(0, 2000))
  if (r.stdout) parts.push('stdout: ' + redactSecrets(r.stdout).slice(0, 3000))
  return parts
}
