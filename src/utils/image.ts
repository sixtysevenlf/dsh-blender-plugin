import { readFileSync } from 'node:fs'

/** PNG → durable attachment（v0.9.2 起失败**不再静默**：返回 {ref} 或 {why}，由调用方写进工具文本） */
export async function toAttachment(ctx: any, data: Uint8Array, name: string): Promise<{ ref?: any; why?: string }> {
  try {
    const attachments = ctx.get('attachments')
    if (!attachments) return { why: '宿主没有 attachments 服务（ctx.get("attachments") 为空）' }
    const mediaTypes: string[] = attachments.imageLimits?.mediaTypes ?? []
    if (!mediaTypes.includes('image/png'))
      return { why: '宿主 imageLimits.mediaTypes 不含 image/png（' + JSON.stringify(mediaTypes) + '）' }
    const ref = await attachments.saveImage({ data, mediaType: 'image/png', name })
    return {
      ref: {
        attachmentId: ref.attachmentId,
        mediaType: ref.mediaType,
        bytes: ref.bytes,
        width: ref.width,
        height: ref.height,
        ...(ref.name === undefined ? {} : { name: ref.name })
      }
    }
  } catch (e: any) {
    return { why: 'saveImage 失败：' + String((e && e.message) || e) }
  }
}

/** Windows / UNC 路径 → WSL 路径（本机 DSH 在 WSL 里跑；Windows 宿主则原样返回）。 */
export function toWslPath(p: any): string {
  const s = String(p == null ? '' : p)
  if (!s) return s
  // Windows 宿主：D:\... 本来就是本机可用路径，转成 /mnt/d/... 反而读不到（Windows 上无此挂载）。
  if (process.platform === 'win32') return s
  if (/^[A-Za-z]:[\\/]/.test(s)) return '/mnt/' + s[0].toLowerCase() + s.slice(2).replace(/\\/g, '/')
  const m = s.match(/^\\\\wsl\.localhost\\[^\\]+\\(.*)$/i)
  if (m) return '/' + String(m[1]).replace(/\\/g, '/')
  return s
}
/** 读本地 PNG（失败返回 null；调用方负责把 why 写进文本，不静默） */
export function readLocalPng(p: any): Uint8Array | null {
  const s = String(p == null ? '' : p)
  if (!s) return null
  const converted = toWslPath(s)
  const candidates = converted === s ? [s] : [converted, s]
  for (const candidate of candidates) {
    try {
      return readFileSync(candidate)
    } catch (e: any) {
      /* try next form */
    }
  }
  return null
}
/** 批量附图：≤max 张；返回已附的 attachment 与失败清单（路径/原因） */
export async function attachPaths(
  ctx: any,
  paths: any[],
  tag: string,
  max = 2
): Promise<{ images: any[]; used: string[]; failed: string[] }> {
  const images: any[] = [],
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
    const att = await toAttachment(ctx, data, tag + '-' + String(Date.now()) + '-' + String(images.length) + '.png')
    if (att.ref) {
      images.push(att.ref)
      used.push(s)
    } else failed.push(s + '（' + String(att.why || '附图失败') + '）')
  }
  return { images, used, failed }
}
/** 无头 see 兜底：走 /worker 的 see op（Workbench 离屏；不依赖 GUI addon）。返回 {text,image} 或 null。 */

/** 从 plan 回执里收集可附图路径（≤2 张；只认常见产物字段） */
export function planImagePaths(res: any): string[] {
  const r = res && typeof res === 'object' ? res : {}
  const out: string[] = []
  const push = (x: any) => {
    const s = typeof x === 'string' ? x : x && (x.path || x.crop || x.out || x.file)
    if (s) out.push(String(s))
  }
  if (Array.isArray(r.images)) for (const x of r.images) push(x)
  else if (r.images && typeof r.images === 'object') {
    for (const k of ['overlay', 'sheet', 'crops', 'per_view']) {
      const v = r.images[k]
      if (Array.isArray(v)) for (const x of v) push(x)
      else if (v) push(v)
    }
  }
  for (const k of ['overlay', 'sheet', 'diff_heat', 'annotated', 'atlas', 'out']) push(r[k])
  if (Array.isArray(r.crops)) for (const c of r.crops) push(c)
  if (Array.isArray(r.views)) for (const v of r.views) push(v)
  if (r.overlay && r.overlay.path) push(r.overlay.path)
  if (r.sheet && r.sheet.path) push(r.sheet.path)
  return out
}
