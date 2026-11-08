/** blender_rt_see 的呈现层：帧文本/空帧告警（不改变任何判定）。 */
import { asRecord, text } from '../../contracts/dynamic.js'

const LF = String.fromCharCode(10)

export function formatHeadlessSeeText(res: Record<string, unknown>): string {
  return (
    '无头 see（Workbench 离屏，GUI 未连接自动降级）· ' +
    text(res.ms) +
    'ms · ' +
    text(res.width) +
    'x' +
    text(res.height) +
    ' · ' +
    text(res.bytes || 0) +
    'B' +
    (res.coverage_estimate === null || res.coverage_estimate === undefined
      ? ''
      : ' · 覆盖 ' + (Number(res.coverage_estimate) * 100).toFixed(2) + '%') +
    ' · 场景与用户视口未改动'
  )
}

interface ViewLike {
  png: Uint8Array
  ms: unknown
  hash: string
  meta: Record<string, unknown> | null
}

export function formatCustomViewText(spec: Record<string, unknown>, fv: ViewLike): string {
  const mt = asRecord(fv.meta)
  return (
    '自定义视角 ' +
    text(mt.mode || spec.mode || 'viewport') +
    ' · ' +
    text(spec.width) +
    'x' +
    text(spec.height) +
    ' · ' +
    text(fv.ms) +
    'ms · ' +
    text((fv.png as Uint8Array).length) +
    'B · hash ' +
    String(fv.hash) +
    ' · from ' +
    JSON.stringify(spec.from) +
    ' → look_at ' +
    JSON.stringify(spec.look_at) +
    (mt.fallback_from ? ' · (viewport 不可用已降级 render：' + text(mt.fallback_from) + ')' : '') +
    (mt.coverage_estimate === undefined || mt.coverage_estimate === null
      ? ''
      : ' · 覆盖 ' + (Number(mt.coverage_estimate) * 100).toFixed(2) + '%') +
    ' · 场景与用户视口均未改动'
  )
}

export function appendEmptyFrameWarning(t: string, raw: unknown): string {
  const warning = asRecord(raw)
  if (warning.code !== 'frame_looks_empty') return t
  const sceneBbox = asRecord(warning.scene_bbox)
  const suggest = asRecord(warning.suggest)
  return (
    t +
    LF +
    '⚠ frame_looks_empty：' +
    text(warning.why || '画面里几乎只剩底色') +
    (warning.scene_bbox
      ? LF +
        '  场景 bbox：center=' +
        JSON.stringify(sceneBbox.center) +
        ' span=' +
        text(sceneBbox.span) +
        ' objects=' +
        text(sceneBbox.objects)
      : '') +
    (warning.suggest
      ? LF +
        '  建议照这组再出一次：from=' +
        JSON.stringify(suggest.from) +
        ' look_at=' +
        JSON.stringify(suggest.look_at)
      : LF + '  ' + text(warning.hint))
  )
}

export function formatPlainFrameText(
  size: number,
  full: boolean,
  f: { png: Uint8Array; ms: unknown; hash: string },
): string {
  return (
    (full ? '区域截图（screenshot_area）· ' : '视口帧 ' + String(size) + 'px · ') +
    String(f.ms) +
    'ms · ' +
    String((f.png as Uint8Array).length) +
    'B · hash ' +
    f.hash
  )
}
