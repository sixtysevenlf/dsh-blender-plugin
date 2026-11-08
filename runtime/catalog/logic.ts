// catalog 辅助逻辑：plan op 匹配/建议、命令目录指纹、磁盘 catalog 对拍与家族文本渲染。
import { createHash } from 'node:crypto'
import { execFileSync } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { errorMessage } from '../core/json.js'
import type { DiskCatalog, PlanFamily } from './data.js'
import { PLAN_CATALOG, planOpNames } from './data.js'
import { TOOL_DETAIL } from './tools.js'
import { COMMAND_CATALOG } from './commands.js'

function sha12(buf: string | Buffer): string {
  return createHash('sha256').update(buf).digest('hex').slice(0, 12)
}
const HERE = path.dirname(fileURLToPath(import.meta.url))

/**
 * 干净进程对拍 import 的门面入口：构建产物 lib/runtime/engine.js。
 * 源码布局（runtime/catalog.ts）下回退到包内 lib/runtime/engine.js；构建布局下就是同目录。
 */
const RUNTIME_DIR = path.resolve(HERE, '..')
const ENGINE_SOURCE_PATH = (() => {
  const built = path.join(RUNTIME_DIR, 'engine.js')
  const fromPkg = path.resolve(RUNTIME_DIR, '..', 'lib', 'runtime', 'engine.js')
  for (const c of [built, fromPkg]) {
    try {
      if (fs.existsSync(c)) return c
    } catch (e) {
      /* ignore */
    }
  }
  return built
})()

export function planEditDistance(a: string, b: string): number {
  const m = a.length,
    n = b.length
  let prev: number[] = new Array(n + 1)
  for (let j = 0; j <= n; j++) prev[j] = j
  for (let i = 1; i <= m; i++) {
    const cur = [i]
    for (let j = 1; j <= n; j++) {
      cur[j] = Math.min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1))
    }
    prev = cur
  }
  return prev[n]
}

/** 未知 op 的最近邻建议（含最小骨架）—— 把"写错了"变成"一步改对" */
export interface PlanSuggestion {
  op: string
  distance: number
  family: string | null
  skeleton: string | null
  when: string | null
}
export function planOpSuggestion(opName: unknown): PlanSuggestion | null {
  const target = String(opName || '')
  if (!target) return null
  let best: { name: string; d: number } | null = null
  for (const name of planOpNames()) {
    const d = planEditDistance(target.toLowerCase(), name.toLowerCase())
    if (!best || d < best.d) best = { name: name, d: d }
  }
  if (!best || best.d > Math.max(3, Math.ceil(target.length * 0.34))) return null
  const fam = PLAN_CATALOG.find((e) =>
    e.prefix ? name0StartsWith(best.name, e.prefix) : e.ops.indexOf(best.name) >= 0,
  )
  return {
    op: best.name,
    distance: best.d,
    family: fam ? fam.f : null,
    skeleton: fam ? fam.sk : null,
    when: fam ? fam.when : null,
  }
}
export function name0StartsWith(s: unknown, p: string): boolean {
  return String(s).indexOf(p) === 0
}

/** blender_inspect 跨通道纠错：把 plan op 当 addon 命令发时，直接告诉它该走哪个工具（实测真实发生过） */
export function planOpCrossChannelHint(addonName: unknown): string | null {
  const n = String(addonName || '')
  if (!n || COMMAND_CATALOG[n]) return null
  // ① 全名精确命中（qc_render_catalog / audit_mesh / sculpt_apply …）
  for (const e of PLAN_CATALOG) {
    for (const op of e.ops) {
      const full = e.prefix ? e.prefix + op : op
      if (n === full) {
        return (
          '「' +
          n +
          '」是 blender_op 的 op（' +
          e.f +
          ' 家族），不是 addon 命令 → ' +
          '改用 blender_op(op="' +
          full +
          '", args={...})。' +
          (e.sk ? '最小骨架：' + e.sk : '')
        )
      }
    }
  }
  // ② 只写了下半段（catalog / mesh / apply）→ 补上前缀
  for (const e of PLAN_CATALOG) {
    if (e.prefix && e.ops.indexOf(n) >= 0) {
      const full = e.prefix + n
      return (
        '「' +
        n +
        '」是 plan 通道的 ' +
        e.f +
        ' 家族 op → 完整写法是 blender_op(op="' +
        full +
        '")。' +
        (e.sk ? '最小骨架：' + e.sk : '')
      )
    }
  }
  // ③ 都不是 → 最近邻
  const near = planOpSuggestion(n)
  if (near) {
    return (
      '「' +
      n +
      '」既不是 addon 命令、也不是已知 plan op；最接近的是「' +
      near.op +
      '」→ ' +
      'blender_op(op="' +
      near.op +
      '")。' +
      (near.skeleton ? '最小骨架：' + near.skeleton : '')
    )
  }
  return null
}

/**
 * 目录自己的内容指纹 —— 只哈希"真正会回答的东西"（定序，顺序敏感）。
 * 用途：① 跨进程对拍（干净进程 import 磁盘 engine.mjs 后的 catalogHash 必须相等）
 *       ② 把"目录变没变"变成可比对的 12 位十六进制，而不是"感觉好像多了几族"。
 */
export interface CatalogFingerprint {
  catalogHash: string
  families: number
  opsCount: number
  toolDetailKeys: number
  commandCatalogKeys: number
}
export function catalogFingerprint(): CatalogFingerprint {
  const parts: string[] = []
  for (const e of PLAN_CATALOG)
    parts.push(
      [
        e.f,
        e.prefix,
        e.requires_session ? 'G' : 'L',
        e.write_scope,
        e.ops.join(','),
        e.when,
        e.not,
        e.sk || '',
        e.key || '',
      ].join('|'),
    )
  return {
    catalogHash: sha12(Buffer.from(parts.join(String.fromCharCode(10)), 'utf8')),
    families: PLAN_CATALOG.length,
    opsCount: planOpNames().length,
    toolDetailKeys: Object.keys(TOOL_DETAIL).length,
    commandCatalogKeys: Object.keys(COMMAND_CATALOG).length,
  }
}

/** 另起一个干净 node 进程 import **磁盘上的** engine.js 门面 —— 只有它才是"下一个请求会拿到什么"的裁判 */
export function probeDiskCatalog(timeoutMs: unknown): DiskCatalog {
  if (!ENGINE_SOURCE_PATH)
    return { ok: false, error: 'engine 源码路径未知（import.meta.url 解不出）' }
  const js = [
    'import(' + JSON.stringify(pathToFileURL(ENGINE_SOURCE_PATH).href) + ')',
    '.then(function (m) { process.stdout.write(JSON.stringify(Object.assign({ ok: true, pluginVersion: m.PLUGIN_VERSION }, m.catalogFingerprint()))); })',
    '.catch(function (e) { process.stdout.write(JSON.stringify({ ok: false, error: String((e && e.message) || e) })); })',
  ].join('')
  try {
    // ⚠ 故意不给 --input-type/-e 之外的任何"装依赖"动作：只用当前 node，不联网、不装包。
    const out = execFileSync(process.execPath, ['-e', js], {
      encoding: 'utf8',
      timeout: Math.max(2000, Math.min(30000, Number(timeoutMs) || 8000)),
      stdio: ['ignore', 'pipe', 'pipe'],
      // issue #51：宿主在 Electron 里时 process.execPath 是 GUI 可执行体；探针子进程必须走 node 模式。
      env: Object.assign({}, process.env, { DSH_PROVENANCE_PROBE: '1', ELECTRON_RUN_AS_NODE: '1' }),
      // windowsHide: node.exe 是 console-subsystem，宿主无控制台时会被新配控制台（issue #43；非 Windows no-op）
      windowsHide: true,
    })
    const line = String(out).trim().split(String.fromCharCode(10)).filter(Boolean).pop() || ''
    return JSON.parse(line) as DiskCatalog
  } catch (e) {
    return { ok: false, error: errorMessage(e).slice(0, 200) }
  }
}

/**
 * 加载版本 / 磁盘版本的内容指纹对拍。
 * 默认只读一次磁盘（~0.2 ms）；`verify:true` 才起干净进程对拍（~120 ms），按需付费。
 */
/** 单条 family 的短渲染（渐进披露：先目录，再 family，再 *_help） */
export function renderFamilyText(e: PlanFamily): string {
  const L = []
  L.push('family: ' + e.f + (e.prefix ? '（前缀 ' + e.prefix + '）' : ''))
  L.push(
    'target: ' +
      (e.requires_session ? 'G（需要 GUI 会话）' : 'L（host 本地，不要求会话）') +
      ' · write_scope: ' +
      e.write_scope,
  )
  L.push('什么时候用: ' + e.when)
  L.push('什么时候别用: ' + e.not)
  L.push('ops: ' + e.ops.join(' / '))
  if (e.sk) L.push('最小骨架: ' + e.sk)
  if (e.key) L.push('要点: ' + e.key)
  L.push(
    '细节速查: blender_op(op="' +
      (e.ops.indexOf('help') >= 0 ? (e.prefix ? e.prefix + 'help' : e.f + '_help') : 'help') +
      '")',
  )
  return L.join(String.fromCharCode(10))
}

/**
 * op="catalog" 的载荷。默认**只回短文本**（≈2–3k 字符）：flash 级模型读"短枚举 + 判据 + 骨架"远比读
 * 4.7k 字散文有效；结构化明细要 args={full:true} 才给（那是给机器/复盘用的）。
 *   op="catalog"                         → 一页目录（family 一行 + ops + 骨架要点）
 *   op="catalog", args={family:"sculpt"}  → 只展开这一族
 *   op="catalog", args={full:true}        → 附带结构化 families/tools
 */
/**
 * 单一事实源：/plan 通道的只读 op 白名单（唯一声明处）（本文件是唯一声明处）。
 * server.mjs 直接消费；tests/unit/js/contract/single-source.test.ts 与冻结基线逐名对拍，防迁移丢项/多项。
 * 判据：不改场景、不动对象变换、不注册 handler。新增只读 op 只改这一处。
 */
