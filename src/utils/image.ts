import { readFileSync } from 'node:fs'
import { asArray, asRecord, text } from '../contracts/dynamic.js'

/** 宿主 attachments 服务的最小结构（只覆盖本插件用到的字段）。 */
export interface AttachmentRef {
  attachmentId: string
  mediaType: string
  bytes: number
  width: number
  height: number
  name?: string
}

interface SaveImageInput {
  data: Uint8Array
  mediaType: string
  name: string
}

interface AttachmentService {
  imageLimits?: { mediaTypes?: string[] }
  saveImage(input: SaveImageInput): Promise<AttachmentRef>
}

interface AttachmentHost {
  get(name: string): unknown
}

/** PNG → durable attachment（v0.9.2 起失败**不再静默**：返回 {ref} 或 {why}，由调用方写进工具文本） */
export async function toAttachment(
  ctx: unknown,
  data: Uint8Array,
  name: string,
): Promise<{ ref?: AttachmentRef; why?: string }> {
  try {
    const host = ctx as AttachmentHost
    const attachments = host.get('attachments') as AttachmentService | undefined
    if (!attachments) return { why: '宿主没有 attachments 服务（ctx.get("attachments") 为空）' }
    const mediaTypes: string[] = attachments.imageLimits?.mediaTypes ?? []
    if (!mediaTypes.includes('image/png'))
      return {
        why: '宿主 imageLimits.mediaTypes 不含 image/png（' + JSON.stringify(mediaTypes) + '）',
      }
    const ref = await attachments.saveImage({ data, mediaType: 'image/png', name })
    return {
      ref: {
        attachmentId: ref.attachmentId,
        mediaType: ref.mediaType,
        bytes: ref.bytes,
        width: ref.width,
        height: ref.height,
        ...(ref.name === undefined ? {} : { name: ref.name }),
      },
    }
  } catch (e: unknown) {
    return { why: 'saveImage 失败：' + (e instanceof Error ? e.message : String(e)) }
  }
}

/** Windows / UNC 路径 → WSL 路径（本机 DSH 在 WSL 里跑；Windows 宿主则原样返回）。 */
export function toWslPath(p: unknown): string {
  const s = String(p == null ? '' : p)
  if (!s) return s
  // Windows 宿主：D:\... 本来就是本机可用路径，转成 /mnt/d/... 反而读不到（Windows 上无此挂载）。
  if (process.platform === 'win32') return s
  if (/^[A-Za-z]:[\\/]/.test(s))
    return '/mnt/' + s[0].toLowerCase() + s.slice(2).replace(/\\/g, '/')
  const m = s.match(/^\\\\wsl\.localhost\\[^\\]+\\(.*)$/i)
  if (m) return '/' + String(m[1]).replace(/\\/g, '/')
  return s
}
/** 读本地 PNG（失败返回 null；调用方负责把 why 写进文本，不静默） */
export function readLocalPng(p: unknown): Uint8Array | null {
  const s = String(p == null ? '' : p)
  if (!s) return null
  const converted = toWslPath(s)
  const candidates = converted === s ? [s] : [converted, s]
  for (const candidate of candidates) {
    try {
      return readFileSync(candidate)
    } catch {
      /* try next form */
    }
  }
  return null
}
/** 批量附图：≤max 张；返回已附的 attachment 与失败清单（路径/原因） */
export async function attachPaths(
  ctx: unknown,
  paths: readonly unknown[],
  tag: string,
  max = 2,
): Promise<{ images: AttachmentRef[]; used: string[]; failed: string[] }> {
  const images: AttachmentRef[] = [],
    used: string[] = [],
    failed: string[] = []
  const seen = new Set<string>()
  for (const p of paths || []) {
    if (images.length >= max) break
    const s = String(p == null ? '' : p)
    if (!s || seen.has(s)) continue
    seen.add(s)
    const data = readLocalPng(s)
    if (!data) {
      failed.push(s + '（读不到）')
      continue
    }
    const att = await toAttachment(
      ctx,
      data,
      tag + '-' + String(Date.now()) + '-' + String(images.length) + '.png',
    )
    if (att.ref) {
      images.push(att.ref)
      used.push(s)
    } else failed.push(s + '（' + String(att.why || '附图失败') + '）')
  }
  return { images, used, failed }
}

/** 从 plan 回执里收集可附图路径（≤2 张；只认常见产物字段） */
export function planImagePaths(res: unknown): string[] {
  const r = asRecord(res)
  const out: string[] = []
  const push = (x: unknown) => {
    if (typeof x === 'string') {
      out.push(x)
      return
    }
    const rec = asRecord(x)
    const s = text(rec.path) || text(rec.crop) || text(rec.out) || text(rec.file)
    if (s) out.push(s)
  }
  const images = r.images
  if (Array.isArray(images)) for (const x of images) push(x)
  else if (images && typeof images === 'object') {
    for (const k of ['overlay', 'sheet', 'crops', 'per_view']) {
      const v = (images as Record<string, unknown>)[k]
      if (Array.isArray(v)) for (const x of v) push(x)
      else if (v) push(v)
    }
  }
  for (const k of ['overlay', 'sheet', 'diff_heat', 'annotated', 'atlas', 'out']) push(r[k])
  for (const c of asArray(r.crops)) push(c)
  for (const v of asArray(r.views)) push(v)
  const overlay = asRecord(r.overlay)
  if (overlay.path) push(overlay.path)
  const sheet = asRecord(r.sheet)
  if (sheet.path) push(sheet.path)
  return out
}
