/** watch 响应文本：无头 worker 结果与 GUI 采样结果两套口径（帧附件在各自入口组装）。 */
import type { HostContext } from '../../host/context.js'
import { toAttachment, readLocalPng } from '../../utils/image.js'
import { asRecord, text } from '../../contracts/dynamic.js'

/** 无头 worker 返回的 {frames,picks,code,...} → 文本 + 最多 6 张附帧。 */
export async function workerWatchResponse(
  ctx: HostContext,
  res: Record<string, unknown>,
): Promise<unknown> {
  const fr = Array.isArray(res.frames) ? res.frames : []
  const hashes = fr.filter((x) => asRecord(x).ok).map((x) => asRecord(x).hash)
  const distinct = new Set(hashes).size
  const parts: string[] = []
  const codeInfo = asRecord(res.code)
  if (res.code)
    parts.push(
      'code: ' +
        (codeInfo.ok ? 'ok' : '失败') +
        (codeInfo.error ? ' · ' + text(codeInfo.error) : ''),
    )
  parts.push(
    'WATCH(headless) ' +
      text(res.sampled || fr.length) +
      ' 帧 · 不同画面 ' +
      String(distinct) +
      '/' +
      String(hashes.length) +
      ' · ' +
      text(res.ms || 0) +
      'ms' +
      (res.static_render ? ' · 无动画帧范围，静态重复采样' : ''),
  )
  parts.push(
    'timeline: ' +
      fr
        .slice(0, 16)
        .map((x) => text(asRecord(x).frame) + ':' + text(asRecord(x).hash))
        .join(' '),
  )
  const images: unknown[] = []
  let whyNot = ''
  const picks = Array.isArray(res.picks) ? res.picks.slice(0, 6) : []
  for (let i = 0; i < picks.length; i++) {
    const p = asRecord(picks[i])
    let data: Uint8Array | null = null
    if (p.png_b64) {
      try {
        data = Buffer.from(String(p.png_b64), 'base64')
      } catch {
        data = null
      }
    }
    if (!data && p.path) data = readLocalPng(p.path)
    if (!data) {
      if (!whyNot) whyNot = '无 png_b64/path'
      continue
    }
    const att = await toAttachment(
      ctx,
      data,
      'watch-hl-' + text(p.frame) + '-' + text(p.hash) + '.png',
    )
    if (att.ref) images.push(att.ref)
    else if (!whyNot) whyNot = att.why || '未知原因'
  }
  parts.push('附帧：' + String(images.length) + ' 张' + (whyNot ? ' · ⚠ ' + whyNot : ''))
  return images.length
    ? { text: parts.join(String.fromCharCode(10)), images: images }
    : { text: parts.join(String.fromCharCode(10)) }
}

/** GUI 逐帧采样 → 文本 + 均匀抽取的附帧。shots[i]={t,ms,hash,png}。 */
export async function guiWatchResponse(
  ctx: HostContext,
  shots: Array<{ t: number; ms: number; hash: string; png: Uint8Array | null }>,
  parts: string[],
  seconds: number,
  fps: number,
): Promise<Record<string, unknown>> {
  const hashes = shots.map((s) => s.hash)
  const distinct = Array.from(new Set(hashes)).length
  const avgMs = Math.round(
    shots.reduce((a, s) => a + Math.max(0, s.ms), 0) / Math.max(1, shots.length),
  )
  parts.push(
    'WATCH ' +
      String(seconds) +
      's @' +
      String(fps) +
      'fps → ' +
      String(shots.length) +
      ' 帧 · 平均 ' +
      String(avgMs) +
      'ms/帧 · 不同画面 ' +
      String(distinct) +
      '/' +
      String(hashes.length),
  )
  parts.push(
    'timeline: ' + shots.map((s, i) => String(i) + '@' + String(s.t) + 'ms:' + s.hash).join(' '),
  )
  const picks: Array<{ t: number; ms: number; hash: string; png: Uint8Array | null }> = []
  const maxImages = 6
  if (shots.length <= maxImages) {
    for (const s of shots) if (s.png) picks.push(s)
  } else {
    for (let k = 0; k < maxImages; k++) {
      const idx = Math.round((k * (shots.length - 1)) / (maxImages - 1))
      if (shots[idx] && shots[idx].png) picks.push(shots[idx])
    }
  }
  const images: unknown[] = []
  let whyNot: string | undefined
  for (let k = 0; k < picks.length; k++) {
    const att = await toAttachment(
      ctx,
      picks[k].png as Uint8Array,
      'watch-' + String(k) + '-' + picks[k].hash + '.png',
    )
    if (att.ref) images.push(att.ref)
    else if (!whyNot) whyNot = att.why
  }
  parts.push(
    '附帧：' +
      String(images.length) +
      ' 张（均匀抽取）' +
      (whyNot ? ' · ⚠ 有帧未回传：' + whyNot : ''),
  )
  const out: Record<string, unknown> = { text: parts.join(String.fromCharCode(10)) }
  if (images.length) out.images = images
  return out
}
