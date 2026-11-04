import { asArray, asRecord, isRecord, text } from '../contracts/dynamic.js'
import { CHIP_KEY, SELECTION_DRAFT_NAME_LIMIT, SUB_DRAFT_INDEX_LIMIT } from './constants.js'
import { getJson, postJson } from './http.js'
import { bridge } from './state.js'
import type { ChipService, SelectionState } from './types.js'

/** v2.4 子元素选中（网格点/边/面、骨骼、曲线/晶格控制点）→ 文本。逐字对齐 lib/client.js 的 subSelectionText。 */
export function subSelectionText(sel: unknown): string | null {
  const s = asRecord(sel)
  const sub = isRecord(s.sub) ? s.sub : null
  if (!sub) return null
  const type = text(sub.type)
  const obj = text(sub.object)
  const counts = asRecord(sub.counts)
  const more = sub.truncated === true
  if (type === 'MESH') {
    const v = Number(counts.vertices || 0),
      e = Number(counts.edges || 0),
      f = Number(counts.faces || 0)
    let txt =
      'Blender 编辑模式（网格）「' + obj + '」：已选 ' + v + ' 顶点 / ' + e + ' 边 / ' + f + ' 面。'
    const idx = asRecord(sub.indices)
    const labels: Record<string, string> = {
      vertices: '顶点索引',
      edges: '边索引',
      faces: '面索引',
    }
    const parts: string[] = []
    for (const k of Object.keys(labels)) {
      const arr = asArray(idx[k])
      if (!arr.length) continue
      let shown = arr.slice(0, SUB_DRAFT_INDEX_LIMIT).join(',')
      if (arr.length > SUB_DRAFT_INDEX_LIMIT || more) shown += '…'
      parts.push(labels[k] + '：' + shown)
    }
    if (parts.length) txt += parts.join('；') + '。'
    return txt + '用 bpy / BMesh 精确修改这些元素；用户说"这些点/线/面"时指它们。'
  }
  if (type === 'ARMATURE') {
    const bones = asArray(sub.bones)
    const boneCount = Number(counts.bones || bones.length)
    let head =
      'Blender 骨骼（骨架）「' +
      obj +
      '」' +
      (text(sub.mode) === 'POSE' ? '姿势模式' : '编辑模式') +
      '：已选 ' +
      boneCount +
      ' 根'
    if (bones.length) {
      head += '：' + bones.slice(0, SUB_DRAFT_INDEX_LIMIT).map(String).join('、')
      if (bones.length > SUB_DRAFT_INDEX_LIMIT || more) head += '…'
    }
    return (
      head +
      "。用 bpy.data.objects['" +
      obj +
      '\'].pose.bones[...] 精确定位；用户说"这些骨骼"时指它们。'
    )
  }
  if (type === 'CURVE' || type === 'LATTICE') {
    const pts = asArray(sub.points)
    const pcount = Number(counts.points || pts.length)
    const label = type === 'CURVE' ? '曲线/曲面控制点' : '晶格控制点'
    let txt = 'Blender ' + label + '「' + obj + '」：' + text(sub.mode) + '，已选 ' + pcount + ' 个'
    if (pts.length) {
      const sample = pts.slice(0, SUB_DRAFT_INDEX_LIMIT).map((p) => {
        const pair = asArray(p)
        return pair.length >= 2 ? String(pair[0]) + ':' + String(pair[1]) : String(p)
      })
      txt += '：' + sample.join('、')
      if (pts.length > SUB_DRAFT_INDEX_LIMIT || more) txt += '…'
    }
    return txt + '。元素用 (spline_index, point_index) 定位；用户说"这些点"时指它们。'
  }
  if (type === 'OTHER') {
    return (
      'Blender ' +
      (text(sub.mode) || '编辑模式') +
      '（对象「' +
      obj +
      '」）：' +
      (text(sub.note) || '子元素暂未展开，请用 Python 读当前选择集') +
      '。'
    )
  }
  return null
}

/** selection payload → 给模型的文本（空选中回 null）。逐字对齐 lib/client.js 的 selectionMessageText。 */
export function selectionMessageText(payload: unknown): string | null {
  const sel = asRecord(asRecord(payload).selection)
  if (!isRecord(payload) || !Object.keys(sel).length) return null
  const kind = sel.kind === 'collection' ? 'collection' : 'objects'
  const names = asArray(sel.names).filter((n): n is string => typeof n === 'string' && n.length > 0)
  const count = typeof sel.count === 'number' ? sel.count : names.length
  if (kind === 'collection') {
    const coll =
      typeof sel.collection === 'string' && sel.collection.length > 0 ? sel.collection : null
    if (!coll) return null
    const shown = names.slice(0, SELECTION_DRAFT_NAME_LIMIT)
    let head = 'Blender 集合：' + coll + '（' + count + ' 件'
    if (shown.length) {
      head += '，前 ' + shown.length + ' 件：' + shown.join('、')
      if (sel.truncated || count > shown.length || names.length > shown.length) head += '…'
    }
    return (
      head +
      "）。集合成员用 bpy.data.collections['" +
      coll +
      '\'].objects 取；用户说"这个集合/这批"时指它。'
    )
  }
  const subText = subSelectionText(sel)
  if (subText) return subText
  if (!names.length) return null
  const drawn = names.slice(0, SELECTION_DRAFT_NAME_LIMIT)
  let out = 'Blender 选中：' + drawn.join('、')
  if (sel.truncated || count > drawn.length || names.length > drawn.length)
    out += '（共 ' + count + ' 件，只列前 ' + drawn.length + ' 件）'
  out += '。'
  if (sel.active) out += '活动对象：' + text(sel.active) + '。'
  if (sel.mode) out += '模式：' + text(sel.mode) + '。'
  const collections = asArray(sel.collections)
  if (collections.length && count > 1) {
    const summary = collections.slice(0, 4).map((c) => {
      const rec = asRecord(c)
      return text(rec.name) + '(' + Number(rec.count || 0) + ')'
    })
    out += '涉及集合：' + summary.join('、') + '。'
  }
  return out + '用户说"这个/这些"时指它们；对象名在 bpy.data.objects 里可直接用。'
}

/** chip 标题（一行，≤ 80 cell）：同一个 key 重发会**替换**上一条，所以标题只说「是什么、多少」 */
export function selectionChipTitle(sel: unknown): string {
  const s = asRecord(sel)
  const clip = (v: unknown, n = 24) => {
    const t = String(v == null ? '' : v)
    return t.length > n ? t.slice(0, n - 1) + '…' : t
  }
  if (s.kind === 'collection')
    return 'Blender 集合 · ' + clip(s.collection) + '（' + String(Number(s.count) || 0) + ' 件）'
  const sub = asRecord(s.sub)
  if (sub.type) return 'Blender 子元素 · ' + clip(sub.object)
  const names = asArray(s.names)
  const count = typeof s.count === 'number' ? s.count : names.length
  return count === 1 && names[0]
    ? 'Blender 选中 · ' + clip(names[0])
    : 'Blender 选中 · ' + String(count) + ' 件'
}

/**
 * 一次轮询（导出给自检）：取未消费的选中 → 挂 chip → consume。
 * 与 Web 桥同一套 CAS 语义：成功才 consume(ok:true)；挂不上不消费、下一 tick 重试，第 3 次失败
 * consume(ok:false, reason)；空选中直接 consume(ok:false)（没有可重试的东西）。
 */
export async function selectionTick(deps: {
  port: number
  chips: ChipService
  notify?: (text: string, color: string) => void
  state: SelectionState
}): Promise<void> {
  const { port, chips, notify, state } = deps
  const body = await getJson(port, '/host/selection/latest')
  if (!body || body.ok !== true || body.unconsumed !== true || !body.selection) return
  const seq = Number(body.seq) || 0
  if (seq <= state.lastSeq) return
  const selection = body.selection
  const txt = selectionMessageText({ selection: selection })
  const done = async (ok: boolean, extra: Record<string, unknown>) => {
    state.lastSeq = seq
    state.fails = 0
    bridge.lastSeq = seq
    bridge.lastOk = ok
    bridge.lastReason = ok ? null : text(extra.reason)
    bridge.lastAt = Date.now()
    await postJson(port, '/host/selection/consume', Object.assign({ seq: seq, ok: ok }, extra))
  }
  if (!txt) {
    await done(false, { reason: '没有对象名可插入（空选中）' })
    return
  }
  let staged: boolean
  try {
    staged =
      chips.attachContext({
        key: CHIP_KEY,
        title: selectionChipTitle(selection),
        content: txt,
      }) === true
  } catch {
    staged = false
  }
  if (staged) {
    await done(true, { how: 'tui-chip' })
    if (notify) notify('已接收 Blender 选中 · 挂到下一条消息（未发送）', 'success')
    return
  }
  state.fails = (state.fails || 0) + 1
  const reason = '输入框没挂上 Blender 选中（没有绑定的会话，或被宿主拒绝）'
  if (state.fails === 1 && notify) notify('Blender 选中没挂上：' + reason, 'warning')
  if (state.fails >= 3) await done(false, { reason: reason })
}
