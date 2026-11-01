/** blender_rt_see 执行层：GUI 帧 / 自定义视角 / 无头 worker 三条通道（行为与 v2.6.2 一致）。 */
import type { HostContext } from '../../host/context.js'
import { BACKEND_HINT } from '../../constants.js'
import { ensureBackend } from '../../host/backend/index.js'
import { backendPost, fetchFrame, fetchView, numTriple } from '../../host/http.js'
import { frameDedupe, dedupeNote } from '../../utils/frame-cache.js'
import { toAttachment, readLocalPng } from '../../utils/image.js'
import { asRecord, text } from '../../contracts/dynamic.js'
import { redactSecrets, toolError } from '../errors.js'
import {
  appendEmptyFrameWarning,
  formatCustomViewText,
  formatHeadlessSeeText,
  formatPlainFrameText,
} from './presenter.js'

export async function executeSee(
  args: Record<string, unknown>,
  ctx: HostContext,
  port: number,
): Promise<unknown> {
  if (!(await ensureBackend(port)))
    return toolError(
      'backend_unreachable',
      '后端不可用（127.0.0.1:' + String(port) + '）',
      BACKEND_HINT,
    )
  const size = Math.max(120, Math.min(1600, Number(args.max_size || 560)))
  const frm = numTriple(args.from) ?? null
  const look = numTriple(args.look_at) ?? null
  // P0：显式 session → 直接走无头 worker（不探 GUI）。
  const wantSession = args.session ? String(args.session) : ''
  if (wantSession) return executeSessionSee(args, ctx, port, size, frm, look, wantSession)
  if (frm || look) return executeCustomView(args, ctx, port, size, frm, look)
  return executePlainFrame(args, ctx, port, size, !!args.full, Number(args.area || 0))
}

async function executeSessionSee(
  args: Record<string, unknown>,
  ctx: HostContext,
  port: number,
  size: number,
  frm: number[] | null,
  look: number[] | null,
  wantSession: string,
): Promise<unknown> {
  const spec: Record<string, unknown> = {
    from: frm || [7, -7, 5],
    look_at: look || [0, 0, 1],
    lens: args.lens === undefined ? undefined : Number(args.lens),
    ortho: args.ortho === undefined ? undefined : !!args.ortho,
    ortho_scale: args.ortho_scale === undefined ? undefined : Number(args.ortho_scale),
    shading: args.shading || undefined,
    width: size,
    height: Math.max(120, Math.round((size * 9) / 16)),
  }
  const mm = String(args.view_size || '').match(/^(\d+)\s*[x×]\s*(\d+)$/)
  if (mm) {
    spec.width = Number(mm[1])
    spec.height = Number(mm[2])
  }
  return (
    (await seeViaWorker(ctx, port, spec, wantSession)) ||
    toolError(
      'backend_unreachable',
      '无头 see 失败：worker 会话 ' + wantSession + ' 不可用',
      '先 blender_rt_worker op=start name=' + wantSession + '，或改用 GUI 通道',
    )
  )
}

async function executeCustomView(
  args: Record<string, unknown>,
  ctx: HostContext,
  port: number,
  size: number,
  frm: number[] | null,
  look: number[] | null,
): Promise<unknown> {
  const spec: Record<string, unknown> = {
    from: frm || [7, -7, 5],
    look_at: look || [0, 0, 1],
    lens: args.lens === undefined ? undefined : Number(args.lens),
    ortho: args.ortho === undefined ? undefined : !!args.ortho,
    ortho_scale: args.ortho_scale === undefined ? undefined : Number(args.ortho_scale),
    shading: args.shading || undefined,
    overlays: args.overlays === undefined ? undefined : !!args.overlays,
    mode: args.view_mode || undefined,
  }
  const mm = String(args.view_size || '').match(/^(\d+)\s*[x×]\s*(\d+)$/)
  if (mm) {
    spec.width = Number(mm[1])
    spec.height = Number(mm[2])
  } else {
    spec.width = size
    spec.height = Math.max(120, Math.round((size * 9) / 16))
  }
  // v0.9.4（P0-1）：diagnostics 只在显式给出时透传
  if (args.diagnostics !== undefined) spec.diagnostics = !!args.diagnostics
  let fv
  try {
    fv = await fetchView(port, spec)
  } catch (e) {
    if (args.auto !== false) {
      const fb = await seeViaWorker(
        ctx,
        port,
        spec,
        args.session ? String(args.session) : undefined,
      )
      if (fb) return fb
    }
    return toolError(
      'backend_unreachable',
      '取帧失败 · 127.0.0.1:' + String(port) + ' · ' + text(e),
      BACKEND_HINT,
    )
  }
  // v0.9.4（P0-2）：同一视角签名 + 同一 hash ⇒ 不重复附图
  const dkey =
    'view:' +
    JSON.stringify([
      spec.width,
      spec.height,
      spec.from,
      spec.look_at,
      spec.lens === undefined ? null : spec.lens,
      !!spec.ortho,
      spec.ortho_scale === undefined ? null : spec.ortho_scale,
      spec.shading || null,
      spec.overlays === undefined ? null : !!spec.overlays,
      spec.mode || null,
    ])
  const dd = frameDedupe(dkey, fv.hash, !!args.force)
  const out: Record<string, unknown> = { text: formatCustomViewText(spec, fv) }
  // v0.9.1（93-D2）：自定义视角瞄空时直接告警 + 给出该怎么瞄
  if (fv.meta && fv.meta.warning)
    out.text = appendEmptyFrameWarning(text(out.text), fv.meta.warning)
  if (dd.dup) {
    out.text = text(out.text) + dedupeNote(dd.repeats)
  } else {
    const img = await toAttachment(ctx, fv.png, 'view-' + String(Date.now()) + '.png')
    if (img.ref) out.image = img.ref
    else
      out.text =
        text(out.text) + String.fromCharCode(10) + '⚠ 图片未回传：' + text(img.why || '未知原因')
  }
  return out
}

async function executePlainFrame(
  args: Record<string, unknown>,
  ctx: HostContext,
  port: number,
  size: number,
  full: boolean,
  area: number,
): Promise<unknown> {
  let f
  try {
    f = await fetchFrame(port, size, full, area)
  } catch (e) {
    if (args.auto !== false) {
      const fb = await seeViaWorker(
        ctx,
        port,
        {
          from: [7, -7, 5],
          look_at: [0, 0, 1],
          width: size,
          height: Math.max(120, Math.round((size * 9) / 16)),
        },
        args.session ? String(args.session) : undefined,
      )
      if (fb) return fb
    }
    return toolError(
      'backend_unreachable',
      '取帧失败 · 127.0.0.1:' + String(port) + ' · ' + text(e),
      BACKEND_HINT,
    )
  }
  // v0.9.4（P0-2）：默认视口帧同样去重（键含 size / area，互不串味）
  const dd = frameDedupe(
    full ? 'area:' + String(area) : 'viewport:' + String(size),
    f.hash,
    !!args.force,
  )
  const out: Record<string, unknown> = { text: formatPlainFrameText(size, full, f) }
  if (dd.dup) {
    out.text = text(out.text) + dedupeNote(dd.repeats)
  } else {
    const img = await toAttachment(
      ctx,
      f.png,
      (full ? 'area-' : 'viewport-') + String(Date.now()) + '.png',
    )
    if (img.ref) out.image = img.ref
    else
      out.text =
        text(out.text) + String.fromCharCode(10) + '⚠ 图片未回传：' + text(img.why || '未知原因')
  }
  return out
}

async function seeViaWorker(
  ctx: HostContext,
  port: number,
  spec: Record<string, unknown>,
  name?: string,
): Promise<unknown> {
  try {
    const r = await backendPost(
      port,
      '/worker',
      { op: 'see', name: name || 'default', spec: spec || {}, isolated: true },
      90000,
    )
    if (r.ok !== true) return null
    const res = asRecord(r.result)
    if (res.ok === false)
      return toolError(
        'backend_error',
        '无头 see 失败 · ' + text(res.error || 'unknown'),
        '用 blender_rt_worker op=start 检查无头 worker；或改用 GUI 通道',
        res.traceback ? redactSecrets(res.traceback).slice(-1200) : undefined,
      )
    let data: Uint8Array | null = null
    if (res.png_b64) {
      try {
        data = Buffer.from(String(res.png_b64), 'base64')
      } catch {
        data = null
      }
    }
    if (!data && res.path) data = readLocalPng(res.path)
    const att = data
      ? await toAttachment(ctx, data, 'headless-see-' + String(Date.now()) + '.png')
      : { why: '没有 png_b64/path' }
    const rendered = formatHeadlessSeeText(res)
    return att.ref
      ? { text: rendered, image: att.ref }
      : {
          text: rendered + String.fromCharCode(10) + '⚠ 图片未回传：' + text(att.why || '未知原因'),
        }
  } catch {
    return null
  }
}
