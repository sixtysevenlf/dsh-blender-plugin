/** blender_py：在 ready 的 GUI 会话里执行 agent 写的 Python（GET /act 路径）。 */
import { BACKEND_HINT } from '../../constants.js'
import { ensureBackend } from '../../host/backend/index.js'
import { backendPost, fetchFrame, leasedText } from '../../host/http.js'
import { text } from '../../contracts/dynamic.js'
import type { HostContext } from '../../host/context.js'
import { dedupeNote, frameDedupe } from '../../utils/frame-cache.js'
import { toAttachment } from '../../utils/image.js'
import { toolError } from '../errors.js'
import { formatActText } from './envelope.js'

/**
 * Execute code inside the GUI session's Blender. The dispatcher already
 * resolved the ready session; no path here starts a headless Blender.
 */
export async function executePy(
  ctx: HostContext,
  port: number,
  args: Record<string, unknown>,
  session: Record<string, unknown>,
): Promise<unknown> {
  if (!(await ensureBackend(port)))
    return toolError(
      'gateway_unreachable',
      'Gateway 不可用（127.0.0.1:' + String(port) + '）',
      BACKEND_HINT,
    )
  const code = String(args.code || '')
  const file = args.file ? String(args.file) : null
  if (!code && !file)
    return toolError(
      'invalid_params',
      'PY 需要 code 或 file 之一',
      '给 code（短 Python）或 file（磁盘上的 .py 脚本路径）',
    )
  const r = await backendPost(
    port,
    '/act',
    {
      code: code,
      file: file,
      py: true,
      instanceId: String(session.instanceId || ''),
      sessionId: String(session.id || ''),
      waitMs: Number(args.wait_ms) || 0,
    },
    180000,
  )
  const lt = leasedText(r)
  if (lt) return { text: lt }
  if (!r || r.ok !== true)
    return toolError(
      'py_error',
      'PY 执行失败 · ' + text(r.error || r.raw || 'unknown'),
      '看 traceback/stderr 修脚本；脚本可以重跑。需要后台执行时用 blender_run(kind=headless)。',
    )
  const out: Record<string, unknown> = { text: formatActText(r).join(String.fromCharCode(10)) }
  if (args.see !== false) {
    const size = Math.max(120, Math.min(1600, Number(args.max_size || 560)))
    let f
    try {
      f = await fetchFrame(port, size, false, 0, String(session.instanceId || ''))
    } catch {
      return out
    }
    const dd = frameDedupe('py-see:' + String(size), f.hash, !!args.force)
    if (dd.dup) {
      out.text = text(out.text) + dedupeNote(dd.repeats)
      return out
    }
    const img = await toAttachment(ctx, f.png, 'py-see-' + String(Date.now()) + '.png')
    if (img.ref) out.image = img.ref
    out.text =
      text(out.text) +
      String.fromCharCode(10) +
      'SEE ' +
      String(size) +
      'px · ' +
      String(f.ms) +
      'ms · hash ' +
      f.hash
    if (!img.ref)
      out.text =
        text(out.text) + String.fromCharCode(10) + '⚠ 图片未回传：' + text(img.why || '未知原因')
  }
  return out
}
