import { vTool } from '../contracts/tool.js'
import { ANY_SCHEMA } from './schema-diagnostic.js'
import { T, BACKEND_HINT } from '../constants.js'
import { ensureBackend } from '../host/backend.js'
import { backendPost, fetchFrame, fetchView, numTriple } from '../host/http.js'
import { frameDedupe, dedupeNote } from '../utils/frame-cache.js'
import { toAttachment, readLocalPng } from '../utils/image.js'
import { renderOne } from '../ui/cards.js'

async function seeViaWorker(ctx: any, port: number, spec: any, name?: string): Promise<any | null> {
  try {
    const r: any = await backendPost(port, '/worker', { op: 'see', name: name || 'default', spec: spec || {}, isolated: true }, 90000)
    if (!r || r.ok !== true) return null
    const res = r.result && typeof r.result === 'object' ? r.result : {}
    if (res.ok === false)
      return {
        text:
          '无头 see 失败 · ' +
          String(res.error || 'unknown') +
          (res.traceback ? String.fromCharCode(10) + String(res.traceback).slice(-1200) : '')
      }
    let data: Uint8Array | null = null
    if (res.png_b64) {
      try {
        data = Buffer.from(String(res.png_b64), 'base64')
      } catch (e: any) {
        data = null
      }
    }
    if (!data && res.path) data = readLocalPng(res.path)
    const att = data ? await toAttachment(ctx, data, 'headless-see-' + String(Date.now()) + '.png') : { why: '没有 png_b64/path' }
    const text =
      '无头 see（Workbench 离屏，GUI 未连接自动降级）· ' +
      String(res.ms) +
      'ms · ' +
      String(res.width) +
      'x' +
      String(res.height) +
      ' · ' +
      String(res.bytes || 0) +
      'B' +
      (res.coverage_estimate === null || res.coverage_estimate === undefined
        ? ''
        : ' · 覆盖 ' + (Number(res.coverage_estimate) * 100).toFixed(2) + '%') +
      ' · 场景与用户视口未改动'
    return att.ref
      ? { text: text, image: att.ref }
      : { text: text + String.fromCharCode(10) + '⚠ 图片未回传：' + String(att.why || '未知原因') }
  } catch (e: any) {
    return null
  }
}

export function registerSee(ctx: any, port: number): void {
  ctx.effect(
    () =>
      ctx.tools.register(
        vTool({
          name: 'blender_rt_see',
          description:
            '【看视口】约 55–160 ms 出一帧：改一步看一眼就用它。GUI 不在或传 session 时自动走持久 worker 的 Workbench 离屏（无头也能同步看）。' +
            '给 from/look_at（可配 lens / ortho / ortho_scale / view_size / shading / overlays / view_mode）走**自定义视角**：自建矩阵离屏绘制，不建相机、不改 scene.camera、不动用户视口。' +
            'full=true + area=N 走整窗口截图（要看 Blender UI 时用）。回执带 coverage_estimate 与 frame_looks_empty 自诊断；同画面默认不重复附图（省视觉 token），要重发传 force=true。' +
            '参数细节见 blender_rt_plan(op="catalog", args={tool:"rt_see"})。',
          parameters: {
            max_size: { type: 'integer', description: '最长边像素，默认 560（420 更快，900 更清晰）' },
            session: { type: 'string', description: '无头 worker 会话名（不走 GUI）' },
            auto: { type: 'boolean', description: 'GUI 不可用时自动降级到无头 see（默认 true）' },
            full: {
              type: 'boolean',
              description: '整窗口/区域截图（screenshot_area 路径）：看 Blender UI 或其他编辑器时用；默认 false = 3D 视口离屏帧'
            },
            area: { type: 'integer', description: 'full=true 时的区域序号，默认 0（第 1 个区域）' },
            from: {
              type: 'string',
              description:
                '【自定义视角】相机位置 "x,y,z"（世界坐标）。给 from + look_at 就走自定义视角出图：套用自建矩阵离屏绘制，完全不动物体、不动用户视口 —— 想"从另一侧看看"或做多角度校验时用它（约 100ms）'
            },
            look_at: { type: 'string', description: '【自定义视角】看向的点 "x,y,z"' },
            lens: { type: 'number', description: '【自定义视角】焦距 mm，默认 50' },
            ortho: { type: 'boolean', description: '【自定义视角】正交投影（配合 ortho_scale）' },
            ortho_scale: { type: 'number', description: '【自定义视角】正交尺度（贴长边），默认 10' },
            view_size: { type: 'string', description: '【自定义视角】分辨率 "宽x高"，如 "1280x720"；默认按 max_size 出 16:9' },
            shading: { type: 'string', description: '【自定义视角】临时切换着色 WIREFRAME/SOLID/MATERIAL/RENDERED（出图后还原）' },
            overlays: { type: 'boolean', description: '【自定义视角】临时开关覆盖物（网格/坐标轴/gizmo），出图后还原' },
            view_mode: {
              type: 'string',
              description: '【自定义视角】viewport（默认，零场景改动，就是视口看到的样子）/ render（临时相机 + Workbench 快渲）'
            },
            diagnostics: {
              type: 'boolean',
              description:
                '【自定义视角】是否强制跑诊断三项（coverage_estimate / scene_bbox / objects_in_frame）。默认不跑（省 45–490 ms/次）；近空帧会自动补跑一次以保住 frame_looks_empty 告警。要逐张核对覆盖率时传 true'
            },
            force: {
              type: 'boolean',
              description: '上一帧与本帧 hash 完全相同时默认**不重复附图**（省模型侧 token）；传 true 强制重发这张图'
            }
          },
          output: { schema: ANY_SCHEMA, render: renderOne },
          isConcurrencySafe: () => true,
          timeoutMs: T.see,
          async execute(args: any) {
            if (!(await ensureBackend(port))) return { text: '后端不可用（127.0.0.1:' + String(port) + '）。' + BACKEND_HINT }
            const size = Math.max(120, Math.min(1600, Number((args && args.max_size) || 560)))
            const full = !!(args && args.full)
            const area = Number((args && args.area) || 0)
            const frm = numTriple(args && args.from)
            const look = numTriple(args && args.look_at)
            // P0：显式 session → 直接走无头 worker（不探 GUI）。
            const wantSession = args && args.session ? String(args.session) : ''
            if (wantSession) {
              const spec: any = {
                from: frm || [7, -7, 5],
                look_at: look || [0, 0, 1],
                lens: args.lens === undefined ? undefined : Number(args.lens),
                ortho: args.ortho === undefined ? undefined : !!args.ortho,
                ortho_scale: args.ortho_scale === undefined ? undefined : Number(args.ortho_scale),
                shading: args.shading || undefined,
                width: size,
                height: Math.max(120, Math.round((size * 9) / 16))
              }
              const vs = String((args && args.view_size) || '')
              const mm = vs.match(/^(\d+)\s*[x×]\s*(\d+)$/)
              if (mm) {
                spec.width = Number(mm[1])
                spec.height = Number(mm[2])
              }
              const fb = await seeViaWorker(ctx, port, spec, wantSession)
              return (
                fb || {
                  text: '无头 see 失败：worker 会话 ' + wantSession + ' 不可用（先 blender_rt_worker op=start name=' + wantSession + '）'
                }
              )
            }
            if (frm || look) {
              const spec: any = {
                from: frm || [7, -7, 5],
                look_at: look || [0, 0, 1],
                lens: args.lens === undefined ? undefined : Number(args.lens),
                ortho: args.ortho === undefined ? undefined : !!args.ortho,
                ortho_scale: args.ortho_scale === undefined ? undefined : Number(args.ortho_scale),
                shading: args.shading || undefined,
                overlays: args.overlays === undefined ? undefined : !!args.overlays,
                mode: args.view_mode || undefined
              }
              const vs = String((args && args.view_size) || '')
              const mm = vs.match(/^(\d+)\s*[x×]\s*(\d+)$/)
              if (mm) {
                spec.width = Number(mm[1])
                spec.height = Number(mm[2])
              } else {
                spec.width = size
                spec.height = Math.max(120, Math.round((size * 9) / 16))
              }
              // v0.9.4（P0-1）：diagnostics 只在显式给出时透传（不传 = view.py 的默认：正常帧不跑、近空帧自动补跑）
              if (args && args.diagnostics !== undefined) spec.diagnostics = !!args.diagnostics
              let fv: any
              try {
                fv = await fetchView(port, spec)
              } catch (e: any) {
                if (!(args && args.auto === false)) {
                  const fb = await seeViaWorker(ctx, port, spec, args && args.session)
                  if (fb) return fb
                }
                throw e
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
                  spec.mode || null
                ])
              const dd2 = frameDedupe(dkey, fv.hash, !!(args && args.force))
              const mt = fv.meta || {}
              const out2: any = {
                text:
                  '自定义视角 ' +
                  String(mt.mode || spec.mode || 'viewport') +
                  ' · ' +
                  String(spec.width) +
                  'x' +
                  String(spec.height) +
                  ' · ' +
                  String(fv.ms) +
                  'ms · ' +
                  String(fv.png.length) +
                  'B · hash ' +
                  fv.hash +
                  ' · from ' +
                  JSON.stringify(spec.from) +
                  ' → look_at ' +
                  JSON.stringify(spec.look_at) +
                  (mt.fallback_from ? ' · (viewport 不可用已降级 render：' + String(mt.fallback_from) + ')' : '') +
                  (mt.coverage_estimate === undefined || mt.coverage_estimate === null
                    ? ''
                    : ' · 覆盖 ' + (Number(mt.coverage_estimate) * 100).toFixed(2) + '%') +
                  ' · 场景与用户视口均未改动'
              }
              // v0.9.1（93-D2）：自定义视角瞄空时直接告警 + 给出该怎么瞄（照 suggest 再出一次即可）
              if (mt.warning && mt.warning.code === 'frame_looks_empty') {
                const w = mt.warning
                out2.text +=
                  String.fromCharCode(10) +
                  '⚠ frame_looks_empty：' +
                  String(w.why || '画面里几乎只剩底色') +
                  (w.scene_bbox
                    ? String.fromCharCode(10) +
                      '  场景 bbox：center=' +
                      JSON.stringify(w.scene_bbox.center) +
                      ' span=' +
                      String(w.scene_bbox.span) +
                      ' objects=' +
                      String(w.scene_bbox.objects)
                    : '') +
                  (w.suggest
                    ? String.fromCharCode(10) +
                      '  建议照这组再出一次：from=' +
                      JSON.stringify(w.suggest.from) +
                      ' look_at=' +
                      JSON.stringify(w.suggest.look_at)
                    : String.fromCharCode(10) + '  ' + String(w.hint || ''))
              }
              if (dd2.dup) {
                out2.text += dedupeNote(dd2.repeats)
              } else {
                const img2 = await toAttachment(ctx, fv.png, 'view-' + String(Date.now()) + '.png')
                if (img2.ref) out2.image = img2.ref
                else out2.text += String.fromCharCode(10) + '⚠ 图片未回传：' + String(img2.why || '未知原因')
              }
              return out2
            }
            let f: any
            try {
              f = await fetchFrame(port, size, full, area)
            } catch (e: any) {
              if (!(args && args.auto === false)) {
                const fb = await seeViaWorker(
                  ctx,
                  port,
                  { from: [7, -7, 5], look_at: [0, 0, 1], width: size, height: Math.max(120, Math.round((size * 9) / 16)) },
                  args && args.session
                )
                if (fb) return fb
              }
              throw e
            }
            // v0.9.4（P0-2）：默认视口帧同样去重（键含 size / area，互不串味）
            const dd = frameDedupe(full ? 'area:' + String(area) : 'viewport:' + String(size), f.hash, !!(args && args.force))
            const out: any = {
              text:
                (full ? '区域截图（screenshot_area）· ' : '视口帧 ' + String(size) + 'px · ') +
                String(f.ms) +
                'ms · ' +
                String(f.png.length) +
                'B · hash ' +
                f.hash
            }
            if (dd.dup) {
              out.text += dedupeNote(dd.repeats)
            } else {
              const img = await toAttachment(ctx, f.png, (full ? 'area-' : 'viewport-') + String(Date.now()) + '.png')
              if (img.ref) out.image = img.ref
              else out.text += String.fromCharCode(10) + '⚠ 图片未回传：' + String(img.why || '未知原因')
            }
            return out
          }
        })
      ),
    '@dsh-external/dsh-blender-plugin: rt-see'
  )
}
