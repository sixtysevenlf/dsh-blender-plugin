/** blender_see 的呈现层：视角/相机/序列帧文本与空帧告警。 */
import { asRecord, text } from '../../contracts/dynamic.js'

const LF = String.fromCharCode(10)

/** Read PNG width/height from IHDR (the only fields see needs). */
export function pngSize(png: Uint8Array): [number, number] {
  if (!png || png.length < 24 || png[1] !== 0x50 || png[2] !== 0x4e || png[3] !== 0x47)
    return [0, 0]
  const dv = new DataView(png.buffer, png.byteOffset, png.byteLength)
  return [dv.getUint32(16), dv.getUint32(20)]
}

export interface ViewLike {
  png: Uint8Array
  ms: unknown
  hash: string
  meta: Record<string, unknown> | null
}

export function formatViewText(
  label: string,
  dims: { width: number; height: number },
  fv: ViewLike,
  _spec: Record<string, unknown>,
): string {
  const mt = asRecord(fv.meta)
  const extras: string[] = []
  if (label === 'camera') {
    if (mt.camera) extras.push('camera=' + text(mt.camera))
    if (mt.frame !== undefined && mt.frame !== null) extras.push('frame=' + text(mt.frame))
    if (mt.preview_only) extras.push('preview_only')
    if (mt.dof_ignored) extras.push('dof_ignored')
    if (mt.motion_blur_ignored) extras.push('motion_blur_ignored')
  }
  if (mt.fallback_from) extras.push('fallback=' + text(mt.fallback_from))
  const cov =
    mt.coverage_estimate === undefined || mt.coverage_estimate === null
      ? ''
      : ' · 覆盖 ' + (Number(mt.coverage_estimate) * 100).toFixed(2) + '%'
  return (
    'SEE ' +
    label +
    ' · ' +
    String(dims.width) +
    'x' +
    String(dims.height) +
    ' · ' +
    String(fv.ms) +
    'ms · ' +
    String(fv.png.length) +
    'B · hash ' +
    String(fv.hash) +
    (extras.length ? ' · ' + extras.join(' · ') : '') +
    cov +
    (label === 'camera'
      ? ' · 当前帧求值，未改 scene.camera / 帧 / 选择 / 用户视口'
      : ' · 场景与用户视口均未改动')
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

export function formatSequenceText(
  label: string,
  seconds: number,
  fps: number,
  shots: Array<{ t: number; ms: number; hash: string }>,
  distinct: number,
): string {
  const avg = shots.length
    ? Math.round(shots.reduce((n, s) => n + Number(s.ms || 0), 0) / shots.length)
    : 0
  const parts = [
    'SEE sequence(' +
      label +
      ') · ' +
      String(seconds) +
      's @' +
      String(fps) +
      'fps → ' +
      String(shots.length) +
      ' 帧 · 平均 ' +
      String(avg) +
      'ms/帧 · 不同画面 ' +
      String(distinct) +
      '/' +
      String(shots.length) +
      (distinct <= 1 ? ' · static=true' : ''),
  ]
  const timeline = shots.map((s, i) => String(i) + '@' + String(s.t) + 'ms:' + s.hash).join(' ')
  if (timeline) parts.push('timeline ' + timeline)
  return parts.join(LF)
}
