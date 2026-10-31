/**
 * @dsh-external/dsh-blender-plugin —— dsh-TUI 宿主半边（v2.6.1，host 侧 / Node 进程内）。
 *
 * Web 的客户端半边（lib/client.js，'dsh.client.platform = "web"'）在 dsh-TUI 里不加载，
 * 所以 v2.3 的「Blender 原生选中 → 草稿」和 '@' 提及在 TUI 里一直是断的（契约 v2.3 增补 §S5.2）。
 * 这里用 dsh-TUI 的**宿主接缝**补上同一套体验（契约 v2.3 增补 §S6）：
 *   ① Blender 原生选中 → 输入框上下文 chip（ctx.tuiContextChips）+ toast（ctx.tuiToast）
 *   ② 页脚状态字段（ctx.tuiStatus.setField）：通道 / AI 暂停 / 渲染 / 租约 / 作业
 *   ③ '@' 提及 Blender 对象 / 材质 / 集合（ctx.tuiMentions）
 *   ④ '/blender status|doctor|jobs' 命令（ctx.commands 是 DSH 核心接缝，Web 也有）+ 子命令补全（ctx.tuiCommandTrees）
 *
 * 纪律：
 *   - 每块都按服务是否存在启用（ctx.inject 懒等）：Web / headless / 没有这些接缝的旧版 dsh-TUI 上什么都不做；
 *   - 只读后端（/host/state /host/selection/* /host/jobs 零 Blender 往返；'@' 提及与 '/blender doctor' 才碰 Blender）；
 *   - 任何失败都静默降级，绝不影响 15 个模型工具；
 *   - 选中**不自动发送**：chip 只在用户自己发下一条消息时随消息走，用户可以删掉。
 */

import { asArray, asRecord, isRecord, text } from './contracts/dynamic.js'

const SELECTION_POLL_MS = 100 // v2.6.1 性能实测：1200→100ms（契约 §S3.2）
const STATE_POLL_MS = 3000
const HTTP_TIMEOUT_MS = 1500
/** toast 队列的发送间隔（见 ① 的说明：toast 必须在 tuiToast 自己的 inject 上下文里调） */
const TOAST_DRAIN_MS = 250
/** 与 lib/client.js 同口径（tests/integration/js/tui-*.test.ts 对拍两边的草稿文本） */
const SELECTION_DRAFT_NAME_LIMIT = 24
const SUB_DRAFT_INDEX_LIMIT = 12
const CHIP_KEY = 'selection'
const FIELD_KEY = 'blender'
const MENTION_LIMIT = 50
/** '@' 提及的场景条目缓存：dsh-TUI 每次查询只给约 300 ms，而 /host/scene-items 要两次 bpy 往返，直接查经常超时 */
const SCENE_CACHE_MS = 5000
const SCENE_CACHE_LIMIT = 1000
const NL = String.fromCharCode(10)

type Json = Record<string, unknown>

export interface TuiHostOptions {
  port: number
  /** 本插件的写租约 holder（页脚区分「本会话持有 / 别的会话持有」） */
  holder: string
}

export interface MentionItem {
  label: string
  detail: string
  insertText: string
}

interface ChipService {
  attachContext(input: { key: string; title: string; content: string }): unknown
}

interface ToastService {
  show(text: string, opts: { color: string }): void
}

interface StatusService {
  setField(key: string, value: string, opts: Record<string, unknown>): unknown
}

interface MentionsService {
  register(def: {
    id: string
    title: string
    complete: (query: string, signal: AbortSignal) => Promise<MentionItem[]>
  }): unknown
}

interface CommandInvocation {
  rawInput?: string
  signal?: AbortSignal
}

interface CommandsService {
  register(def: {
    name: string
    description: string
    input: { hint: string }
    handler: (inv: CommandInvocation) => Promise<{ kind: string; text: string }>
  }): unknown
}

interface CommandTreeService {
  register(def: {
    root: string
    children: (path: readonly string[]) => readonly { name: string; description: string }[]
  }): unknown
}

interface TuiServices {
  tuiToast?: ToastService
  tuiContextChips?: ChipService
  tuiStatus?: StatusService
  tuiMentions?: MentionsService
  commands?: CommandsService
  tuiCommandTrees?: CommandTreeService
  effect(fn: () => void | (() => void), label: string): void
}

interface TuiContext {
  inject(names: string[], fn: (c: TuiServices) => void): void
}

interface SelectionState {
  lastSeq: number
  fails: number
}

/** doctor 用的接线现场：哪些接缝挂上了、选中桥最近一次结果 */
const wired = {
  chips: false,
  toast: false,
  status: false,
  mentions: false,
  commands: false,
  commandTree: false,
}
const bridge = {
  lastSeq: 0,
  lastOk: null as null | boolean,
  lastReason: null as null | string,
  lastAt: null as null | number,
}

// ──────────────────────────────────────────────────────────────────────────
// HTTP（永不抛：失败一律回 null）
// ──────────────────────────────────────────────────────────────────────────

function base(port: number): string {
  return 'http://127.0.0.1:' + String(port)
}
function withTimeout(ms: number, outer?: AbortSignal): AbortSignal {
  const own = AbortSignal.timeout(ms)
  return outer && typeof AbortSignal.any === 'function' ? AbortSignal.any([own, outer]) : own
}
async function getJson(
  port: number,
  path: string,
  signal?: AbortSignal,
  timeoutMs = HTTP_TIMEOUT_MS,
): Promise<Json | null> {
  try {
    const r = await fetch(base(port) + path, {
      cache: 'no-store',
      signal: withTimeout(timeoutMs, signal),
    })
    const body: unknown = await r.json()
    return isRecord(body) ? body : null
  } catch {
    return null
  }
}
async function postJson(port: number, path: string, body: unknown): Promise<Json | null> {
  try {
    const r = await fetch(base(port) + path, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(body),
      signal: withTimeout(HTTP_TIMEOUT_MS),
    })
    const parsed: unknown = await r.json()
    return isRecord(parsed) ? parsed : null
  } catch {
    return null
  }
}

// ──────────────────────────────────────────────────────────────────────────
// ① 选中 → 上下文 chip
// ──────────────────────────────────────────────────────────────────────────

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
 * 与 Web 桥同一套 CAS 语义（§S3.2）：成功才 consume(ok:true)；挂不上不消费、下一 tick 重试，第 3 次失败
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
  let staged = false
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

// ──────────────────────────────────────────────────────────────────────────
// ② 页脚状态字段
// ──────────────────────────────────────────────────────────────────────────

/** '/host/state' 响应 → 页脚一格（文本 / tone / 悬停说明）。纯函数，导出给自检。 */
export function footerField(
  state: unknown,
  holder: string,
  port: number,
): { text: string; tone: string; detail: string } {
  const st = asRecord(state)
  if (st.ok !== true) {
    return {
      text: 'Blender ○',
      tone: 'dim',
      detail:
        '后端未运行（127.0.0.1:' +
        String(port) +
        '）· 让模型跑 blender_viewport(op="start")，或 /blender doctor',
    }
  }
  const ch = asRecord(st.channel)
  const lease = asRecord(st.lease)
  const jobs = Number(asRecord(st.jobs).running || 0)
  const jobsTail = jobs > 0 ? ' · 作业 ' + String(jobs) : ''
  const jobsDetail = jobs > 0 ? ' · ' + String(jobs) + ' 个 headless 作业在跑（/blender jobs）' : ''
  if (ch.aiPaused) {
    return {
      text: 'Blender ⏸ AI 暂停',
      tone: 'warning',
      detail: 'Blender 里暂停了 AI 操作：N 面板「活动状态」点「继续 AI 操作」' + jobsDetail,
    }
  }
  const render = asRecord(st.render)
  if (render.busy) {
    const s = Math.round(Number(render.ageMs || 0) / 1000)
    return {
      text: 'Blender ● 渲染中' + jobsTail,
      tone: 'warning',
      detail: 'Blender 正在渲染（' + String(s) + ' s）· 主线程命令会秒回 busy' + jobsDetail,
    }
  }
  if (ch.diagnosis === 'main-thread-busy') {
    return {
      text: 'Blender ● 忙' + jobsTail,
      tone: 'warning',
      detail: 'Blender 主线程被渲染或模态操作占住 · 等它空下来，或改走无头' + jobsDetail,
    }
  }
  if (
    ch.lastOkAgeMs == null ||
    ch.diagnosis === 'blender-unreachable' ||
    ch.diagnosis === 'addon-thread-stuck'
  ) {
    return {
      text: 'Blender ○ 未连接' + jobsTail,
      tone: 'dim',
      detail:
        'GUI Blender 还没连上（addon 没 Connect 或 Blender 没起）· Blender 里按 N → Connect；无头路线不受影响' +
        jobsDetail,
    }
  }
  if (lease.active && lease.holder && lease.holder !== holder) {
    return {
      text: 'Blender ● 他人租约' + jobsTail,
      tone: 'warning',
      detail:
        '写租约在别的会话（' + String(lease.holder) + '）· 写操作会 409；只读不受影响' + jobsDetail,
    }
  }
  const mine = lease.active && lease.holder === holder ? ' · 本会话持有写租约' : ''
  return {
    text: 'Blender ●' + jobsTail,
    tone: 'success',
    detail: 'GUI 通道已连上' + mine + jobsDetail,
  }
}

// ──────────────────────────────────────────────────────────────────────────
// ③ @ 提及
// ──────────────────────────────────────────────────────────────────────────

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

// ──────────────────────────────────────────────────────────────────────────
// ④ /blender 命令
// ──────────────────────────────────────────────────────────────────────────

const SUBCOMMANDS = [
  { name: 'status', description: 'Blender 通道状态（零 Blender 往返）' },
  { name: 'doctor', description: '体检 GUI 通道（真跑一次 bpy 往返）' },
  { name: 'jobs', description: 'headless 作业列表' },
]

function ago(ms: unknown): string {
  if (ms == null) return '从未'
  const s = Math.round(Number(ms) / 1000)
  return s < 60 ? String(s) + ' s 前' : String(Math.round(s / 60)) + ' min 前'
}

/** '/blender <sub>' → 文本。导出给自检。 */
export async function blenderCommand(
  rawInput: string,
  opts: TuiHostOptions,
  signal?: AbortSignal,
): Promise<{ kind: string; text: string }> {
  const sub =
    String(rawInput || '')
      .trim()
      .split(/\s+/)[0] || 'status'
  const port = opts.port
  if (sub === 'status') {
    const st = await getJson(port, '/host/state', signal)
    const f = footerField(st, opts.holder, port)
    if (!st || st.ok !== true) return { kind: 'success', text: f.text + NL + f.detail }
    const ch = asRecord(st.channel)
    const lease = asRecord(st.lease)
    const lines = [
      f.text + ' — ' + f.detail,
      '通道：上次成功 ' +
        ago(ch.lastOkAgeMs) +
        ' · 进行中 ' +
        String(ch.inflight || 0) +
        ' · 上次命令 ' +
        (text(ch.lastCmd) || '-'),
      '租约：' +
        (lease.active
          ? text(lease.holder) + (lease.holder === opts.holder ? '（本会话）' : '')
          : '空闲'),
      '渲染：' +
        (asRecord(st.render).busy
          ? '进行中（' + ago(asRecord(st.render).ageMs).replace(' 前', '') + '）'
          : '无'),
      '作业：' + String(asRecord(st.jobs).running || 0) + ' 个在跑',
    ]
    if (ch.lastError)
      lines.push('最近错误（' + ago(ch.lastErrorAgeMs) + '）：' + text(ch.lastError))
    return { kind: 'success', text: lines.join(NL) }
  }
  if (sub === 'doctor') {
    const d = await getJson(port, '/doctor', signal, 30000)
    if (!d)
      return {
        kind: 'error',
        text:
          '体检失败：后端不可达（127.0.0.1:' +
          String(port) +
          '）· 让模型跑 blender_viewport(op="start")',
      }
    const a = asRecord(d.addon)
    const lines = ['诊断：' + (text(a.kind) || (d.ok ? 'ok' : 'unknown'))]
    if (a.summary) lines.push('结论：' + text(a.summary))
    if (a.fix) lines.push('修法：' + text(a.fix))
    return { kind: d.ok ? 'success' : 'error', text: lines.join(NL) }
  }
  if (sub === 'jobs') {
    const j = await getJson(port, '/host/jobs?limit=10', signal)
    if (!j || j.ok !== true)
      return { kind: 'error', text: '取作业列表失败：后端不可达（127.0.0.1:' + String(port) + '）' }
    const jobs = asArray(j.jobs)
    if (!jobs.length) return { kind: 'success', text: '没有 headless 作业' }
    const lines = [
      '作业 ' + String(j.running || 0) + ' 个在跑 / 共 ' + String(j.total || jobs.length) + ' 个：',
    ]
    for (const job of jobs) {
      const rec = asRecord(job)
      lines.push(
        '  ' +
          (text(rec.id) || '?') +
          ' · ' +
          (text(rec.status) || '?') +
          ' · ' +
          String(Math.round(Number(rec.ms || 0) / 1000)) +
          ' s' +
          (rec.script ? ' · ' + (text(rec.script).split(/[\\/]/).pop() ?? '') : ''),
      )
    }
    return { kind: 'success', text: lines.join(NL) }
  }
  return {
    kind: 'error',
    text: '未知子命令「' + sub + '」：可用 ' + SUBCOMMANDS.map((s) => s.name).join(' / '),
  }
}

// ──────────────────────────────────────────────────────────────────────────
// 接线
// ──────────────────────────────────────────────────────────────────────────

function every(c: TuiServices, ms: number, fn: () => Promise<void>, label: string): void {
  c.effect(() => {
    let busy = false
    const run = () => {
      if (busy) return
      busy = true
      fn()
        .catch(() => {})
        .finally(() => {
          busy = false
        })
    }
    const t = setInterval(run, ms)
    if (t && typeof t.unref === 'function') t.unref()
    run()
    return () => clearInterval(t)
  }, label)
}

/** apply() 调这个；宿主没有对应服务的块永远不会启用。 */
export function registerTuiHost(ctx: unknown, opts: TuiHostOptions): void {
  const host = ctx as TuiContext
  if (!host || typeof host.inject !== 'function') return
  const tag = '@dsh-external/dsh-blender-plugin: tui '

  // ① 选中 → chip（toast 可选：有就提示，没有也照常挂 chip）
  //    dsh-TUI 按**当前异步上下文所属的 activation** 认调用方：toast 只能在 tuiToast 自己的 inject 里调，
  //    所以选中桥只把提示排进队列，由这里的小定时器发出去（实测：跨 inject 直接调会被拒）。
  const toastQueue: Array<[string, string]> = []
  const notify = (txt: string, color: string) => {
    if (wired.toast && toastQueue.length < 5) toastQueue.push([txt, color])
  }
  host.inject(['tuiToast'], (c) => {
    const svc = c.tuiToast
    if (!svc || typeof svc.show !== 'function') return
    c.effect(() => {
      wired.toast = true
      const t = setInterval(() => {
        while (toastQueue.length) {
          const item = toastQueue.shift()
          if (!item) break
          const [txt, color] = item
          try {
            svc.show(txt, { color: color })
          } catch {
            /* toast 只是提示 */
          }
        }
      }, TOAST_DRAIN_MS)
      if (t && typeof t.unref === 'function') t.unref()
      return () => {
        clearInterval(t)
        toastQueue.length = 0
        wired.toast = false
      }
    }, tag + 'toast')
  })
  host.inject(['tuiContextChips'], (c) => {
    const chips = c.tuiContextChips
    if (!chips || typeof chips.attachContext !== 'function') return
    const state: SelectionState = { lastSeq: 0, fails: 0 }
    c.effect(() => {
      wired.chips = true
      return () => {
        wired.chips = false
      }
    }, tag + 'selection bridge flag')
    every(
      c,
      SELECTION_POLL_MS,
      () => selectionTick({ port: opts.port, chips: chips, notify: notify, state: state }),
      tag + 'selection bridge',
    )
  })

  // ② 页脚字段（tuiStatus.setField 是 dsh-TUI 较新的接缝：旧版只有 set()，这里不退化成整行状态）
  host.inject(['tuiStatus'], (c) => {
    const status = c.tuiStatus
    if (!status || typeof status.setField !== 'function') return
    wired.status = true
    let last = ''
    let clear: unknown
    let misses = 0
    every(
      c,
      STATE_POLL_MS,
      async () => {
        const st = await getJson(opts.port, '/host/state')
        // 偶发一次没取到（后端正忙 / 刚重启）不立刻改成「后端未运行」；连续两次才算
        misses = st ? 0 : misses + 1
        if (!st && misses < 2 && last) return
        const f = footerField(st, opts.holder, opts.port)
        const key = f.text + '|' + f.tone + '|' + f.detail
        if (key === last) return
        last = key
        // setField 每次都回一个只清「这一次写入」的 disposer：留最新那个，卸载时用它清
        clear = status.setField(FIELD_KEY, f.text, {
          slot: 'right-head',
          tone: f.tone,
          maxCells: 24,
          minColumns: 60,
          detail: f.detail,
        })
      },
      tag + 'footer field',
    )
    c.effect(
      () => () => {
        wired.status = false
        try {
          if (typeof clear === 'function') clear()
        } catch {
          /* 宿主随 activation 清理 */
        }
      },
      tag + 'footer field clear',
    )
  })

  // ③ @ 提及
  host.inject(['tuiMentions'], (c) => {
    const mentions = c.tuiMentions
    if (!mentions || typeof mentions.register !== 'function') return
    c.effect(() => {
      let dispose: unknown
      const complete = createSceneMentionSource(opts.port)
      // register 对非法 / 重复 / 超额直接抛（与 tuiCommandTrees 同口径）：注册不上就当没有这块
      try {
        dispose = mentions.register({ id: 'blender', title: 'Blender', complete: complete })
      } catch {
        dispose = undefined
      }
      wired.mentions = typeof dispose === 'function'
      return () => {
        wired.mentions = false
        if (typeof dispose === 'function') dispose()
      }
    }, tag + 'mentions')
  })

  // ④ /blender 命令（DSH 核心接缝；Web 也会有）+ dsh-TUI 子命令补全
  host.inject(['commands'], (c) => {
    const commands = c.commands
    if (!commands || typeof commands.register !== 'function') return
    c.effect(() => {
      wired.commands = true
      const dispose = commands.register({
        name: 'blender',
        description: 'Blender 通道：status / doctor / jobs',
        input: { hint: 'status | doctor | jobs' },
        handler: (inv) => blenderCommand(inv.rawInput ?? '', opts, inv.signal),
      })
      return () => {
        wired.commands = false
        if (typeof dispose === 'function') dispose()
      }
    }, tag + 'command')
  })
  host.inject(['tuiCommandTrees'], (c) => {
    const trees = c.tuiCommandTrees
    if (!trees || typeof trees.register !== 'function') return
    c.effect(() => {
      wired.commandTree = true
      let dispose: unknown
      try {
        dispose = trees.register({
          root: 'blender',
          children: (path: readonly string[]) => (path.length === 1 ? SUBCOMMANDS : []),
        })
      } catch {
        dispose = undefined
      }
      return () => {
        wired.commandTree = false
        if (typeof dispose === 'function') dispose()
      }
    }, tag + 'command tree')
  })
}

/** doctor 的一行：TUI 宿主半边接上了哪些接缝（非 dsh-TUI 宿主时说明为什么是空的） */
export function tuiHostSummary(): string {
  const on = (b: boolean) => (b ? '✅' : '—')
  if (!wired.chips && !wired.status && !wired.mentions && !wired.commandTree) {
    return (
      'TUI 宿主半边：未启用（不是 dsh-TUI 宿主，或 dsh-TUI 版本没有这些接缝；模型工具不受影响）' +
      (wired.commands ? ' · /blender 命令 ✅' : '')
    )
  }
  let s =
    'TUI 宿主半边：选中 chip ' +
    on(wired.chips) +
    ' · 页脚 ' +
    on(wired.status) +
    ' · @ 提及 ' +
    on(wired.mentions) +
    ' · /blender ' +
    on(wired.commands)
  if (bridge.lastAt)
    s +=
      ' · 最近一次选中 seq=' +
      String(bridge.lastSeq) +
      (bridge.lastOk ? ' 已挂上' : ' 未挂上（' + String(bridge.lastReason || '') + '）')
  return s
}

export const __tuiHostInternals = { wired: wired, bridge: bridge, SUBCOMMANDS: SUBCOMMANDS }
