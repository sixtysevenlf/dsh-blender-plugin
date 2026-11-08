/**
 * blender_see 的采集实现：camera / full / viewport|custom 三条通道。
 *
 * 只碰 /view 与 /frame.png，不执行脚本、不改场景；去重键交给 frame-cache。
 */
import { BACKEND_HINT } from '../../constants.js'
import { asRecord, text } from '../../contracts/dynamic.js'
import type { HostContext } from '../../host/context.js'
import { fetchFrame, fetchView } from '../../host/http.js'
import { dedupeNote, frameDedupe } from '../../utils/frame-cache.js'
import { toAttachment } from '../../utils/image.js'
import { toolError } from '../errors.js'
import { publishView } from '../shared/session.js'
import { appendEmptyFrameWarning, formatViewText, pngSize, type ViewLike } from './presenter.js'
import type { FramePlan } from './validate.js'

const DEFAULT_WIDTH = 1024
const DEFAULT_HEIGHT = 576

export function failureFromView(e: unknown): string {
  return e instanceof Error ? e.message : String(e)
}

async function attach(
  ctx: HostContext,
  png: Uint8Array,
  name: string,
): Promise<{ ref: unknown } | null> {
  const img = await toAttachment(ctx, png, name)
  return img.ref ? { ref: img.ref } : null
}

/** 相机视角：当前帧 evaluated 相机；PANO 明确指向 blender_run。 */
export async function captureCamera(
  ctx: HostContext,
  port: number,
  plan: FramePlan,
  maxSize: unknown,
  sessionId?: string,
): Promise<unknown> {
  let fv
  try {
    fv = await fetchView(port, {
      mode: 'camera',
      camera: plan.camera || '',
      max_size: Number(maxSize) || DEFAULT_WIDTH,
    })
  } catch (e: unknown) {
    const msg = failureFromView(e)
    if (/unsupported|pano|panorama/i.test(msg))
      return toolError(
        'unsupported',
        '相机视角不支持：' + text(msg),
        'PANO 相机请用 blender_run(kind=render) 走最终渲染',
      )
    return toolError('backend_error', 'SEE camera 失败 · ' + text(msg), BACKEND_HINT)
  }
  const mt = asRecord(fv.meta)
  const width = Number(mt.width) || 0
  const height = Number(mt.height) || 0
  const dedupe = frameDedupe('see:camera', fv.hash, plan.force)
  const dims = { width, height }
  let t = formatViewText('camera', dims, fv as unknown as ViewLike, {})
  if (dedupe.dup) t += dedupeNote(dedupe.repeats)
  const out: Record<string, unknown> = { text: appendEmptyFrameWarning(t, mt.warning) }
  if (!dedupe.dup) {
    const img = await attach(ctx, fv.png, 'see-camera-' + String(Date.now()) + '.png')
    if (img) out.image = img.ref
  }
  if (sessionId)
    await publishView(port, sessionId, {
      viewpoint: 'camera',
      viewMatrix: mt.view_matrix,
      camera: text(mt.camera) || plan.camera || '',
      frame: mt.frame,
    })
  return out
}

/** 整窗口截图（仅 frame + viewport + full=true，固定 1024 长边）。 */
export async function captureFull(
  ctx: HostContext,
  port: number,
  plan: FramePlan,
  sessionId?: string,
): Promise<unknown> {
  const area = Number(plan.area) || 0
  let fr
  try {
    fr = await fetchFrame(port, DEFAULT_WIDTH, true, area)
  } catch (e: unknown) {
    return toolError('backend_error', 'SEE full 失败 · ' + text(failureFromView(e)), BACKEND_HINT)
  }
  const [width, height] = pngSize(fr.png)
  const dedupe = frameDedupe('see:full:' + String(area), fr.hash, plan.force)
  let t =
    'SEE full · ' +
    String(width) +
    'x' +
    String(height) +
    ' · ' +
    String(fr.ms) +
    'ms · ' +
    String(fr.png.length) +
    'B · hash ' +
    String(fr.hash)
  if (dedupe.dup) t += dedupeNote(dedupe.repeats)
  const out: Record<string, unknown> = { text: t }
  if (!dedupe.dup) {
    const img = await attach(ctx, fr.png, 'see-full-' + String(Date.now()) + '.png')
    if (img) out.image = img.ref
  }
  if (sessionId) await publishView(port, sessionId, { viewpoint: 'viewport', followUser: true })
  return out
}

function viewSpec(plan: FramePlan): Record<string, unknown> {
  const dims = plan.dims || {
    aspect: '16:9',
    maxSize: DEFAULT_WIDTH,
    width: DEFAULT_WIDTH,
    height: DEFAULT_HEIGHT,
  }
  if (plan.viewpoint === 'custom')
    return {
      mode: 'viewport',
      from: plan.from,
      look_at: plan.lookAt,
      lens: plan.lens,
      ortho: plan.ortho,
      ortho_scale: plan.orthoScale,
      width: dims.width,
      height: dims.height,
      shading: plan.shading,
      overlays: plan.overlays,
      diagnostics: plan.diagnostics,
    }
  return { mode: 'current', width: dims.width, height: dims.height }
}

async function publishPlanView(port: number, sessionId: string, plan: FramePlan): Promise<void> {
  if (plan.viewpoint === 'custom')
    await publishView(port, sessionId, {
      viewpoint: 'custom',
      from: plan.from,
      lookAt: plan.lookAt,
      lens: plan.lens,
      ortho: plan.ortho,
      orthoScale: plan.orthoScale,
    })
  else await publishView(port, sessionId, { viewpoint: 'viewport', followUser: true })
}

/** viewport（离屏当前视口）或 custom（离屏固定机位）。 */
export async function captureView(
  ctx: HostContext,
  port: number,
  plan: FramePlan,
  sessionId?: string,
): Promise<unknown> {
  const spec = viewSpec(plan)
  let fv
  try {
    fv = await fetchView(port, spec)
  } catch (e: unknown) {
    return toolError(
      'backend_error',
      'SEE ' + plan.viewpoint + ' 失败 · ' + text(failureFromView(e)),
      BACKEND_HINT,
    )
  }
  const dims = plan.dims || {
    aspect: '16:9',
    maxSize: DEFAULT_WIDTH,
    width: DEFAULT_WIDTH,
    height: DEFAULT_HEIGHT,
  }
  const key =
    plan.viewpoint === 'custom'
      ? 'see:custom:' + JSON.stringify(spec)
      : 'see:current:' + String(dims.width) + 'x' + String(dims.height)
  const dedupe = frameDedupe(key, fv.hash, plan.force)
  const mt = asRecord(fv.meta)
  let t = formatViewText(plan.viewpoint, dims, fv as unknown as ViewLike, spec)
  if (dedupe.dup) t += dedupeNote(dedupe.repeats)
  t = appendEmptyFrameWarning(t, mt.warning)
  const out: Record<string, unknown> = { text: t }
  if (!dedupe.dup) {
    const img = await attach(
      ctx,
      fv.png,
      'see-' + plan.viewpoint + '-' + String(Date.now()) + '.png',
    )
    if (img) out.image = img.ref
  }
  if (sessionId) await publishPlanView(port, sessionId, plan)
  return out
}
