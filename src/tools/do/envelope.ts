/** blender_rt_do 的回执装配：ACT / 无头会话 exec / see 的文本（逐字保持 v2.6.2 文案）。 */
import { text } from '../../contracts/dynamic.js'
import { redactSecrets } from '../errors.js'

const LF = String.fromCharCode(10)

export function formatSessionExec(res: Record<string, unknown>, nm: string): string[] {
  const parts: string[] = ['ACT(session=' + nm + ') ok · ' + text(res.ms) + 'ms']
  if (res.stdout && String(res.stdout).trim())
    parts.push('stdout: ' + redactSecrets(res.stdout).slice(0, 3000))
  if (res.stderr && String(res.stderr).trim())
    parts.push('stderr: ' + redactSecrets(res.stderr).slice(0, 1200))
  return parts
}

export function formatSessionSee(
  res: Record<string, unknown>,
  spec: Record<string, unknown>,
): string {
  return (
    'SEE(session) ' +
    text(spec.width) +
    'x' +
    text(spec.height) +
    ' · ' +
    text(res.ms) +
    'ms' +
    (res.coverage_estimate === null || res.coverage_estimate === undefined
      ? ''
      : ' · 覆盖 ' + (Number(res.coverage_estimate) * 100).toFixed(2) + '%')
  )
}

export function formatActText(r: Record<string, unknown>): string[] {
  const parts: string[] = []
  parts.push('ACT ok · ' + text(r.ms) + 'ms')
  if (Number(r.mainThreadMs) > 1000)
    parts.push(
      '⚠️ 本次占用 Blender 主线程约 ' +
        text(r.mainThreadMs) +
        'ms —— 超过 ~1s 的重活建议改走 blender_rt_headless / blender_rt_worker（不卡 GUI）',
    )
  if (r.stderr && String(r.stderr).trim())
    parts.push('stderr: ' + redactSecrets(r.stderr).slice(0, 1200))
  if (r.traceback) parts.push('--- traceback ---' + LF + redactSecrets(r.traceback).slice(0, 2000))
  if (r.stdout) parts.push('stdout: ' + redactSecrets(r.stdout).slice(0, 3000))
  return parts
}
