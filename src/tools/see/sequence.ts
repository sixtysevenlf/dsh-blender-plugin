/**
 * blender_see(mode=sequence)：在固定离屏视角上采样若干帧。
 *
 * 只做"采集"：不执行 caller 的 code、不改场景、不回落无头 worker。
 * 生成/分析归 blender_run / blender_op。
 */
import { asRecord, text } from '../../contracts/dynamic.js'
import type { HostContext } from '../../host/context.js'
import { fetchView } from '../../host/http.js'
import { toAttachment } from '../../utils/image.js'
import { toolError } from '../errors.js'
import { publishView } from '../shared/session.js'
import { poseFromMeta } from './capture.js'
import { formatSequenceText } from './presenter.js'

export interface SequenceOpts {
  viewpoint: 'viewport' | 'custom'
  width: number
  height: number
  seconds: number
  fps: number
  sessionId?: string
  from?: string
  lookAt?: string
  lens?: number
  ortho?: boolean
  orthoScale?: number
  shading?: string
  overlays?: boolean
  instanceId?: string
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, Math.max(0, ms)))
}

export async function executeSequence(
  ctx: HostContext,
  port: number,
  opts: SequenceOpts,
): Promise<unknown> {
  const total = Math.max(1, Math.min(32, Math.round(opts.seconds * opts.fps)))
  const spec: Record<string, unknown> =
    opts.viewpoint === 'custom'
      ? {
          mode: 'viewport',
          from: opts.from,
          look_at: opts.lookAt,
          lens: opts.lens,
          ortho: opts.ortho,
          ortho_scale: opts.orthoScale,
          width: opts.width,
          height: opts.height,
          shading: opts.shading,
          overlays: opts.overlays,
        }
      : { mode: 'current', width: opts.width, height: opts.height }
  const interval = (opts.seconds * 1000) / total
  const shots: Array<{ t: number; ms: number; hash: string; png: Uint8Array }> = []
  const started = Date.now()
  let lastError = ''
  let firstMeta: Record<string, unknown> | null = null
  for (let i = 0; i < total; i++) {
    const t0 = Date.now()
    try {
      const fv = await fetchView(port, spec, 180000, opts.instanceId || '', opts.sessionId || '')
      if (!firstMeta) firstMeta = asRecord(fv.meta)
      shots.push({ t: Date.now() - started, ms: fv.ms, hash: fv.hash, png: fv.png })
    } catch (e: unknown) {
      lastError = e instanceof Error ? e.message : String(e)
    }
    const spent = Date.now() - t0
    if (i < total - 1) await sleep(interval - spent)
  }
  if (!shots.length)
    return toolError(
      'backend_error',
      'SEE sequence 失败 · 全部帧采集失败 · ' + text(lastError || 'unknown'),
      '先 blender_session action=status 看 Gateway/addon 状态；sequence 只走 GUI，不会降级 headless',
    )
  // presence：sequence 只发布第一帧的固定 pose（spec），不随后续帧漂移。
  if (firstMeta && opts.sessionId)
    await publishView(port, opts.sessionId, poseFromMeta(firstMeta, opts.viewpoint))
  const seen = new Set<string>()
  const images: string[] = []
  for (const shot of shots) {
    if (seen.has(shot.hash) || images.length >= 6) continue
    seen.add(shot.hash)
    const img = await toAttachment(
      ctx,
      shot.png,
      'see-seq-' + String(Date.now()) + '-' + shot.hash + '.png',
    )
    if (img.ref) images.push(img.ref.attachmentId)
  }
  const textOut = formatSequenceText(
    opts.viewpoint,
    opts.seconds,
    opts.fps,
    shots.map((s) => ({ t: s.t, ms: s.ms, hash: s.hash })),
    new Set(shots.map((s) => s.hash)).size,
  )
  return { text: textOut, images: images.length ? images : undefined }
}
