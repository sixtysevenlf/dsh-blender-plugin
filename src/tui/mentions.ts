import { asArray, asRecord, text } from '../contracts/dynamic.js'
import { MENTION_LIMIT, SCENE_CACHE_LIMIT, SCENE_CACHE_MS } from './constants.js'
import { getJson } from './http.js'
import type { MentionItem } from './types.js'

/** '/host/scene-items' 响应 → 提及条目。插进草稿的是与 Web codec 同一份模型文本（'<blender-object>名字</blender-object>'）。 */
export function mentionItems(body: unknown, limit = MENTION_LIMIT): MentionItem[] {
  if (asRecord(body).ok !== true) return []
  const b = asRecord(body)
  const out: MentionItem[] = []
  const groups: Array<[string, string, string | null]> = [
    ['objects', 'object', null],
    ['materials', 'material', '材质'],
    ['collections', 'collection', '集合'],
  ]
  for (const [field, kind, label] of groups) {
    for (const it of asArray(b[field])) {
      const rec = asRecord(it)
      if (!rec.name || out.length >= limit) continue
      const name = text(rec.name)
      out.push({
        label: name,
        detail: label || text(rec.type) || 'OBJECT',
        insertText: '<blender-' + kind + '>' + name + '</blender-' + kind + '>',
      })
    }
  }
  return out
}

/** 本地过滤：label 含 query（不分大小写）；空 query 给前 50 条。宿主会再按自己的模糊打分排序。 */
export function filterMentionItems(items: MentionItem[], query: string): MentionItem[] {
  const q = String(query || '')
    .trim()
    .toLowerCase()
  const out: MentionItem[] = []
  for (const it of items) {
    if (out.length >= MENTION_LIMIT) break
    if (!q || String(it.label).toLowerCase().indexOf(q) >= 0) out.push(it)
  }
  return out
}

/**
 * '@' 提及的数据源：第一次查询拉一份全量（≤ 1000 条）缓存起来，之后在本地过滤、立即返回；
 * 缓存超过 5 s 就在后台刷新一次（不等它）。冷启动那一次可能超过宿主的单次时限、被丢弃，
 * 但缓存会填上，下一次按键就有结果。只在用户真的打 '@' 时才碰 Blender，不轮询。
 */
export function createSceneMentionSource(
  port: number,
): (query: string, signal: AbortSignal) => Promise<MentionItem[]> {
  const cache: {
    at: number
    items: MentionItem[] | null
    inflight: Promise<void> | null
  } = { at: 0, items: null, inflight: null }
  const refresh = (): Promise<void> => {
    if (cache.inflight) return cache.inflight
    cache.inflight = getJson(
      port,
      '/host/scene-items?limit=' + String(SCENE_CACHE_LIMIT) + '&q=',
      undefined,
      10000,
    )
      .then((body) => {
        if (body && body.ok === true) {
          cache.items = mentionItems(body, SCENE_CACHE_LIMIT)
          cache.at = Date.now()
        }
      })
      .finally(() => {
        cache.inflight = null
      })
    return cache.inflight
  }
  return async (query: string, signal: AbortSignal) => {
    if (Date.now() - cache.at >= SCENE_CACHE_MS) {
      const pending = refresh()
      if (!cache.items) {
        // 冷启动：等第一份结果，但宿主 abort 了就先回空（拉取继续在后台跑）
        await new Promise<void>((resolve) => {
          pending.then(resolve, resolve)
          if (signal) signal.addEventListener('abort', () => resolve(), { once: true })
        })
      }
    }
    return filterMentionItems(cache.items || [], query)
  }
}
