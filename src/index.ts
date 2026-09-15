// @ts-nocheck
/**
 * @dsh-external/dsh-blender-plugin —— host 侧（10 个工具）。
 *
 * 唯一职责：**给模型低延迟的「看 / 做 / 连续看 / 重活」原语**
 * （后端 runtime/server.mjs 127.0.0.1:9877 → 直连 addon socket 127.0.0.1:9876）：
 *       blender_rt_see      取一帧视口 / 自定义视角（不动物体、不动用户视口）
 *       blender_rt_do       跑一段 Python + 立刻回一帧（整步 ~105ms）
 *       blender_rt_watch    指定时间窗内连续采样若干帧（看动画/交互行为）
 *       blender_rt_loop     内环：一次调用在 Blender 主线程跑几千次迭代
 *       blender_rt_cmd/commands  透传 addon 任意命令（30 条）
 *       blender_rt_perf/opt 渲染性能预设 / 对象精简
 *       blender_rt_headless 无头进程（blender -b）：重活不占 GUI 通道
 *       blender_viewport   后端运维：status / doctor / who / lease / release / start / stop / restart
 * 配合持久内核变量 K（addon 每次调用是新命名空间，K 存在 sys.modules 里 → REPL 语义）。
 *
 * 并发：写操作自动带租约 holder；别的会话持有时写路由 409（只读 op 豁免，force 可抢）。
 * 说明：人肉面板（网页 UI + Windows 输入注入）已按需求移除，不在此插件内。
 */
import { spawn } from 'node:child_process'
import { createHash } from 'node:crypto'
import { readdirSync, readFileSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { defineTool } from '@deepseek-ai/dsh-tools'
import z from 'schemastery'

export const name = '@dsh-external/dsh-blender-plugin'
export const inject = ['tools']

// 后端与 Blender 侧模块都放在插件包的 runtime/ 下 → 整个包可搬到任意目录（WSL 或 Windows）
const HERE = path.dirname(fileURLToPath(import.meta.url))
const SERVER_PATH = process.env.DSH_BLENDER_SERVER || path.join(HERE, '..', 'runtime', 'server.mjs')
const HOSTNAME = '127.0.0.1'

export interface Config {
  port: number
  autoStart: boolean
}

/**
 * 端口解析（与 runtime/config.mjs 同一套来源，避免两处配置漂移）：
 *   插件配置 port → DSH_BLENDER_HTTP_PORT → $DSH_BLENDER_CONFIG → <包根>/dsh-blender.config.json
 *   → ~/.dsh/dsh-blender.config.json → 9877
 */
function resolveHttpPort(): number {
  const env = Number(process.env.DSH_BLENDER_HTTP_PORT)
  if (Number.isFinite(env) && env > 0) return env
  const cands = [
    process.env.DSH_BLENDER_CONFIG,
    path.join(HERE, '..', 'dsh-blender.config.json'),
    path.join(os.homedir(), '.dsh', 'dsh-blender.config.json'),
  ].filter(Boolean) as string[]
  for (const p of cands) {
    try {
      const j = JSON.parse(readFileSync(p, 'utf8'))
      const n = Number(j && j.httpPort)
      if (Number.isFinite(n) && n > 0) return n
    } catch (e) { /* 没这个文件或不是 JSON：跳过 */ }
  }
  return 9877
}
/** 后端未就绪时的统一提示（不假设任何本机路径） */
const BACKEND_HINT = '先确认 Blender 在运行且 addon（MCP for Blender）已 Connect；然后 blender_viewport op=start 拉起后端。'
  + '端口/工作目录/blender.exe 可用 DSH_BLENDER_* 环境变量或包根 dsh-blender.config.json 配置（见 docs/配置参考.md）。'

export const Config = z.object({
  port: z.natural().default(resolveHttpPort()),
  autoStart: z.boolean().default(true),
})

let child: any = null
/** 用户用 op=stop 显式停过 → 看护不再自动拉起（op=start 解除） */
let paused = false

function base(port: number): string {
  return 'http://' + HOSTNAME + ':' + String(port)
}
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))

async function probe(port: number, timeoutMs = 1500): Promise<boolean> {
  const ac = new AbortController()
  const timer = setTimeout(() => ac.abort(), timeoutMs)
  try {
    const r = await fetch(base(port) + '/health', { signal: ac.signal })
    return r.ok
  } catch (e) {
    return false
  } finally {
    clearTimeout(timer)
  }
}

async function ensureBackend(port: number): Promise<boolean> {
  if (await probe(port)) return true
  if (paused) return false
  try { startBackend(port) } catch (e) { /* ignore */ }
  for (let i = 0; i < 25; i++) {
    if (await probe(port)) return true
    await sleep(300)
  }
  return false
}

/** 子进程是否真的还活着：被信号杀掉时 exitCode 仍为 null、只有 signalCode 有值 */
function childAlive(): boolean {
  return !!(child && child.exitCode === null && child.signalCode === null && child.pid)
}

function startBackend(port: number): string {
  if (childAlive()) return 'already-spawned'
  child = null
  child = spawn(process.execPath, [SERVER_PATH, '--port', String(port)], { stdio: 'ignore', detached: true })
  child.unref()
  return 'spawned pid=' + String(child.pid)
}

/**
 * 按 /proc 扫出"本插件历史代次拉起的后端"，SIGTERM 掉。
 * 热重载会丢掉 child 句柄（上一代 fiber 持有），只靠句柄停不掉残留进程 —— 这是兜底。
 */
function killBackendByCmd(port: number): string[] {
  const killed: string[] = []
  try {
    for (const d of readdirSync('/proc')) {
      if (!/^[0-9]+$/.test(d)) continue
      if (Number(d) === process.pid) continue
      try {
        const cmd = readFileSync('/proc/' + d + '/cmdline', 'utf8').replace(/\u0000/g, ' ')
        // 路径已随插件包改名（dsh-blender-plugin/runtime/server.mjs）→ 用更通用的匹配，否则停不掉残留
        if (cmd.includes('server.mjs') && cmd.includes('--port ' + String(port))) {
          process.kill(Number(d), 'SIGTERM')
          killed.push(d)
        }
      } catch (e) { /* 进程可能已退出 */ }
    }
  } catch (e) { /* /proc 不可读则不兜底 */ }
  return killed
}

async function withTimeout(path: string, init: any, timeoutMs: number): Promise<Response> {
  const ac = new AbortController()
  const timer = setTimeout(() => ac.abort(), timeoutMs)
  try {
    return await fetch(path, { ...(init || {}), signal: ac.signal })
  } finally {
    clearTimeout(timer)
  }
}

async function backendJson(port: number, path: string, timeoutMs = 20000, init?: any): Promise<any> {
  const r = await withTimeout(base(port) + path, init, timeoutMs)
  const text = await r.text()
  try { return JSON.parse(text) } catch (e) { return { ok: false, raw: text.slice(0, 500) } }
}

/** 本会话在写通道上的身份（租约 holder）；跨会话共用同一个后端时用它区分 */
const HOLDER = process.env.DSH_BLENDER_HOLDER || ('plugin-pid-' + String(process.pid))

/** 写操作统一走它：自动带 holder（租约门禁用），并保留 HTTP 状态码便于识别 409 */
async function backendPost(port: number, path: string, body: any, timeoutMs = 60000): Promise<any> {
  const r = await withTimeout(base(port) + path, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ ...(body || {}), holder: HOLDER }),
  }, timeoutMs)
  const text = await r.text()
  let j: any = null
  try { j = JSON.parse(text) } catch (e) { j = { ok: false, raw: text.slice(0, 500) } }
  j.__status = r.status
  return j
}

/** 409 leased → 给模型一句能照着做的话（而不是丢一段 JSON） */
function leasedText(r: any): string | undefined {
  if (!r || r.error !== 'leased') return undefined
  return '写通道被别的会话占用（holder=' + String(r.holder) + '，剩余 ' + String(Math.round(Number(r.expiresInMs || 0) / 1000)) + 's）。'
    + String(r.hint || '') + '；要强抢：blender_viewport op=lease force=true'
}

/** 拉一帧 PNG 字节（含耗时）；full=true 走 screenshot_area（整窗口/区域） */
async function fetchFrame(port: number, size: number, full = false, area = 0): Promise<any> {
  const t0 = Date.now()
  const url = base(port) + '/frame.png?' + (full ? ('full=1&area=' + String(area)) : ('size=' + String(size)))
  const r = await withTimeout(url, {}, 60000)
  if (!r.ok) throw new Error('frame http ' + String(r.status))
  const buf = new Uint8Array(await r.arrayBuffer())
  return { png: buf, ms: Date.now() - t0, hash: createHash('md5').update(buf).digest('hex').slice(0, 8) }
}

/** 自定义视角出图：POST /view（view.py）——不动物体、不动用户视口 */
async function fetchView(port: number, spec: any, timeoutMs = 180000): Promise<any> {
  const t0 = Date.now()
  const r = await withTimeout(base(port) + '/view', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(spec || {}),
  }, timeoutMs)
  if (!r.ok) {
    const text = await r.text()
    let j: any = null
    try { j = JSON.parse(text) } catch (e) { j = null }
    throw new Error('view http ' + String(r.status) + ' · ' + String((j && j.error) || text.slice(0, 300)))
  }
  let meta: any = null
  try { meta = JSON.parse(String(r.headers.get('x-dsh-view') || 'null')) } catch (e) { meta = null }
  const buf = new Uint8Array(await r.arrayBuffer())
  return { png: buf, ms: Date.now() - t0, meta: meta, hash: createHash('md5').update(buf).digest('hex').slice(0, 8) }
}

function numTriple(v: any): number[] | undefined {
  if (v === undefined || v === null || v === '') return undefined
  if (Array.isArray(v)) return v.map((x) => Number(x))
  return String(v).split(/[,\s]+/).filter(Boolean).map((x) => Number(x))
}

/** PNG → durable attachment（不可用时返回 undefined，工具回落纯文本） */
async function toAttachment(ctx: any, data: Uint8Array, name: string): Promise<any> {
  try {
    const attachments = ctx.get('attachments')
    if (!attachments) return undefined
    const mediaTypes: string[] = attachments.imageLimits?.mediaTypes ?? []
    if (!mediaTypes.includes('image/png')) return undefined
    const ref = await attachments.saveImage({ data, mediaType: 'image/png', name })
    return {
      attachmentId: ref.attachmentId,
      mediaType: ref.mediaType,
      bytes: ref.bytes,
      width: ref.width,
      height: ref.height,
      ...(ref.name === undefined ? {} : { name: ref.name }),
    }
  } catch (e) {
    return undefined
  }
}

function renderOne(_args: unknown, value: any): any[] {
  const blocks: any[] = [{ type: 'text', text: String((value && value.text) || '') }]
  if (value && value.image) blocks.push({ type: 'image', attachment: value.image })
  if (value && Array.isArray(value.images)) for (const im of value.images) blocks.push({ type: 'image', attachment: im })
  return blocks
}

const ANY_SCHEMA: any = { type: 'json' }
const HINT_KERNEL = '持久内核：K.x = 1 这次写，下次调用还能读到；预置 bpy / math / mathutils / Vector。'

export function apply(ctx: any, config: Config): void {
  const port = config.port

  void (async () => { if (config.autoStart && !paused) await ensureBackend(port) })()

  // 常驻看护：每 15s 探活一次，掉线自动拉起（宿主重启 / 进程被杀 / 端口被回收都能自愈）。
  // 显式 op=stop 会置 paused，看护尊重它，不会跟用户对着干。
  ctx.effect(() => {
    const wd = setInterval(() => {
      void (async () => {
        try {
          if (paused) return
          if (await probe(port)) {
            // 租约心跳：只在"本来就持有"时续期，空闲时不抢占别的会话
            try { await backendPost(port, '/lease', { renewOnly: true }, 5000) } catch (e) { /* 后端可能刚挂，下一轮再试 */ }
            return
          }
          startBackend(port)
        } catch (e) { /* 看护失败静默，下一轮再试 */ }
      })()
    }, 15000)
    return () => clearInterval(wd)
  }, '@dsh-external/dsh-blender-plugin: watchdog')

  ctx.effect(() => () => {
    if (child) { try { child.kill() } catch (e) {} child = null }
  }, '@dsh-external/dsh-blender-plugin: backend')

  // ---------- A. AI 实时交互原语 ----------

  ctx.effect(() => ctx.tools.register(defineTool({
    name: 'blender_rt_see',
    description: '实时看 Blender 视口：直接向 addon socket 取一帧离屏渲染并内联返回（约 55ms，比 CLI/MCP 通道快 20 倍）。用于「改一步、看一眼」的闭环。' + HINT_KERNEL,
    parameters: {
      max_size: { type: 'integer', description: '最长边像素，默认 560（420 更快，900 更清晰）' },
      full: { type: 'boolean', description: '整窗口/区域截图（screenshot_area 路径）：看 Blender UI 或其他编辑器时用；默认 false = 3D 视口离屏帧' },
      area: { type: 'integer', description: 'full=true 时的区域序号，默认 0（第 1 个区域）' },
      from: { type: 'string', description: '【自定义视角】相机位置 "x,y,z"（世界坐标）。给 from + look_at 就走自定义视角出图：套用自建矩阵离屏绘制，完全不动物体、不动用户视口 —— 想"从另一侧看看"或做多角度校验时用它（约 100ms）' },
      look_at: { type: 'string', description: '【自定义视角】看向的点 "x,y,z"' },
      lens: { type: 'number', description: '【自定义视角】焦距 mm，默认 50' },
      ortho: { type: 'boolean', description: '【自定义视角】正交投影（配合 ortho_scale）' },
      ortho_scale: { type: 'number', description: '【自定义视角】正交尺度（贴长边），默认 10' },
      view_size: { type: 'string', description: '【自定义视角】分辨率 "宽x高"，如 "1280x720"；默认按 max_size 出 16:9' },
      shading: { type: 'string', description: '【自定义视角】临时切换着色 WIREFRAME/SOLID/MATERIAL/RENDERED（出图后还原）' },
      overlays: { type: 'boolean', description: '【自定义视角】临时开关覆盖物（网格/坐标轴/gizmo），出图后还原' },
      view_mode: { type: 'string', description: '【自定义视角】viewport（默认，零场景改动，就是视口看到的样子）/ render（临时相机 + Workbench 快渲）' },
    },
    output: { schema: ANY_SCHEMA, render: renderOne },
    isConcurrencySafe: () => true,
    async execute(args: any) {
      if (!(await ensureBackend(port))) return { text: '后端不可用（127.0.0.1:' + String(port) + '）。' + BACKEND_HINT }
      const size = Math.max(120, Math.min(1600, Number((args && args.max_size) || 560)))
      const full = !!(args && args.full)
      const area = Number((args && args.area) || 0)
      const frm = numTriple(args && args.from)
      const look = numTriple(args && args.look_at)
      if (frm || look) {
        const spec: any = {
          from: frm || [7, -7, 5],
          look_at: look || [0, 0, 1],
          lens: args.lens === undefined ? undefined : Number(args.lens),
          ortho: args.ortho === undefined ? undefined : !!args.ortho,
          ortho_scale: args.ortho_scale === undefined ? undefined : Number(args.ortho_scale),
          shading: args.shading || undefined,
          overlays: args.overlays === undefined ? undefined : !!args.overlays,
          mode: args.view_mode || undefined,
        }
        const vs = String((args && args.view_size) || '')
        const mm = vs.match(/^(\d+)\s*[x×]\s*(\d+)$/)
        if (mm) { spec.width = Number(mm[1]); spec.height = Number(mm[2]) }
        else { spec.width = Math.round(size * 16 / 9); spec.height = size }
        const fv = await fetchView(port, spec)
        const img2 = await toAttachment(ctx, fv.png, 'view-' + String(Date.now()) + '.png')
        const mt = fv.meta || {}
        const out2: any = {
          text: '自定义视角 ' + String(mt.mode || spec.mode || 'viewport') + ' · ' + String(spec.width) + 'x' + String(spec.height)
            + ' · ' + String(fv.ms) + 'ms · ' + String(fv.png.length) + 'B · hash ' + fv.hash
            + ' · from ' + JSON.stringify(spec.from) + ' → look_at ' + JSON.stringify(spec.look_at)
            + (mt.fallback_from ? (' · (viewport 不可用已降级 render：' + String(mt.fallback_from) + ')') : '')
            + ' · 场景与用户视口均未改动',
        }
        if (img2) out2.image = img2
        return out2
      }
      const f = await fetchFrame(port, size, full, area)
      const img = await toAttachment(ctx, f.png, (full ? 'area-' : 'viewport-') + String(Date.now()) + '.png')
      const out: any = { text: (full ? '区域截图（screenshot_area）· ' : '视口帧 ' + String(size) + 'px · ') + String(f.ms) + 'ms · ' + String(f.png.length) + 'B · hash ' + f.hash }
      if (img) out.image = img
      return out
    },
  })), '@dsh-external/dsh-blender-plugin: rt-see')

  ctx.effect(() => ctx.tools.register(defineTool({
    name: 'blender_rt_do',
    description: '实时驱动 Blender：在 Blender 的 Python 里执行一段代码（或直接跑一个 .py 文件），可选立刻回一帧视口（整步约 105ms/次，可在同一轮里连续做几十步）。'
      + HINT_KERNEL
      + ' v0.7.0 起：① **异常也会回传 partial stdout / stderr / traceback**（不再只给一句 error，诊断信息不会丢）；'
      + ' ② file 参数可直接执行工作区脚本（等价 K.run(path)，支持 WSL / Windows / 相对 .blend 三种路径）；'
      + ' ③ 内置路径辅助 K.win_path / K.wsl_path / K.blend_path / K.out_dir（省掉手拼 UNC 与 chr(92)）；'
      + ' ④ 返回的 ms 即**主线程占用**，>1 s 会提示改走 blender_rt_headless / blender_rt_worker。',
    parameters: {
      code: { type: 'string', description: 'Python 代码（在 Blender 主线程执行；预置 bpy/math/mathutils/Vector 与持久内核 K）。与 file 二选一' },
      file: { type: 'string', description: '要执行的 .py 文件路径（WSL / Windows / 相对当前 .blend 均可）—— 长脚本用这个，不必塞进入参' },
      see: { type: 'boolean', description: '是否同时回一帧视口（默认 true）' },
      max_size: { type: 'integer', description: '回帧最长边像素，默认 560' },
    },
    output: { schema: ANY_SCHEMA, render: renderOne },
    isConcurrencySafe: () => false,
    async execute(args: any) {
      if (!(await ensureBackend(port))) return { text: '后端不可用（127.0.0.1:' + String(port) + '）。' + BACKEND_HINT }
      const code = String((args && args.code) || '')
      const file = (args && args.file) ? String(args.file) : null
      if (!code && !file) return { text: '需要 code 或 file 之一' }
      const body: any = { code: code }
      if (file) body.file = file
      const r = await backendPost(port, '/act', body, 180000)
      const lt = leasedText(r)
      if (lt) return { text: lt }
      const parts: string[] = []
      parts.push(r && r.ok ? 'ACT ok · ' + String(r.ms) + 'ms' : 'ACT 失败 · ' + String((r && (r.error || r.raw)) || 'unknown'))
      if (r && Number(r.mainThreadMs) > 1000) parts.push('⚠️ 本次占用 Blender 主线程约 ' + String(r.mainThreadMs) + 'ms —— 超过 ~1s 的重活建议改走 blender_rt_headless / blender_rt_worker（不卡 GUI）')
      if (r && r.stderr && String(r.stderr).trim()) parts.push('stderr: ' + String(r.stderr).trim().slice(0, 1200))
      if (r && r.traceback) parts.push("--- traceback ---" + String.fromCharCode(10) + String(r.traceback).slice(0, 2000))
      if (r && r.stdout) parts.push('stdout: ' + String(r.stdout).trim().slice(0, 3000))
      let image: any = undefined
      const see = !(args && args.see === false)
      if (see) {
        try {
          const size = Math.max(120, Math.min(1600, Number((args && args.max_size) || 560)))
          const f = await fetchFrame(port, size)
          image = await toAttachment(ctx, f.png, 'viewport-' + String(Date.now()) + '.png')
          parts.push('SEE ' + String(size) + 'px · ' + String(f.ms) + 'ms · hash ' + f.hash)
        } catch (e) {
          parts.push('SEE 失败: ' + String((e && e.message) || e))
        }
      }
      const out: any = { text: parts.join('\n') }
      if (image) out.image = image
      return out
    },
  })), '@dsh-external/dsh-blender-plugin: rt-do')

  ctx.effect(() => ctx.tools.register(defineTool({
    name: 'blender_rt_watch',
    description: '实时观察一段时间窗：可选先跑一段 Python（例如起 bpy.app.timers 驱动 / 播放动画 / 让物体动起来），然后在 seconds 秒内按 fps 连续采样视口帧，内联返回均匀抽取的最多 6 帧 + 逐帧 hash（hash 相同=画面没变）。用于判断「它到底动没动、动得对不对」。',
    parameters: {
      code: { type: 'string', description: '可选：观察前先执行的 Python' },
      seconds: { type: 'number', description: '观察时长，默认 2 秒（0.2-20）' },
      fps: { type: 'number', description: '采样率，默认 4（0.5-8）；注意每帧占用 Blender 主线程 ~55ms' },
      max_size: { type: 'integer', description: '帧最长边像素，默认 420' },
    },
    output: { schema: ANY_SCHEMA, render: renderOne },
    isConcurrencySafe: () => false,
    async execute(args: any) {
      if (!(await ensureBackend(port))) return { text: '后端不可用（127.0.0.1:' + String(port) + '）。' + BACKEND_HINT }
      const seconds = Math.max(0.2, Math.min(20, Number((args && args.seconds) || 2)))
      const fps = Math.max(0.5, Math.min(8, Number((args && args.fps) || 4)))
      const size = Math.max(120, Math.min(1600, Number((args && args.max_size) || 420)))
      const parts: string[] = []
      if (args && args.code) {
        const r = await backendPost(port, '/act', { code: String(args.code) }, 180000)
        const lt = leasedText(r)
        parts.push(lt ? lt : (r && r.ok ? 'ACT ok · ' + String(r.ms) + 'ms' + (r.stdout ? ' · stdout: ' + String(r.stdout).trim().slice(0, 600) : '') : 'ACT 失败 · ' + String((r && (r.error || r.raw)) || '')))
      }
      const total = Math.max(1, Math.min(32, Math.round(seconds * fps)))
      const interval = (seconds * 1000) / total
      const shots: any[] = []
      const t0 = Date.now()
      for (let i = 0; i < total; i++) {
        try {
          const f = await fetchFrame(port, size)
          shots.push({ t: Date.now() - t0, ms: f.ms, hash: f.hash, png: f.png })
        } catch (e) {
          shots.push({ t: Date.now() - t0, ms: -1, hash: 'ERR', png: null })
        }
        const wait = (i + 1) * interval - (Date.now() - t0)
        if (wait > 1) await sleep(wait)
      }
      const hashes = shots.map((s) => s.hash)
      const distinct = Array.from(new Set(hashes)).length
      const avgMs = Math.round(shots.reduce((a, s) => a + Math.max(0, s.ms), 0) / Math.max(1, shots.length))
      parts.push('WATCH ' + String(seconds) + 's @' + String(fps) + 'fps → ' + String(shots.length) + ' 帧 · 平均 ' + String(avgMs) + 'ms/帧 · 不同画面 ' + String(distinct) + '/' + String(hashes.length))
      parts.push('timeline: ' + shots.map((s, i) => String(i) + '@' + String(s.t) + 'ms:' + s.hash).join(' '))
      const picks: any[] = []
      const maxImages = 6
      if (shots.length <= maxImages) {
        for (const s of shots) if (s.png) picks.push(s)
      } else {
        for (let k = 0; k < maxImages; k++) {
          const idx = Math.round((k * (shots.length - 1)) / (maxImages - 1))
          if (shots[idx] && shots[idx].png) picks.push(shots[idx])
        }
      }
      const images: any[] = []
      for (let k = 0; k < picks.length; k++) {
        const att = await toAttachment(ctx, picks[k].png, 'watch-' + String(k) + '-' + picks[k].hash + '.png')
        if (att) images.push(att)
      }
      parts.push('附帧：' + String(images.length) + ' 张（均匀抽取）')
      const out: any = { text: parts.join('\n') }
      if (images.length) out.images = images
      return out
    },
  })), '@dsh-external/dsh-blender-plugin: rt-watch')

  ctx.effect(() => ctx.tools.register(defineTool({
    name: 'blender_rt_cmd',
    description: '直连调用 Blender addon 的任意命令（30 个名字：14 常驻 + 15 集成门控 + ping），含 MCP 层不暴露的 get_world_state_snapshot / drain_human_activity / get_telemetry_consent / set_telemetry_consent / get_addon_info，以及资产类命令（PolyHaven / Sketchfab / Poly Pizza / Hyper3D / Hunyuan3D）。参数必须匹配 addon 真实签名：get_scene_info 无参、get_object_info 用 name（不是 object_name）、不要传 MCP 才有的 user_prompt。先用 blender_rt_commands 看清单与可用性。',
    parameters: {
      name: { type: 'string', required: true, description: 'addon 命令名，如 get_world_state_snapshot、search_polyhaven_assets' },
      params: { type: 'json', description: '参数对象（可选），如 {"asset_type":"hdris"}' },
      timeout_ms: { type: 'integer', description: '超时毫秒，默认 120000（下载/生成类可调大）' },
    },
    output: { schema: ANY_SCHEMA, render: renderOne },
    isConcurrencySafe: () => false,
    async execute(args: any) {
      if (!(await ensureBackend(port))) return { text: '后端不可用（127.0.0.1:' + String(port) + '）。' + BACKEND_HINT }
      const name = String((args && args.name) || '')
      if (!name) return { text: 'blender_rt_cmd 需要 name 参数' }
      const r = await backendPost(port, '/cmd', { name: name, params: (args && args.params) || {}, timeoutMs: Number((args && args.timeout_ms) || 120000) }, 300000)
      const lt = leasedText(r)
      if (lt) return { text: lt }
      if (!r || r.ok !== true) return { text: 'CMD ' + name + ' 失败 · ' + String((r && (r.error || r.raw)) || 'unknown') }
      const payload = JSON.stringify(r.result)
      const head = 'CMD ' + name + ' ok · ' + String(r.ms) + 'ms · ' + String(payload.length) + ' chars'
      return { text: head + '\n' + (payload.length > 3000 ? payload.slice(0, 3000) + '\n…(已截断)' : payload) }
    },
  })), '@dsh-external/dsh-blender-plugin: rt-cmd')

  ctx.effect(() => ctx.tools.register(defineTool({
    name: 'blender_rt_commands',
    description: '列出直连通道当前可用的 addon 命令（14 常驻 + 15 集成门控 + ping）与 5 个集成的真实状态（开关是否打开、是否缺 API key）。调 blender_rt_cmd 之前先用它。',
    parameters: {},
    output: { schema: ANY_SCHEMA, render: renderOne },
    isConcurrencySafe: () => true,
    async execute() {
      if (!(await ensureBackend(port))) return { text: '后端不可用（127.0.0.1:' + String(port) + '）。' + BACKEND_HINT }
      const r = await backendJson(port, '/commands', 120000)
      if (!r || r.ok !== true) return { text: 'COMMANDS 失败 · ' + String((r && (r.error || r.raw)) || 'unknown') }
      const lines: string[] = []
      lines.push('场景 ' + String(r.scene) + (r.file ? ' · 文件 ' + String(r.file) : ' · 未保存文件'))
      lines.push('可用命令 ' + String(r.total) + ' 条；被集成开关挡住 ' + String((r.disabled || []).length) + ' 条')
      lines.push('集成：' + Object.keys(r.integrations || {}).map((k: string) => k + '=' + (r.integrations[k].enabled ? 'on' : 'off')).join(' · '))
      for (const k of Object.keys(r.integrations || {})) {
        const it = r.integrations[k]
        if (!it.enabled && it.message) lines.push('  [' + k + '] ' + it.message)
      }
      if ((r.disabled || []).length) lines.push('被挡住：' + r.disabled.join(', '))
      lines.push('可用：' + (r.available || []).join(', '))
      return { text: lines.join('\n') }
    },
  })), '@dsh-external/dsh-blender-plugin: rt-commands')

  ctx.effect(() => ctx.tools.register(defineTool({
    name: 'blender_rt_loop',
    description: '【AI 建模内环】在 Blender 侧跑高频迭代（bpy.app.timers，主线程安全，迭代/时间双上限 + 急停）：模型只写目标与验收，机器跑几千次迭代。spec={setup, step, measure, iterations, budget_ms, interval, measure_every, minimize, top_k, group_key, redraw_every}；setup 只跑一次，step/measure 共享命名空间 ns（预置 bpy/K/math/random/np/i/frac/penalize/anneal/record），measure 必须给 ns["score"]，参数写 ns["params"]，可选 ns["metrics"]/ns["violations"]。辅助：penalize(errors, violations, weights, lam) 做多目标+罚项；anneal(v0,v1,frac) 退火步长（step 里读 ns["i"]/ns["frac"]）；record(...) 手动登记候选；top_k 保留候选表，group_key（如 "obj"）按对象分组各留最优（跨对象批量）。op=help 出契约速查；op=board 取候选表；op=export 把 best 导出成可复用脚本（内嵌 setup 源码，可直接再跑）。⚠️ 内环只优化你写的目标函数：收敛后必须换**另一条**计算通路复核 + blender_rt_see 视觉确认（防 Goodhart）。',
    parameters: {
      op: { type: 'string', required: true, description: 'start | status | stop | board | export | help | bench' },
      spec: { type: 'json', description: 'op=start 的规格：{setup, step, measure, iterations, budget_ms, interval, measure_every, minimize, top_k, group_key, redraw_every}' },
      history: { type: 'integer', description: 'op=status 返回的指标尾迹长度，默认 8' },
      board: { type: 'integer', description: 'op=status 时同时返回前 N 个候选（默认 0=不返回）' },
      limit: { type: 'integer', description: 'op=board 的候选条数，默认 10' },
      groups: { type: 'boolean', description: 'op=board 是否包含分组候选（默认 true）' },
      path: { type: 'string', description: 'op=export 写出脚本的路径（宿主可见路径，如 /tmp/xxx.py 或 D:\\out\\xxx.py），省略=只回文本' },
      top: { type: 'integer', description: 'op=export 的变体数量（配 include_variants）' },
      include_variants: { type: 'boolean', description: 'op=export 是否把候选表一起导出' },
      note: { type: 'string', description: 'op=export 的备注（写进脚本头部）' },
      iterations: { type: 'integer', description: 'op=bench 的迭代次数' },
    },
    output: { schema: ANY_SCHEMA, render: renderOne },
    isConcurrencySafe: () => false,
    async execute(args: any) {
      if (!(await ensureBackend(port))) return { text: '后端不可用（127.0.0.1:' + String(port) + '）。' + BACKEND_HINT }
      const op = String((args && args.op) || 'status')
      const payload: any = {
        op: op,
        spec: (args && args.spec) || {},
        history: (args && args.history) || 8,
        board: (args && args.board) || 0,
        limit: (args && args.limit) || 10,
        groups: !(args && args.groups === false),
        path: args && args.path,
        top: (args && args.top) || 1,
        include_variants: !!(args && args.include_variants),
        note: (args && args.note) || '',
        iterations: (args && args.iterations) || 5000,
      }
      const r = await backendPost(port, '/loop', payload, 300000)
      const lt = leasedText(r)
      if (lt) return { text: lt }
      if (!r || r.ok !== true) return { text: 'LOOP ' + op + ' 失败 · ' + String((r && (r.error || r.raw)) || 'unknown') }
      return { text: 'LOOP ' + op + ' ' + JSON.stringify(r.result) }
    },
  })), '@dsh-external/dsh-blender-plugin: rt-loop')

  ctx.effect(() => ctx.tools.register(defineTool({
    name: 'blender_rt_perf',
    description: '【渲染性能】Cycles CPU 瓶颈诊断与优化预设。op=analyze 差分实测「每轮同步 / 每采样 GPU 成本」（占主线程十几秒）；op=apply 应用预设（persistent_data / OptiX 降噪 / denoising_use_gpu / auto_tile off / 采样上限）；op=revert 还原 apply 之前；op=status 看当前设置与已存快照；op=help 契约。实测背景：2318 对象场景每轮 CPU 侧同步 ≈4.2 s、GPU 单采样 ≈0.2 s（1080p）、首次 kernel JIT ≈15 s；persistent_data 让重复渲染 14.6 s → 0.79 s。',
    parameters: {
      op: { type: 'string', required: true, description: 'status | analyze | apply | revert | help' },
      args: { type: 'json', description: 'analyze{pct=25,low=1,high=4}；apply{samples=1024,persistent=true,denoiser="OPTIX",denoise_gpu=true,auto_tile=false}' },
    },
    output: { schema: ANY_SCHEMA, render: renderOne },
    isConcurrencySafe: () => false,
    async execute(args: any) {
      if (!(await ensureBackend(port))) return { text: '后端不可用（127.0.0.1:' + String(port) + '）。' + BACKEND_HINT }
      const op = String((args && args.op) || 'status')
      const r = await backendPost(port, '/perf', { op: op, args: (args && args.args) || undefined }, 600000)
      const lt = leasedText(r)
      if (lt) return { text: lt }
      if (!r || r.ok !== true) return { text: 'PERF ' + op + ' 失败 · ' + String((r && (r.error || r.raw)) || 'unknown') }
      return { text: 'PERF ' + op + ' ' + JSON.stringify(r.result) }
    },
  })), '@dsh-external/dsh-blender-plugin: rt-perf')

  ctx.effect(() => ctx.tools.register(defineTool({
    name: 'blender_rt_opt',
    description: '【对象精简】降 Cycles 每轮场景同步成本（实测 ≈1.4 ms/对象）。op=analyze 列出可安全合并的分组与预计节省；op=join 执行合并（**默认 dry_run=true 只报告**；dry_run=false 才真合并，强烈建议同时给 save_before=<.blend 绝对路径> 先存回退点）。合并规则：同集合 / 同材质 / 同父级 / 无修改器 / 无动画 / 无形态键 / 无自定义属性 / 无实例 / 非库链接。合并后**几何零损失**（面数与顶点数不变），合并对象名为 `<前缀>_MERGED_<材质>`。',
    parameters: {
      op: { type: 'string', required: true, description: 'analyze | join | help' },
      args: { type: 'json', description: 'join{dry_run=true, save_before="D:/.../xxx_before_join.blend"}' },
    },
    output: { schema: ANY_SCHEMA, render: renderOne },
    isConcurrencySafe: () => false,
    async execute(args: any) {
      if (!(await ensureBackend(port))) return { text: '后端不可用（127.0.0.1:' + String(port) + '）。' + BACKEND_HINT }
      const op = String((args && args.op) || 'analyze')
      const r = await backendPost(port, '/opt', { op: op === 'join' ? 'opt_join' : 'opt_analyze', args: (args && args.args) || undefined }, 600000)
      const lt = leasedText(r)
      if (lt) return { text: lt }
      if (!r || r.ok !== true) return { text: 'OPT ' + op + ' 失败 · ' + String((r && (r.error || r.raw)) || 'unknown') }
      return { text: 'OPT ' + op + ' ' + JSON.stringify(r.result) }
    },
  })), '@dsh-external/dsh-blender-plugin: rt-opt')

  ctx.effect(() => ctx.tools.register(defineTool({
    name: 'blender_rt_headless',
    description: '【无头 Blender】独立进程跑脚本（blender.exe -b）：不占 GUI 通道、不动你正看着的场景，适合重渲染 / 批量几何 / 数据校验 / 长任务。'
      + '脚本里 print("HEADLESS {...}") 会被解析成 result（**必须是单行 JSON**：json.dumps(obj, separators=(",",":"))）。'
      + '默认 --factory-startup（干净、快、不会去抢 9876 端口）；要用用户的启动文件与偏好时设 factory_startup=false + use_user_config=true。'
      + '引擎语义（v0.8.0，默认 **EEVEE + 光追**）：默认注入引擎前导（BLENDER_EEVEE + use_raytracing + SCREEN 光追 + 阴影质量），并回传 engine/gpu 字段（实测 360 对象工程 EEVEE+RT 预热帧 1.35 s vs Cycles GPU 3.13 s = 2.3×）；'
      + '可选 engine="cycles"（OptiX 设备前导，含"静默回落 CPU 15.4×"防护）/ engine="keep"（不动设置）。首帧有 EEVEE 着色器编译成本（冷缓存 ~16 s）→ 迭代请配 blender_rt_worker 热会话；'
      + '② 客户端超时/断连**不等于任务失败**：服务端子进程会继续跑完，产物仍落在 outdir（日志路径在 logs 字段里）。超 5 分钟的长任务请用 blender_rt_worker（热会话）或把结果写文件。'
      + '返回：result / gpu / logs（全量日志路径）/ lastException / traceback / 过滤后的产物清单。',
    parameters: {
      script: { type: 'string', description: 'Python 源码（默认已注入持久内核 K；预置 bpy/math/mathutils/Vector）。不给脚本则只起 Blender（可用于 --version 类探测）' },
      file: { type: 'string', description: '要打开的 .blend（Windows 路径或 WSL 路径都可，自动转换）' },
      outdir: { type: 'string', description: '产物目录（Windows 路径，如 D:\\work\\out；也是默认工作目录）；跑完列出其中新文件，并把该路径追加到脚本的 sys.argv' },
      args: { type: 'string', description: '额外命令行参数（空格分隔），追加在 -- 之后，脚本里从 sys.argv 读' },
      timeout_ms: { type: 'integer', description: '超时毫秒，默认 180000（3 min），上限 1800000（30 min）；超时 SIGKILL 掉整个进程' },
      factory_startup: { type: 'boolean', description: '默认 true = --factory-startup；false 用用户启动文件与插件（注意：其 startup 里的本插件会尝试占 9876 端口，通常无害但有报错噪音）' },
      bootstrap: { type: 'boolean', description: '默认 true = 注入持久内核 K（与 blender_rt_do 一致）；false 时脚本原样跑' },
      preload: { type: 'string', description: '预载 runtime 里的 python 模块（逗号分隔，如 "view,perf,contract,planner"）：源码拼到脚本开头，之后可用 K.dsh_view_api / K.dsh_perf_api 等' },
      engine: { type: 'string', description: '渲染引擎：eevee（默认 = EEVEE + 光追，纯 GPU、不依赖设备偏好）/ cycles（OptiX 设备前导）/ keep（保持现状）；gpu:"false" 等价于 engine:"keep"' },
      gpu: { type: 'string', description: '（仅 cycles 路径的设备语义）auto / true（必须有 GPU，否则 ok=false）/ false' },
      use_user_config: { type: 'boolean', description: '透传 BLENDER_USER_CONFIG / BLENDER_USER_SCRIPTS 给无头进程（默认 false）—— 想让无头进程继承你的偏好/插件时打开（通常配合 factory_startup=false）' },
      include_noise: { type: 'boolean', description: '产物清单是否包含噪音文件（__pycache__ / *.pyc / *.blend1|2 / tmp*）；默认 false = 过滤掉' },
    },
    output: { schema: ANY_SCHEMA, render: renderOne },
    isConcurrencySafe: () => false,
    async execute(args: any) {
      if (!(await ensureBackend(port))) return { text: '后端不可用（127.0.0.1:' + String(port) + '）；无头通道由后端进程代管，先 blender_viewport op=start' }
      const body: any = {
        script: String((args && args.script) || ''),
        file: (args && args.file) || undefined,
        outdir: (args && args.outdir) || undefined,
        timeoutMs: (args && args.timeout_ms) ? Number(args.timeout_ms) : undefined,
        factoryStartup: !(args && args.factory_startup === false),
        bootstrap: !(args && args.bootstrap === false),
        preload: (args && args.preload) || undefined,
        gpu: (args && args.gpu) || undefined,
        engine: (args && args.engine) || undefined,
        useUserConfig: !!(args && args.use_user_config),
        includeNoise: !!(args && args.include_noise),
      }
      if (args && args.args) body.args = String(args.args).split(/\s+/).filter(Boolean)
      const budget = 60000 + Number(body.timeoutMs || 180000)
      const r = await backendPost(port, '/headless', body, budget)
      const lt = leasedText(r)
      if (lt) return { text: lt }
      if (!r || r.error) return { text: 'HEADLESS 失败 · ' + String((r && (r.error || r.raw)) || 'unknown') + (r && r.hint ? ('\n' + String(r.hint)) : '') }
      const res = (r && r.result) || {}
      const parts: string[] = []
      parts.push((res.ok ? 'HEADLESS ok' : 'HEADLESS 失败') + ' · exit=' + String(res.exitCode) + (res.timedOut ? ' · 超时被杀' : '') + ' · ' + String(res.ms) + 'ms'
        + (res.reason ? ' · ' + String(res.reason) : ''))
      const engName = res.engine || (res.gpu && res.gpu.after && res.gpu.after.engine) || null
      if (engName) {
        const rt = res.gpu && res.gpu.after ? res.gpu.after.rt : null
        parts.push('引擎：' + String(engName) + (rt === true ? ' + 光追' : '') + (res.engineMode ? ('（mode=' + String(res.engineMode) + '）') : ''))
      }
      if (res.gpu) {
        const g = res.gpu
        parts.push('GPU：' + (g.fell_back_to_cpu ? ('⚠️ 回落 CPU（' + String(g.error || (g.before && g.before.device_type) || '') + '）')
          : ('✅ ' + String((g.after && g.after.device_type) || g.configured || '') + ' · ' + String(((g.after && g.after.gpu_enabled) || []).join(','))))
          + (g.configured ? ' · 本次已自动配置 ' + String(g.configured) : ''))
      }
      if (res.script) parts.push('脚本：' + String(res.script))
      if (res.lastException) parts.push('最后异常：' + String(res.lastException))
      if (res.logs) parts.push('日志：' + String(res.logs.stdout) + ' · ' + String(res.logs.stderr))
      if (res.result) parts.push('result: ' + JSON.stringify(res.result).slice(0, 2000))
      if (res.stdout) parts.push('--- stdout 尾部 ---\n' + String(res.stdout).slice(-2500))
      if (res.stderr && String(res.stderr).trim()) parts.push('--- stderr 尾部 ---\n' + String(res.stderr).slice(-1500))
      if (res.artifacts && res.artifacts.length) parts.push('产物：' + res.artifacts.slice(0, 12).map((a: any) => a.name + '(' + a.bytes + 'B' + (a.fresh ? ' 新' : '') + ')').join(' · '))
      if (res.noiseFiltered) parts.push('（已过滤 ' + String(res.noiseFiltered) + ' 个噪音产物：__pycache__/*.pyc/*.blend1|2/tmp*）')
      if (res.hint) parts.push('提示：' + String(res.hint))
      if (res.traceback) parts.push('--- traceback ---' + String.fromCharCode(10) + String(res.traceback).slice(0, 1200))
      return { text: parts.join('\n') }
    },
  })), '@dsh-external/dsh-blender-plugin: rt-headless')

  ctx.effect(() => ctx.tools.register(defineTool({
    name: 'blender_rt_plan',
    description: '【契约层 + 规划器】把「假设 / 区间 / 校验 / 证据 / 门控」与「对象图编译」变成可调用 API。'
      + '契约 op：status · help · reset · register_component · register_connection · register_envelope · '
      + 'check_envelope · check_interference · check_interface · destructive_guard · evidence · ledger · report · '
      + 'verify · flip · advance；规划器 op（plan_ 前缀）：plan_load · plan_validate · plan_order · plan_build · '
      + 'plan_graph · plan_status · plan_help。判据与流程见 docs/假设驱动建模-cookbook.md（外部证据不足必须报 unresolved；'
      + '未判别的连接上做 boolean/weld/merge 会被 destructive_guard 拦下）。'
      + '**QC 也在这里**（v0.7.0，前缀 qc_）：qc_compare（参考图 vs 渲染：IoU/Dice/缺面积/多面积/边界距离/剖面差 + 叠加图与三联对照图）、'
      + 'qc_compare_basic（对齐逻辑照搬 plush-build 脚本，用于与历史数字对照）、qc_self_check（合成自检）、qc_robustness_check（平移/缩放鲁棒性）、qc_help。',
    parameters: {
      op: { type: 'string', required: true, description: '契约 op 或 plan_<op>（见工具描述；op=help / plan_help 出速查）' },
      args: { type: 'json', description: 'op 的参数对象，例如 {"cid":"joint","err":184,"tolerance":220,"identifiable":["dy"]}' },
      path: { type: 'string', description: 'op=report 时的输出路径（Markdown）；省略则只回文本' },
    },
    output: { schema: ANY_SCHEMA, render: renderOne },
    isConcurrencySafe: () => false,
    async execute(argsIn: any) {
      if (!(await ensureBackend(port))) return { text: '后端不可用（127.0.0.1:' + String(port) + '）。' + BACKEND_HINT }
      const op = String((argsIn && argsIn.op) || 'status')
      const payload: any = (argsIn && argsIn.args && typeof argsIn.args === 'object') ? { ...argsIn.args } : {}
      if (op === 'report' && argsIn && argsIn.path) payload.path = String(argsIn.path)
      const r = await backendPost(port, '/plan', { op: op, args: payload }, 300000)
      const lt = leasedText(r)
      if (lt) return { text: lt }
      if (!r || r.ok !== true) return { text: 'PLAN ' + op + ' 失败 · ' + String((r && (r.error || r.raw)) || 'unknown') }
      const res = r.result
      const txt = typeof res === 'string' ? res : JSON.stringify(res, null, 1)
      return { text: 'PLAN ' + op + ' ok · ' + String(txt.length) + ' chars\n' + (txt.length > 4000 ? txt.slice(0, 4000) + '\n…(已截断)' : txt) }
    },
  })), '@dsh-external/dsh-blender-plugin: rt-plan')

  ctx.effect(() => ctx.tools.register(defineTool({
    name: 'blender_rt_worker',
    description: '【热无头会话】常驻的 blender -b 进程：start 拉起 / exec 跑代码片段（复用**持久内核 K 与同一个 Blender 会话**）/ status / stop / restart。'
      + '适合"反复迭代"的工作流 —— 省掉每次冷启动（0.9–1.2 s）与重复导入/重建的成本；无头侧也能用 K.dsh_view_api（exec 里 exec(open(<包路径>/runtime/view.py, encoding="utf-8").read()) 加载即可）。'
      + '语义与限制：① **串行** —— 一次只处理一个请求，长代码会占住 worker（可 op=status 看状态）；'
      + '② 无窗口 —— 依赖 GUI 上下文的 bpy.ops 可能失败（用 temp_override 或改用 GUI 通道）；'
      + '③ print("HEADLESS {json}")（单行）会作为 result 回传；异常带 traceback 返回；'
      + '④ 与 GUI 通道**互不干扰**（独立进程、不占 9876/9877）。',
    parameters: {
      op: { type: 'string', required: true, description: 'start | exec | status | stop | restart' },
      code: { type: 'string', description: 'op=exec 时的 Python 源码（预置 K/bpy/math/mathutils/Vector）' },
      timeout_ms: { type: 'integer', description: 'op=exec 的响应超时，默认 120000；长代码请调大' },
      gpu: { type: 'string', description: 'op=start 时的 GPU 语义（仅 cycles 路径）：auto（默认）/ true / false' },
      engine: { type: 'string', description: 'op=start 时的渲染引擎：eevee（默认 = EEVEE + 光追）/ cycles / keep；热会话让 EEVEE 着色器编译只付一次' },
    },
    output: { schema: ANY_SCHEMA, render: renderOne },
    isConcurrencySafe: () => false,
    async execute(args: any) {
      if (!(await ensureBackend(port))) return { text: '后端不可用（127.0.0.1:' + String(port) + '）。' + BACKEND_HINT }
      const op = String((args && args.op) || 'status')
      const body: any = { op: op }
      if (args && args.code !== undefined) body.code = String(args.code)
      if (args && args.timeout_ms) body.timeoutMs = Number(args.timeout_ms)
      if (args && args.gpu) body.gpu = String(args.gpu)
      if (args && args.engine) body.engine = String(args.engine)
      const budget = 60000 + Number(body.timeoutMs || 120000)
      const r = await backendPost(port, '/worker', body, budget)
      const lt = leasedText(r)
      if (lt) return { text: lt }
      if (!r || r.ok !== true) {
        const rr: any = (r && r.result) || {}
        const bits: string[] = []
        bits.push('WORKER ' + op + ' 失败 · ' + String((r && (r.error || rr.error)) || 'unknown'))
        if (rr.traceback) bits.push('--- traceback ---' + String.fromCharCode(10) + String(rr.traceback).slice(0, 2000))
        if (rr.stderr && String(rr.stderr).trim()) bits.push('stderr: ' + String(rr.stderr).trim().slice(0, 1200))
        if (rr.stdout && String(rr.stdout).trim()) bits.push('stdout: ' + String(rr.stdout).trim().slice(0, 800))
        if (r && r.hint) bits.push(String(r.hint))
        return { text: bits.join(String.fromCharCode(10)) }
      }
      const res = (r && r.result) || {}
      if (op === 'exec') {
        const parts: string[] = []
        parts.push((res.ok ? 'WORKER exec ok' : 'WORKER exec 失败') + ' · ' + String(res.ms) + 'ms')
        if (res.result) parts.push('result: ' + JSON.stringify(res.result).slice(0, 2000))
        if (res.error) parts.push('error: ' + String(res.error))
        if (res.stdout && String(res.stdout).trim()) parts.push('--- stdout ---\n' + String(res.stdout).slice(-2500))
        if (res.stderr && String(res.stderr).trim()) parts.push('--- stderr ---\n' + String(res.stderr).slice(-1200))
        if (res.traceback) parts.push('--- traceback ---' + String.fromCharCode(10) + String(res.traceback).slice(0, 1500))
        return { text: parts.join('\n') }
      }
      const parts2: string[] = []
      parts2.push('WORKER ' + op + ' ok · ' + JSON.stringify({ alive: res.alive, ready: res.ready, pid: res.pid, port: res.port, uptimeMs: res.uptimeMs }))
      if (res.gpu) parts2.push('GPU：' + (res.gpu.fell_back_to_cpu ? '⚠️ 回落 CPU' : ('✅ ' + String((res.gpu.after || {}).device_type || ''))) + ' ' + String(((res.gpu.after || {}).gpu_enabled || []).join(',')))
      if (res.status) parts2.push('status: ' + JSON.stringify(res.status))
      if (res.lastError) parts2.push('lastError: ' + String(res.lastError))
      if (res.stdoutTail) parts2.push('worker stdout 尾：' + String(res.stdoutTail).slice(-800))
      return { text: parts2.join('\n') }
    },
  })), '@dsh-external/dsh-blender-plugin: rt-worker')

  ctx.effect(() => ctx.tools.register(defineTool({
    name: 'blender_rt_txn',
    description: '【事务 / 回滚】两级快照：**文件级** snapshot/restore（整场景回退；写 .blend 副本，用 copy=True 所以不改当前 filepath；大场景有 MB 级成本，本机 300 对象工程实测约 97MB / 0.8s）+ **对象级** mark/revert（只记 transform / 材质槽 / 可见性 / 修改器开关，毫秒级、就地回滚）。'
      + '用途："整个场景推倒重来之前先留个点"（snapshot）、"只改一处再对比"（mark 再 revert）。'
      + '⚠️ 边界：对象级**不含拓扑/UV/顶点级改动** —— Boolean、合并、删面之后回不去（revert 会跳过并报告），那种回滚请用文件级 snapshot/restore（restore 会丢掉当前未保存状态）。',
    parameters: {
      op: { type: 'string', required: true, description: 'snapshot | restore | list | prune | mark | revert | marks | drop | help' },
      label: { type: 'string', description: '快照 / mark 的名字（snapshot 省略则用时间戳）' },
      objects: { type: 'string', description: 'mark 时限定对象（逗号分隔；省略 = 全场景）' },
      keep: { type: 'integer', description: 'prune 只保留最近 N 个快照，默认 5' },
      note: { type: 'string', description: 'snapshot 备注' },
    },
    output: { schema: ANY_SCHEMA, render: renderOne },
    isConcurrencySafe: () => false,
    async execute(args: any) {
      if (!(await ensureBackend(port))) return { text: '后端不可用（127.0.0.1:' + String(port) + '）' }
      const op = String((args && args.op) || 'list')
      const a: any = {}
      if (args && args.label) a.label = String(args.label)
      if (args && args.note) a.note = String(args.note)
      if (args && args.keep) a.keep = Number(args.keep)
      if (args && args.objects) a.objects = String(args.objects).split(',').map((s) => s.trim()).filter(Boolean)
      const r = await backendPost(port, '/txn', { op: op, args: a }, 300000)
      const lt = leasedText(r)
      if (lt) return { text: lt }
      if (!r || r.ok !== true) return { text: 'TXN ' + op + ' 失败 · ' + String((r && (r.error || r.raw)) || 'unknown') }
      const res = (r && r.result) || {}
      const parts: string[] = []
      parts.push('TXN ' + op + ' ok')
      if (op === 'snapshot' && res.snapshot) parts.push('快照 ' + String(res.snapshot.label) + ' · ' + String(res.snapshot.bytes) + 'B · ' + String(res.snapshot.ms) + 'ms · ' + String(res.snapshot.objects) + ' 对象' + String.fromCharCode(10) + '路径：' + String(res.snapshot.path))
      else if (op === 'restore') parts.push('已恢复：' + JSON.stringify(res.restored || {}).slice(0, 200) + String.fromCharCode(10) + String(res.warning || ''))
      else if (op === 'list') {
        parts.push('快照 ' + String((res.snapshots || []).length) + ' 个：' + (res.snapshots || []).map((s: any) => s.label + '(' + s.bytes + 'B)').join(' · '))
        parts.push('mark：' + JSON.stringify(res.marks || []))
        parts.push('目录：' + String(res.snapshot_dir || ''))
      } else if (op === 'mark') parts.push('已记 mark ' + String(res.mark) + ' · ' + String(res.objects) + ' 个对象')
      else if (op === 'revert') parts.push('回滚 ' + String(res.reverted) + '：改了 ' + String(res.changed_count) + ' 个' + (res.blocked && res.blocked.length ? (' · 跳过 ' + JSON.stringify(res.blocked).slice(0, 200)) : ''))
      else parts.push(JSON.stringify(res).slice(0, 800))
      return { text: parts.join(String.fromCharCode(10)) }
    },
  })), '@dsh-external/dsh-blender-plugin: rt-txn')

  ctx.effect(() => ctx.tools.register(defineTool({
    name: 'blender_rt_preset',
    description: '【配方库】把「参数组合」变成可保存、可套用、可分发的资产：save / list / get / apply / delete / export / import / help。'
      + 'data 用点路径表达：材质节点 {"inputs.Base Color": [1,0,0,1]}、对象属性 {"location": [0,0,1]}、场景设置 {"render.resolution_x": 640}。'
      + 'apply 的 targets 用 MAT:材质名 / OBJ:对象名 / SCENE（逗号分隔）；不给 targets 只预览（dry_run），给了就实际写入并回报 applied/skipped/errors。'
      + 'export/import 走单个 JSON bundle，便于把配方分享到别的机器或会话。',
    parameters: {
      op: { type: 'string', required: true, description: 'save | list | get | apply | delete | export | import | help' },
      name: { type: 'string', description: '配方名（save / get / apply / delete）' },
      kind: { type: 'string', description: '分类（list 可按 kind 过滤），如 material / object / scene' },
      tags: { type: 'string', description: '标签（逗号分隔；list 可按 tag 过滤）' },
      note: { type: 'string', description: '备注（save）' },
      data: { type: 'string', description: '点路径 JSON 字符串（save），如 {"inputs.Roughness": 0.4}' },
      targets: { type: 'string', description: 'apply 目标（逗号分隔）：MAT:材质名 / OBJ:对象名 / SCENE' },
      path: { type: 'string', description: 'export 输出路径 / import 输入路径' },
      names: { type: 'string', description: 'export 选定的配方名（逗号分隔；省略=全部）' },
      overwrite: { type: 'boolean', description: 'save/import 是否覆盖同名（默认 true）' },
    },
    output: { schema: ANY_SCHEMA, render: renderOne },
    isConcurrencySafe: () => false,
    async execute(args: any) {
      if (!(await ensureBackend(port))) return { text: '后端不可用（127.0.0.1:' + String(port) + '）' }
      const op = String((args && args.op) || 'list')
      const a: any = {}
      if (args && args.name) a.name = String(args.name)
      if (args && args.kind) a.kind = String(args.kind)
      if (args && args.note) a.note = String(args.note)
      if (args && args.path) a.path = String(args.path)
      if (args && args.tags) a.tags = String(args.tags).split(',').map((x) => x.trim()).filter(Boolean)
      if (args && args.names) a.names = String(args.names).split(',').map((x) => x.trim()).filter(Boolean)
      if (args && args.targets) a.targets = String(args.targets).split(',').map((x) => x.trim()).filter(Boolean)
      if (args && args.data) {
        try { a.data = JSON.parse(String(args.data)) }
        catch (e) { return { text: 'data 不是合法 JSON: ' + String((e as Error).message).slice(0, 120) } }
      }
      if (args && args.overwrite === false) a.overwrite = false
      const r = await backendPost(port, '/preset', { op: op, args: a }, 300000)
      const lt = leasedText(r)
      if (lt) return { text: lt }
      if (!r || r.ok !== true) return { text: 'PRESET ' + op + ' 失败 · ' + String((r && (r.error || r.raw)) || 'unknown') }
      const res: any = (r && r.result) || {}
      const parts: string[] = []
      parts.push('PRESET ' + op + ' ok')
      if (op === 'list') {
        parts.push('共 ' + String(res.count) + ' 个：' + (res.presets || []).map((p: any) => p.name + '(' + p.kind + ',' + p.keys + '键)').join(' · '))
        parts.push('目录：' + String(res.dir || ''))
      } else if (op === 'apply') {
        const rr = res.report || {}
        parts.push('配方 ' + String(res.preset) + ' · 目标 ' + JSON.stringify(res.targets) + ' · 写入 ' + String((rr.applied || []).length) + ' 项')
        if ((rr.skipped || []).length) parts.push('跳过：' + JSON.stringify(rr.skipped).slice(0, 300))
        if ((rr.errors || []).length) parts.push('错误：' + JSON.stringify(rr.errors).slice(0, 300))
      } else parts.push(JSON.stringify(res).slice(0, 900))
      return { text: parts.join(String.fromCharCode(10)) }
    },
  })), '@dsh-external/dsh-blender-plugin: rt-preset')

  // ---------- 运维 ----------

  ctx.effect(() => ctx.tools.register(defineTool({
    name: 'blender_viewport',
    description: '实时通道后端（127.0.0.1:' + String(port) + '）运维入口：status 看健康/视口区域/计数，doctor 做连通性体检（区分 Blender 未连/主线程忙/addon 线程卡死并给修法），start/stop/restart 管后端，who 看写通道租约（多会话共存时先看它）、lease 拿/抢写权限、release 释放。',
    parameters: {
      op: { type: 'string', required: true, description: 'status | doctor | who | lease | release | start | stop | restart' },
      holder: { type: 'string', description: 'lease/release 时的持有者名（默认本插件进程 pid 标识）' },
      ttl_ms: { type: 'integer', description: 'lease 有效期毫秒，默认 600000（10 min）；写操作会自动续期' },
      force: { type: 'boolean', description: 'lease 时抢占别人持有的租约（默认 false，被别人占用时返回 409 提示）' },
    },
    output: {
      schema: { type: 'string' },
      render: (_args: unknown, value: unknown) => [{ type: 'text', text: String(value) }],
    },
    async execute(args: any) {
      const op = String((args && args.op) || 'status')
      if (op === 'start') {
        paused = false
        if (await probe(port)) return '后端已在运行：' + base(port) + '/（看护已启用）'
        const how = startBackend(port)
        for (let i = 0; i < 20; i++) {
          if (await probe(port)) return '后端已启动（' + how + '）：' + base(port) + '/（看护已启用）'
          await sleep(300)
        }
        return '启动失败：' + how + '（检查 ' + SERVER_PATH + ' 与 node 是否可用）'
      }
      if (op === 'restart') {
        paused = false
        if (child) { try { child.kill() } catch (e) {} child = null }
        killBackendByCmd(port)
        await sleep(500)
        const how2 = startBackend(port)
        for (let i = 0; i < 20; i++) {
          if (await probe(port)) return '后端已重启（' + how2 + '）：' + base(port) + '/'
          await sleep(300)
        }
        return '重启失败：' + how2
      }
      if (op === 'stop') {
        paused = true
        const notes: string[] = []
        if (child) { try { child.kill() } catch (e) {} child = null; notes.push('child-句柄已杀') }
        const killed = killBackendByCmd(port)
        if (killed.length) notes.push('残留后端已杀 pid=' + killed.join(','))
        if (!notes.length) return '没有在跑的后端（127.0.0.1:' + String(port) + '）；看护已暂停'
        await sleep(400)
        return '已停止后端：' + notes.join(' · ') + '（看护已暂停，op=start 恢复）'
      }
      if (op === 'who' || op === 'lease' || op === 'release') {
        try {
          if (op === 'who') {
            const w: any = await backendJson(port, '/who', 15000)
            const lz = (w && w.lease) || {}
            const m = (w && w.metrics) || {}
            return [
              '租约：' + (lz.active ? ('持有者 ' + String(lz.holder) + '（剩余 ' + String(Math.round(Number(lz.expiresInMs || 0) / 1000)) + 's，续期 ' + String(lz.renewals || 0) + ' 次）') : '空闲（谁都能写）'),
              '本会话 holder：' + HOLDER + (lz.active && lz.holder === HOLDER ? '（就是本会话）' : ''),
              '通道：calls=' + String(m.calls || 0) + ' errors=' + String(m.errors || 0) + ' timeouts=' + String(m.timeouts || 0) + ' inflight=' + String(m.inflight || 0) + ' lastCmd=' + String(m.lastCmd || '-'),
              '统计：' + JSON.stringify((w && w.stats) || {}),
            ].join('\n')
          }
          const body: any = { holder: String((args && args.holder) || HOLDER) }
          if (args && args.ttl_ms) body.ttlMs = Number(args.ttl_ms)
          if (op === 'lease' && args && args.force) body.force = true
          const r: any = await backendPost(port, op === 'lease' ? '/lease' : '/release', body, 20000)
          if (op === 'lease' && r && r.error === 'leased') {
            return '租约被占用：holder=' + String(r.holder) + '，剩余 ' + String(Math.round(Number(r.expiresInMs || 0) / 1000)) + 's\n'
              + String(r.hint || '') + '\n（确认对方在跑就知道为什么被挡；要强抢再加 force=true）'
          }
          return (op === 'lease' ? 'LEASE ' : 'RELEASE ') + JSON.stringify(r)
        } catch (e) {
          return op + ' 失败：' + String((e && e.message) || e) + '（后端没起？blender_viewport op=start）'
        }
      }
      if (op === 'doctor') {
        try {
          const d: any = await backendJson(port, '/doctor')
          const m = (d && d.metrics) || {}
          const diag = (d && d.diagnosis) || m.lastDiagnosis || null
          const lines: string[] = []
          lines.push('诊断：' + String((d && d.kind) || (diag && diag.kind) || (d && d.ok ? 'ok' : 'unknown')))
          if (diag && diag.summary) lines.push('结论：' + diag.summary)
          if (diag && diag.fix) lines.push('修法：' + diag.fix)
          if (d && d.addon) lines.push('addon：' + JSON.stringify(d.addon))
          lines.push('计数：' + JSON.stringify(d && d.stats ? d.stats : {}))
          lines.push('指标：calls=' + String(m.calls || 0) + ' errors=' + String(m.errors || 0) + ' timeouts=' + String(m.timeouts || 0)
            + ' inflight=' + String(m.inflight || 0) + ' lastCmd=' + String(m.lastCmd || '-')
            + (m.lastCmdAgeMs === null || m.lastCmdAgeMs === undefined ? '' : (' (' + String(Math.round(Number(m.lastCmdAgeMs) / 1000)) + 's 前)'))
            + ' views=' + String(m.views || 0) + ' headlessRuns=' + String(m.headlessRuns || 0))
          return lines.join('\n')
        } catch (e) {
          return '体检失败：' + String((e && e.message) || e) + '（后端可能没起：blender_viewport op=start）'
        }
      }
      try {
        const s = await backendJson(port, '/status')
        const views = s && s.region && s.region.views ? s.region.views : []
        const v = views[0] || null
        return JSON.stringify({
          running: true,
          paused: paused,
          child: child ? (childAlive() ? ('alive pid=' + String(child.pid)) : ('dead pid=' + String(child.pid))) : 'none',
          watchdog: '15s',
          endpoint: base(port),
          viewportRegion: v ? v.region : null,
          shading: v ? v.shading : null,
          window: s && s.region ? s.region.window : null,
          stats: s.stats,
          diagnosis: s.diagnosis || null,
        })
      } catch (e) {
        return JSON.stringify({ running: false, url: base(port) + '/', error: String((e && e.message) || e), hint: '用 blender_viewport op=start 拉起' })
      }
    },
  })), '@dsh-external/dsh-blender-plugin: tool')
}
