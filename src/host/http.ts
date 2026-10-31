import { createHash } from 'node:crypto'
import { ABORT_HINT, HOLDER } from '../constants.js'
import { execSignal } from './exec-context.js'
import { base, withTimeout } from './backend.js'
import { asRecord, isRecord, text } from '../contracts/dynamic.js'

export interface FrameResult {
  png: Uint8Array
  ms: number
  hash: string
}

export interface ViewResult extends FrameResult {
  meta: Record<string, unknown> | null
}

export async function backendJson(
  port: number,
  path: string,
  timeoutMs = 20000,
  init?: RequestInit,
): Promise<Record<string, unknown>> {
  const r = await withTimeout(base(port) + path, init, timeoutMs)
  const body = await r.text()
  try {
    const parsed: unknown = JSON.parse(body)
    return isRecord(parsed) ? parsed : { value: parsed }
  } catch {
    return { ok: false, raw: body.slice(0, 500) }
  }
}

/**
 * 解析后端响应（v0.8.9 修）：后端有两种形态 ——
 *   ① 普通路由：整段就是一个 JSON；
 *   ② 流式路由（/headless /worker /txn /preset）：NDJSON，**每 15 s 先写一行** {"heartbeat":true,...}，最后一行才是结果。
 * 旧实现整段 JSON.parse，于是任何 **超过 15 s** 的调用都会抛 Extra data → 调用方只看到 exit=undefined（子进程其实跑完了）。
 * 现在：解析失败就按行往前找最后一条非心跳 JSON；同时把心跳条数记进 __heartbeats 供工具层提示。
 */
export function parseBackendBody(body: unknown): Record<string, unknown> {
  const t = String(body == null ? '' : body)
  try {
    const parsed: unknown = JSON.parse(t)
    if (isRecord(parsed)) return parsed
  } catch {
    /* 流式响应，往下走 */
  }
  const lines = t
    .split('\n')
    .map((s) => s.trim())
    .filter(Boolean)
  let last: Record<string, unknown> | null = null
  let beats = 0
  for (let i = lines.length - 1; i >= 0; i--) {
    let parsed: unknown = null
    try {
      parsed = JSON.parse(lines[i]) as unknown
    } catch {
      continue
    }
    if (!isRecord(parsed)) continue
    if (parsed.heartbeat === true) continue
    last = parsed
    break
  }
  for (let i = 0; i < lines.length; i++) {
    try {
      const parsed: unknown = JSON.parse(lines[i]) as unknown
      if (isRecord(parsed) && parsed.heartbeat === true) beats++
    } catch {
      /* 非 JSON 行 */
    }
  }
  if (last) {
    last.__heartbeats = beats
    last.__streamLines = lines.length
    return last
  }
  return {
    ok: false,
    raw: t.slice(-500),
    streamLines: lines.length,
    parseError: 'no JSON line found',
  }
}

/**
 * 写操作统一走它：自动带 holder（租约门禁用），并保留 HTTP 状态码便于识别 409。
 *
 * v0.9.3（D1，关键）：**计时器必须活到 body 读完**。
 * 旧版是 withTimeout(fetch) → fetch 在**收到响应头**时就 resolve（流式路由 /headless 立刻发头 + 每 15 s 心跳），
 * 于是超时预算只覆盖「等响应头」，随后的 r.text() 无限期等下去 —— 客户端等待窗口形同虚设，
 * 外层 run_code 的 deadline 一到就整段丢结果（正是 D1 报的现象）。
 * 现在把 text() 也放进同一个 AbortController：到点 abort → fetch / body 读取一起抛 AbortError。
 */
export async function backendPost(
  port: number,
  path: string,
  body: unknown,
  timeoutMs = 60000,
): Promise<Record<string, unknown>> {
  const ac = new AbortController()
  const ext = execSignal()
  let fired = false
  const timer = setTimeout(() => {
    fired = true
    ac.abort()
  }, timeoutMs)
  const signal = ext ? AbortSignal.any([ext, ac.signal]) : ac.signal
  try {
    const r = await fetch(base(port) + path, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ ...asRecord(body), holder: HOLDER }),
      signal,
    })
    const bodyText = await r.text()
    const parsed = parseBackendBody(bodyText)
    parsed.__status = r.status
    return parsed
  } catch (e) {
    if (!fired && ext && ext.aborted) throw new Error(ABORT_HINT)
    throw e
  } finally {
    clearTimeout(timer)
  }
}

/** v0.9.1：工具入参容错 —— harness 的 type:"json" 有时把对象给成**字符串**（实测同一会话里两种形态都出现过），
 *  旧写法 typeof x === 'object' 会静默丢参（表现为"参数没生效却 ok:true"，非常难查）。 */
export function jsonPayload(v: unknown): Record<string, unknown> {
  if (v === undefined || v === null || v === '') return {}
  if (typeof v === 'string') {
    try {
      const parsed: unknown = JSON.parse(v)
      return isRecord(parsed) ? parsed : {}
    } catch {
      return {}
    }
  }
  if (typeof v === 'object' && !Array.isArray(v)) return { ...asRecord(v) }
  return {}
}

/** 把 "‑‑flag \"a b\" c" 这样的字符串切成 argv（引号成对时不当分隔符；v0.8.9 修 headless 的 args 引号问题）
 *  v0.9.1：数组直接透传（旧版 String([...]) 会变成 "[delta]" 这种带方括号的单个 token） */
export function splitArgs(s: unknown): string[] {
  if (Array.isArray(s)) return s.map((x) => String(x))
  const str = String(s == null ? '' : s)
  const out: string[] = []
  let cur = ''
  let q: string | null = null
  for (let i = 0; i < str.length; i++) {
    const c = str[i]
    if (q) {
      if (c === q) q = null
      else cur += c
      continue
    }
    if (c === '"' || c === "'") {
      q = c
      continue
    }
    if (/\s/.test(c)) {
      if (cur) {
        out.push(cur)
        cur = ''
      }
      continue
    }
    cur += c
  }
  if (cur) out.push(cur)
  return out
}

/** 409 leased → 给模型一句能照着做的话（而不是丢一段 JSON） */
export function leasedText(r: unknown): string | undefined {
  const body = asRecord(r)
  if (body.error !== 'leased') return undefined
  return (
    '写通道被别的会话占用（holder=' +
    text(body.holder) +
    '，剩余 ' +
    String(Math.round(Number(body.expiresInMs || 0) / 1000)) +
    's）。' +
    text(body.hint) +
    '；要强抢：blender_viewport op=lease force=true' +
    '\ncode: lease_conflict' +
    '\nnext_action: 等租约释放后重试，或 blender_viewport op=lease force=true 强抢'
  )
}

/** 拉一帧 PNG 字节（含耗时）；full=true 走 screenshot_area（整窗口/区域） */
export async function fetchFrame(
  port: number,
  size: number,
  full = false,
  area = 0,
): Promise<FrameResult> {
  const t0 = Date.now()
  const url =
    base(port) + '/frame.png?' + (full ? 'full=1&area=' + String(area) : 'size=' + String(size))
  const r = await withTimeout(url, {}, 60000)
  if (!r.ok) throw new Error('frame http ' + String(r.status))
  const buf = new Uint8Array(await r.arrayBuffer())
  return {
    png: buf,
    ms: Date.now() - t0,
    hash: createHash('md5').update(buf).digest('hex').slice(0, 8),
  }
}

/** 自定义视角出图：POST /view（view.py）——不动物体、不动用户视口 */
export async function fetchView(
  port: number,
  spec: unknown,
  timeoutMs = 180000,
): Promise<ViewResult> {
  const t0 = Date.now()
  const r = await withTimeout(
    base(port) + '/view',
    {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(asRecord(spec)),
    },
    timeoutMs,
  )
  if (!r.ok) {
    const bodyText = await r.text()
    let parsed: Record<string, unknown> | null = null
    try {
      const candidate: unknown = JSON.parse(bodyText)
      parsed = isRecord(candidate) ? candidate : null
    } catch {
      parsed = null
    }
    throw new Error(
      'view http ' +
        String(r.status) +
        ' · ' +
        String(parsed ? parsed.error : bodyText.slice(0, 300)),
    )
  }
  let meta: Record<string, unknown> | null = null
  try {
    const rawHdr = String(r.headers.get('x-dsh-view') || 'null')
    // v0.9.1：头值 URL 编码（非 ASCII 不能进头）—— 老后端是裸 JSON，两种都兼容
    const txt = rawHdr.indexOf('%7B') === 0 ? decodeURIComponent(rawHdr) : rawHdr
    const candidate: unknown = JSON.parse(txt)
    meta = isRecord(candidate) ? candidate : null
  } catch {
    meta = null
  }
  const buf = new Uint8Array(await r.arrayBuffer())
  return {
    png: buf,
    ms: Date.now() - t0,
    meta: meta,
    hash: createHash('md5').update(buf).digest('hex').slice(0, 8),
  }
}

export function numTriple(v: unknown): number[] | undefined {
  if (v === undefined || v === null || v === '') return undefined
  if (Array.isArray(v)) return v.map((x) => Number(x))
  return String(v)
    .split(/[,\s]+/)
    .filter(Boolean)
    .map((x) => Number(x))
}
