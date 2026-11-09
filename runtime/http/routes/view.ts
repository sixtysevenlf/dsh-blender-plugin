import type { IncomingMessage, ServerResponse } from 'node:http'
import { objArg } from './deps.js'
import type { HttpRouteDeps } from './deps.js'
import { errorMessage, errorField } from '../../core/json.js'
export function viewHeader(meta: Record<string, unknown>): string {
  const spec = objArg(meta.spec)
  const matrix = objArg(meta.matrix)
  const capture = objArg(meta.meta)
  const payload: Record<string, unknown> = {
    mode: String(meta.mode || '').slice(0, 64),
    ms: meta.ms,
    path: meta.path,
    width: meta.width,
    height: meta.height,
    coverage_estimate: meta.coverage_estimate,
    objects_in_frame: meta.objects_in_frame,
    min_margin_px: meta.min_margin_px,
    scene_bbox: meta.scene_bbox,
    warning: typeof meta.warning === 'string' ? meta.warning.slice(0, 512) : meta.warning,
    fallback_from: meta.fallback_from,
    view_matrix: matrix.view || null,
    proj_matrix: matrix.proj || null,
    from: Array.isArray(spec.from) ? spec.from : null,
    look_at: Array.isArray(spec.look_at) ? spec.look_at : null,
    lens: typeof spec.lens === 'number' ? spec.lens : null,
    ortho: spec.ortho === true,
    ortho_scale: typeof spec.ortho_scale === 'number' ? spec.ortho_scale : null,
    sensor: typeof spec.sensor === 'number' ? spec.sensor : null,
    viewpoint: typeof spec.mode === 'string' ? spec.mode : null,
    camera: meta.camera ?? null,
    frame: meta.frame ?? null,
    preview_only: meta.preview_only === true,
    dof_ignored: meta.dof_ignored === true,
    motion_blur_ignored: meta.motion_blur_ignored === true,
    pixel_aspect: meta.pixel_aspect ?? null,
    shading_used: capture.shading_used || null,
    region: capture.region || null,
  }
  let text = JSON.stringify(payload)
  if (text.length > 6000) {
    payload.path = null
    payload.coverage_estimate = null
    payload.objects_in_frame = null
    payload.scene_bbox = null
    payload.region = null
    text = JSON.stringify(payload)
  }
  if (text.length > 6000) {
    text = JSON.stringify({
      mode: payload.mode,
      width: payload.width,
      height: payload.height,
      view_matrix: payload.view_matrix,
      proj_matrix: payload.proj_matrix,
      from: payload.from,
      look_at: payload.look_at,
      lens: payload.lens,
      ortho: payload.ortho,
      ortho_scale: payload.ortho_scale,
      viewpoint: payload.viewpoint,
      camera: payload.camera,
      frame: payload.frame,
    })
  }
  return encodeURIComponent(text)
}

// HTTP 路由 /view：自定义视角
export function createViewRoutes(E: HttpRouteDeps) {
  const { engine, json, pushRtt, readBody, stats } = E
  return {
    async view(req: IncomingMessage, res: ServerResponse, _url: URL, _p: string): Promise<void> {
      const raw = await readBody(req)
      let payload: Record<string, unknown>
      try {
        payload = JSON.parse(raw)
      } catch (e) {
        payload = {}
      }
      const t0 = Date.now()
      try {
        const out = objArg(
          await engine.view(
            objArg(payload),
            payload.instanceId === undefined ? '' : String(payload.instanceId),
          ),
        )
        const meta = objArg(out.meta)
        stats.views++
        stats.lastViewMs = Date.now() - t0
        pushRtt('view', stats.lastViewMs)
        stats.lastError = null
        if (payload.asJson) {
          json(res, 200, { ok: true, ms: Date.now() - t0, meta: out.meta })
          return
        }
        res.writeHead(200, {
          'content-type': 'image/png',
          'cache-control': 'no-store',
          // ⚠ 实测坑（v0.9.1）：HTTP 头值不能含非 ASCII —— warning 里有中文，直接塞会 502 "Invalid character in header content"。
          // 所以整段 URL 编码后再放头里（客户端 decodeURIComponent 还原）。
          'x-dsh-view': viewHeader(meta),
        })
        const png = out.png
        res.end(typeof png === 'string' || Buffer.isBuffer(png) ? png : undefined)
      } catch (e) {
        stats.lastError = errorMessage(e)
        json(res, 502, {
          ok: false,
          error: stats.lastError,
          diagnosis: errorField(e, 'diagnosis') || null,
        })
      }
      return
    },
  }
}
