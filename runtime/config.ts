/**
 * 分享版配置解析 —— **换机器不用改源码**：环境变量 → 配置文件 → 自动探测 → 默认值。
 *
 * 这文件是本机版（作者机器）与分享版的唯一区别来源：
 *   本机版把 workDir / blender.exe / 端口写死在 engine.mjs；分享版全部走这里。
 *
 * 优先顺序（高 → 低）：
 *   1) 环境变量     DSH_BLENDER_*（见 docs/配置参考.md）
 *   2) 配置文件     $DSH_BLENDER_CONFIG → <包根>/dsh-blender.config.json → ~/.dsh/dsh-blender.config.json
 *   3) 自动探测     工作目录（Windows 用户临时区）/ Blender 安装路径 / PATH
 *   4) 内置默认     Gateway 127.0.0.1:39877 · 租约 TTL 600000 ms
 */
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { execFileSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import { isRecord, readString } from './core/json.js'
import { errorMessage } from './core/json.js'
import { detectBlenderExe } from './blender-detect.js'
import { GATEWAY_DEFAULT_PORT } from './transport/gateway-schema.generated.js'
import { VIRTUAL_GUI_DEFAULT_MAX } from './display/limits.js'

const HERE = path.dirname(fileURLToPath(import.meta.url))
/**
 * 包根 —— 源码布局和构建布局有两种，必须都能找到 package.json / dsh-blender.config.json：
 * - 源码布局：<pkg>/runtime/config.ts → HERE 的上一级就是包根
 * - 构建布局：<pkg>/lib/runtime/config.js → 向上两级才是包根（中间是 lib/）
 */
export const PKG_ROOT = (() => {
  if (fs.existsSync(path.join(HERE, 'package.json'))) return HERE
  const parent = path.join(HERE, '..')
  if (fs.existsSync(path.join(parent, 'package.json'))) return parent
  return path.resolve(HERE, '..', '..')
})()
export const IS_WIN = process.platform === 'win32'
/** macOS：宿主与 Blender 同机同 OS，没有 Windows/WSL 的跨 OS 路径问题 */
export const IS_MAC = process.platform === 'darwin'

/**
 * 是否真的跑在 WSL 里（而不是原生 Linux）—— issue #11。
 *
 * 为什么必须单独判：`wslToWin()` 原来只分了 Windows / macOS，**其余一律当成「Linux 宿主 ⇒ WSL +
 * Windows 上的 Blender」**，把宿主绝对路径映射成 `\\wsl.localhost\<distro>\…`。原生 Linux 上 Blender
 * 与宿主同机同 OS（跟 macOS 一样同构），这个映射会让 Blender 把该串当**相对路径**，产物落进一个字面量
 * 反斜杠目录，后端再按原路径读就 ENOENT ⇒ `blender_see` 恒报 `frame http 502`。
 *
 * 判据：环境变量优先（交互式 WSL 一定有）；再退回 /proc/version —— dsh 跑在 systemd 服务里时**不继承**
 * `WSL_DISTRO_NAME`（issue #6 现场），但那台机器确实是 WSL，不能因此误判成原生 Linux。
 * 剥成纯函数是为了能在**任何平台**上对拍判据（tests/unit/js/platform-paths.test.ts [1]）。
 */
export function detectWsl(
  platform: string,
  env: Record<string, string | undefined> | undefined,
  procVersion: unknown,
): boolean {
  if (platform === 'win32' || platform === 'darwin') return false
  const e = env || {}
  if (e.WSL_DISTRO_NAME || e.WSL_INTEROP) return true
  return /microsoft|wsl/i.test(String(procVersion || ''))
}

export const IS_WSL = detectWsl(
  process.platform,
  process.env,
  (function () {
    try {
      return fs.readFileSync('/proc/version', 'utf8')
    } catch (e) {
      return ''
    }
  })(),
)

/** WSL 发行版名（拼 UNC 用）；非 WSL 平台只用于诊断显示，见 linuxDistroName() */
export const DISTRO = process.env.WSL_DISTRO_NAME || 'Ubuntu'

/** 原生 Linux 的发行版名（只喂 /doctor 的显示字段；WSL 用 DISTRO） */
export function linuxDistroName() {
  try {
    const t = fs.readFileSync('/etc/os-release', 'utf8')
    const m = t.match(/^PRETTY_NAME="?([^"\n]+)"?/m) || t.match(/^NAME="?([^"\n]+)"?/m)
    if (m) return m[1].trim()
  } catch (e) {
    /* 读不到就退回平台名，不编 */
  }
  return 'linux'
}

export const CONFIG_FILES: string[] = [
  process.env.DSH_BLENDER_CONFIG,
  path.join(PKG_ROOT, 'dsh-blender.config.json'),
  path.join(os.homedir(), '.dsh', 'dsh-blender.config.json'),
].filter((p): p is string => Boolean(p))

function readJson(p: string): unknown {
  try {
    return JSON.parse(fs.readFileSync(p, 'utf8'))
  } catch (e) {
    return null
  }
}

/** 载入第一个存在的配置文件（并记住它，方便 doctor/日志说明配置来自哪里） */
interface FileConfig {
  [key: string]: unknown
}

function loadFileConfig(): { cfg: FileConfig; from: string | null } {
  for (const p of CONFIG_FILES) {
    const j = readJson(p)
    if (isRecord(j)) return { cfg: j, from: p }
  }
  return { cfg: {}, from: null }
}

const FILE = loadFileConfig()

/** Windows 路径 → WSL 路径（D:\a\b → /mnt/d/a/b；UNC → /…；Windows 上原样返回） */
export function winToWsl(p: unknown): string {
  const s = String(p || '')
  if (IS_WIN || IS_MAC) return s
  const m = s.match(/^([A-Za-z]):[\\/](.*)$/)
  if (m) return '/mnt/' + m[1].toLowerCase() + '/' + m[2].replace(/\\/g, '/')
  // v0.9.3：UNC 形态（\\wsl.localhost\<distro>\home\… 或 \\wsl$\…）→ WSL 可见的 /home/…
  // 旧版**原样返回** → 一旦 workDir 是 WSL 路径（wslToWin 先转成 UNC），台账 / results / trajectory
  // 就全落在一个 Node 打不开的 UNC 串上（实测：stale 对账查不到台账）。
  const u = s.match(/^\\\\wsl(?:\.localhost)?\\[^\\]+\\(.*)$/i)
  if (u) return '/' + u[1].replace(/\\/g, '/')
  return s
}

/**
 * 宿主路径 → Blender 进程侧的可用路径。
 *   Windows     → 反斜杠化（原样）
 *   WSL         → /mnt/x/... → X:\...；WSL 内部 → \\wsl.localhost\<distro>\...
 *   原生 Linux  → **恒等**（宿主与 Blender 同机同 OS，跟 macOS 一样不该做跨 OS 改写）—— issue #11
 *   macOS       → 恒等
 */
export function wslToWin(p: unknown): string {
  const s = String(p || '')
  if (IS_MAC) return s
  if (IS_WIN) return s.replace(/\//g, '\\')
  // 原生 Linux（非 WSL）：恒等。映射成 \\wsl.localhost\… 的话，同机的 Blender 会把它当**相对路径**，
  // 产物落进一个字面量反斜杠目录（取帧 PNG 写出来但读不到 ⇒ frame http 502、无头脚本打不开）。
  if (!IS_WSL) return s
  const m = s.match(/^\/mnt\/([a-z])\/(.*)$/i)
  if (m) return m[1].toUpperCase() + ':' + '\\' + m[2].replace(/\//g, '\\')
  if (s.startsWith('/')) return '\\\\wsl.localhost\\' + DISTRO + s.replace(/\//g, '\\')
  return s
}

/**
 * v0.9.6（workDir 共享性自证）：这个 WSL 路径，Windows 侧的 Blender 到底看不看得见？
 *
 * 为什么需要它（现场实测 2026-09-26，租约自检失败）：
 *   自检把隔离工作目录设在 os.tmpdir()（WSL 的 /tmp）。Node 侧写脚本**成功**，
 *   但 Windows 的 blender.exe 打不开，报
 *     OSError: Python file "\\wsl.localhost\Ubuntu\tmp\…\dsh_headless_*.py" could not be opened
 *   → 无头回执 resultJson=null，看起来像"租约把写通道锁死了"，其实根因是**工作目录不共享**。
 *   Node 侧 `fs.existsSync` 永远说"在"，因为它在 WSL 命名空间里看；能判定的只有挂载归属。
 *
 * 判据（读 /proc/self/mounts，不猜、不看 mtime）：
 *   · /mnt/<盘>/…                       → Windows 侧是 X:\…，共享（drvfs）
 *   · 与 "/" 同 source 的挂载（发行版根文件系统，本机 /dev/sdd）→ \\wsl.localhost\<distro>\…，共享
 *   · 其它独立挂载（tmpfs / overlay / …）→ **不能保证** Windows 可见 → false（保守：宁可换目录）
 * 三态：true（共享）/ false（不可保证共享）/ null（判不了：Windows 平台、相对路径、无挂载表）。
 */
interface MountEntry {
  source: string
  target: string
  fstype: string
}
let _mountEntries: MountEntry[] | undefined
function mountEntries(): MountEntry[] {
  if (_mountEntries !== undefined) return _mountEntries
  _mountEntries = []
  try {
    const oct = (s: string): string =>
      String(s).replace(/\\(040|011|012|134)/g, (m: string, o: string) =>
        String.fromCharCode(parseInt(o, 8)),
      )
    for (const line of fs.readFileSync('/proc/self/mounts', 'utf8').split('\n')) {
      const f = line.trim().split(' ')
      if (f.length < 3) continue
      _mountEntries.push({ source: oct(f[0]), target: oct(f[1]), fstype: oct(f[2]) })
    }
  } catch (e) {
    _mountEntries = []
  }
  return _mountEntries
}

export interface PathShare {
  shared: boolean | null
  why: string
  source?: string
  fstype?: string
  mount?: string
}
export function wslPathShared(p: unknown): PathShare {
  const s = String(p || '')
  if (IS_WIN) return { shared: true, why: 'windows' }
  if (s.charAt(0) !== '/') return { shared: null, why: 'not-absolute' }
  // native Linux: Blender runs on this same machine, so nothing can be invisible to it
  if (!IS_MAC && !IS_WSL) return { shared: true, why: 'native-linux-same-machine' }
  if (/^\/mnt\/[a-z](\/|$)/i.test(s)) return { shared: true, why: 'drvfs-drive' }
  const ms = mountEntries()
  if (!ms.length) return { shared: null, why: 'no-mount-table' }
  const root = ms.find((m) => m.target === '/')
  let best = null
  for (const m of ms) {
    const t = m.target === '/' ? '/' : m.target.replace(/\/+$/, '')
    const hit = t === '/' ? s.charAt(0) === '/' : s === t || s.indexOf(t + '/') === 0
    if (hit && (!best || t.length > best.target.length)) best = m
  }
  if (!best) return { shared: null, why: 'no-mount-match' }
  if (root && best.source === root.source) {
    return {
      shared: true,
      why: 'distro-root-fs',
      source: best.source,
      fstype: best.fstype,
      mount: best.target,
    }
  }
  return {
    shared: false,
    why: 'separate-mount-not-guaranteed-windows-visible',
    source: best.source,
    fstype: best.fstype,
    mount: best.target,
  }
}

/** 在 WSL 里问 Windows 要 %LOCALAPPDATA%（没有互操作时返回 null） */
let _lad: string | null | undefined
function winLocalAppData(): string | null {
  if (_lad !== undefined) return _lad
  _lad = null
  if (IS_WIN) {
    _lad = process.env.LOCALAPPDATA || null
    return _lad
  }
  if (IS_MAC) return null // macOS 无 %LOCALAPPDATA%；也不需要调 cmd.exe
  try {
    // cwd 要给 Windows 侧目录：在 UNC 路径下 cmd.exe 会警告 "UNC 路径不支持" 并回退到 C:Windows
    const cwd = fs.existsSync('/mnt/c') ? '/mnt/c' : fs.existsSync('/mnt/d') ? '/mnt/d' : undefined
    // windowsHide: WSL 侧 spawn Windows 可执行体（cmd.exe，console-subsystem）时避免新配控制台（issue #43）
    const out = execFileSync('cmd.exe', ['/c', 'echo', '%LOCALAPPDATA%'], {
      encoding: 'utf8',
      timeout: 5000,
      cwd: cwd,
      windowsHide: true,
    }).trim()
    if (/^[A-Za-z]:\\/.test(out)) _lad = out
  } catch (e) {
    /* 无 interop（纯 Linux）→ 走 os.tmpdir() */
  }
  return _lad
}

/** 工作目录：帧 PNG / 无头脚本 / 临时产物。要求"两端都能读写"。 */
type ProbeResult = { ok: true } | { ok: false; why: string }
function probeWritable(dir: string): ProbeResult {
  // 真写一次才算可写（Node 的 mkdir 成功不代表能写：只读挂载/权限/路径过长都会在后一步炸）
  try {
    fs.mkdirSync(dir, { recursive: true })
    const p = path.join(dir, '.dsh_write_probe_' + process.pid)
    fs.writeFileSync(p, 'ok', 'utf8')
    fs.unlinkSync(p)
    return { ok: true }
  } catch (e) {
    return { ok: false, why: errorMessage(e).slice(0, 200) }
  }
}

/**
 * 宿主（Node）自己的路径形态。Windows 原样；WSL 里把 Windows 盘符/UNC 转回 /mnt 或 /；
 * 其余 POSIX 路径原样。这样 workDir 的 .wsl 字段永远是 Node 真能 fopen 的那一个。
 */
function hostPathOf(p: string): string {
  if (IS_WIN) return p
  if (/^[A-Za-z]:[\\/]/.test(p) || /^\\\\/.test(p)) return winToWsl(p)
  return p
}

/** 原始候选（不探写）；回退逻辑在 resolveWorkDir() 里 */
interface RawWorkDir {
  win: string
  wsl: string
  from: string
}
interface WorkDirFallback {
  requested: string
  requestedFrom: string
  why: string
  used: string
  usedFrom: string
  errs: string[]
}
interface WorkDirInfo {
  win: string
  wsl: string
  from: string
  writable: boolean
  fallback: WorkDirFallback | null
}
function resolveWorkDirRaw(): RawWorkDir {
  const given = readString(process.env.DSH_BLENDER_WORKDIR) || readString(FILE.cfg.workDir)
  if (given) {
    const host = hostPathOf(given)
    return { win: blenderPath(host, BLENDER.exe), wsl: host, from: 'config' }
  }
  if (IS_MAC) {
    // macOS：用户级持久目录（不用 os.tmpdir()，它会被系统清理，帧/台账会丢）
    const d = path.join(os.homedir(), '.dsh-blender-rt')
    return { win: d, wsl: d, from: 'auto:homedir' }
  }
  const lad = winLocalAppData()
  if (lad) {
    const host = hostPathOf(path.win32.join(lad, 'dsh-blender-rt'))
    return { win: blenderPath(host, BLENDER.exe), wsl: host, from: 'auto:localappdata' }
  }
  const host = path.join(os.tmpdir(), 'dsh-blender-rt')
  return { win: blenderPath(host, BLENDER.exe), wsl: host, from: 'auto:tmpdir' }
}

/**
 * workDir **可写性自证 + 回退**（现场：/mnt/d 变只读后，后端写不了自己的 status/ledger 就起不来，
 * 而报错只说"看后端日志"，没有一句"你的 workDir 不可写"—— 40 分钟就这么没了）。
 * 顺序：配置/自动探测的候选 → 包内 tmp（工作区内，通常可写）→ OS tmpdir。回退必须显式留痕。
 */
function resolveWorkDir(): WorkDirInfo {
  const cand = resolveWorkDirRaw()
  const pr = probeWritable(cand.wsl)
  if (pr.ok) return Object.assign({}, cand, { writable: true, fallback: null })
  const pkgTmp = path.join(PKG_ROOT, 'tmp')
  const tries = [
    { dir: pkgTmp, from: 'fallback:pkg-tmp' },
    { dir: path.join(os.tmpdir(), 'dsh-blender-rt'), from: 'fallback:tmpdir' },
  ]
  const errs = [cand.wsl + '（' + cand.from + '）：' + pr.why]
  for (const t of tries) {
    const p2 = probeWritable(t.dir)
    if (p2.ok) {
      const host = hostPathOf(t.dir)
      return {
        win: blenderPath(host, BLENDER.exe),
        wsl: host,
        from: t.from,
        writable: true,
        fallback: {
          requested: cand.wsl,
          requestedFrom: cand.from,
          why: pr.why,
          used: t.dir,
          usedFrom: t.from,
          errs: errs,
        },
      }
    }
    errs.push(t.dir + '（' + t.from + '）：' + p2.why)
  }
  // 全写不了：仍然返回原候选（上层会在写的时候报错），但把证据带上
  return Object.assign({}, cand, {
    writable: false,
    fallback: {
      requested: cand.wsl,
      requestedFrom: cand.from,
      why: pr.why,
      used: cand.wsl,
      usedFrom: 'none',
      errs: errs,
    },
  })
}

const FEATURE_VIRTUAL_GUI = (() => {
  const env = readString(process.env.DSH_BLENDER_VIRTUAL_GUI)
  if (env) return { value: env, source: 'env:DSH_BLENDER_VIRTUAL_GUI' }
  const features = isRecord(FILE.cfg.features) ? FILE.cfg.features : null
  const file = features ? readString(features.virtualGui) : ''
  if (file) return { value: file, source: 'file:' + String(FILE.from || 'config') }
  return { value: '', source: 'default' }
})()

const FEATURE_VIRTUAL_GUI_MAX = (() => {
  const env = readString(process.env.DSH_BLENDER_VIRTUAL_GUI_MAX)
  if (env) return { value: env, source: 'env:DSH_BLENDER_VIRTUAL_GUI_MAX' }
  const features = isRecord(FILE.cfg.features) ? FILE.cfg.features : null
  const file = features ? String(features.virtualGuiMax ?? '') : ''
  if (file) return { value: file, source: 'file:' + String(FILE.from || 'config') }
  return { value: '', source: 'default' }
})()

/** blender.exe 探测在 runtime/blender-detect.ts（可注入依赖；Windows 含 Steam 库）。
 *  config.ts 不再转出 detectBlenderExe：需要探测结果的调用方直接 import blender-detect.js；
 *  这里只用一次（模块级 CFG），探测来源见 CFG.source.blenderExe。
 *  必须在 resolveWorkDir 之前：workDir 的 Blender 侧形态取决于 Blender 是 Windows 还是同机进程。 */
const BLENDER = detectBlenderExe({ fileCfg: FILE.cfg })
const WORK = resolveWorkDir()

function num(v: unknown, d: number): number {
  const n = Number(v)
  return Number.isFinite(n) && n > 0 ? n : d
}

/** 最终配置（一次性算好，模块级常量） */
export const CFG = {
  // 通道
  addonHost:
    readString(process.env.DSH_BLENDER_ADDON_HOST) || readString(FILE.cfg.addonHost) || '127.0.0.1',
  addonPort: num(process.env.DSH_BLENDER_ADDON_PORT || readString(FILE.cfg.addonPort), 9876),
  httpPort: num(
    process.env.DSH_BLENDER_HTTP_PORT || readString(FILE.cfg.httpPort),
    GATEWAY_DEFAULT_PORT,
  ),
  /** 3.1 Gateway 单端点端口；DSH_BLENDER_HTTP_PORT 仅作为显式兼容名保留。 */
  gatewayPort: num(
    process.env.DSH_BLENDER_GATEWAY_PORT || readString(FILE.cfg.gatewayPort),
    GATEWAY_DEFAULT_PORT,
  ),
  /**
   * Windows 侧启动代理（默认 127.0.0.1:9878）。
   * WSL 走它拉起 GUI Blender ⇒ 本次启动 0 次互操作调用，回避 microsoft/WSL#41173
   * 的中继空转泄漏（Interop 线程吃满一个核心且无法释放）。
   * 代理不在线时自动回退到旧的互操作 spawn；设为 "off" 可强制回退。
   */
  launchAgentUrl: String(
    process.env.DSH_BLENDER_LAUNCH_AGENT_URL ||
      readString(FILE.cfg.launchAgentUrl) ||
      'http://127.0.0.1:9878',
  ).replace(/\/+$/, ''),
  holder:
    readString(process.env.DSH_BLENDER_HOLDER) ||
    readString(FILE.cfg.holder) ||
    'plugin-pid-' + String(process.pid),
  leaseTtlMs: num(process.env.DSH_BLENDER_LEASE_TTL_MS || readString(FILE.cfg.leaseTtlMs), 600000),
  // 目录（成对：Windows 侧给 Blender 写，宿主侧给 Node 读）
  workDirWin: WORK.win,
  workDirWsl: WORK.wsl,
  /** workDir 探写结果与回退留痕（null=原候选可写；非 null=已回退，见 errs） */
  workDirFallback: WORK.fallback || null,
  workDirWritable: WORK.writable !== false,
  // 无头进程
  blenderExe: BLENDER.exe,
  /** 用户 Blender 配置目录（GPU 偏好所在）：无头进程默认读不到 → 要继承就设它（配合 factory_startup=false） */
  blenderUserConfig:
    readString(process.env.DSH_BLENDER_USER_CONFIG) ||
    readString(process.env.BLENDER_USER_CONFIG) ||
    readString(FILE.cfg.blenderUserConfig) ||
    null,
  blenderUserScripts:
    readString(process.env.DSH_BLENDER_USER_SCRIPTS) ||
    readString(process.env.BLENDER_USER_SCRIPTS) ||
    readString(FILE.cfg.blenderUserScripts) ||
    null,
  /** 热无头 worker 的本地端口（与 Gateway 39877 区分开） */
  workerPort: num(process.env.DSH_BLENDER_WORKER_PORT || readString(FILE.cfg.workerPort), 9879),
  // 功能开关（3.1：virtual-gui 默认 off；Linux preview 需显式 on）
  features: {
    virtualGui: FEATURE_VIRTUAL_GUI.value,
    source: FEATURE_VIRTUAL_GUI.source,
    /** virtual-gui 并发上限（1–16）；低资源时由 runtime/display/limits.ts 降到 2。 */
    virtualGuiMax: Number(FEATURE_VIRTUAL_GUI_MAX.value || VIRTUAL_GUI_DEFAULT_MAX),
    virtualGuiMaxSource: FEATURE_VIRTUAL_GUI_MAX.source,
  },
  // 溯源（doctor/日志/教程排查用）
  source: { configFile: FILE.from, workDir: WORK.from, blenderExe: BLENDER.from },
}

/**
 * Blender 进程是否使用 Windows 路径（而不是与宿主相同的 POSIX 路径）。
 *
 * 3.0 的规则是「Windows 宿主 / macOS 宿主 / 其余一律当 WSL+Windows Blender」。3.1 起
 * Linux（含 WSL 里的 Linux Blender）也能被 virtual-gui provider 拉起，因此 WSL 不再
 * 自动意味着 Blender 在 Windows 侧：只有配置的 Blender 可执行文件本身是 Windows 程序
 * 时才做 /mnt 与 UNC 映射，否则必须把宿主路径原样交给同机的 Linux Blender。
 *
 * 判据是纯函数，便于在任意平台对拍（tests/unit/js/platform-paths.test.ts）。
 */
export function blenderUsesWindowsPaths(
  platform: string,
  isWsl: boolean,
  blenderExe: unknown,
): boolean {
  if (platform === 'win32') return true
  if (platform === 'darwin') return false
  if (!isWsl) return false
  const exe = String(blenderExe || '')
  return /.exe$/i.test(exe) || /^[A-Za-z]:[\\/]/.test(exe)
}

/**
 * 宿主侧路径 → Blender 进程侧路径。
 *   Windows / WSL+Windows Blender → 反斜杠 / UNC
 *   原生 Linux、macOS、WSL+Linux Blender → 恒等
 */
export function blenderPath(p: unknown, blenderExe: unknown = CFG.blenderExe): string {
  const s = String(p || '')
  if (blenderUsesWindowsPaths(process.platform, IS_WSL, blenderExe)) {
    return IS_WIN ? s.replace(/\//g, '\\') : wslToWin(s)
  }
  return s
}

/**
 * 成对路径：`...Win` 给 Blender 进程用，`...Wsl` 给宿主 Node 用。
 * macOS / 原生 Linux / WSL+Linux Blender 上两者同构，不能再无脑 path.win32.join 或 UNC
 * 映射 —— 那会产出 \\Users\\... 或 \\wsl.localhost\\...，同机 Blender 打不开（取帧静默失败）。
 */
function blenderSidePath(p: string, name: string): string {
  return blenderPath(path.join(p, name))
}

export const PATHS = {
  // workDirWsl 是宿主真实路径；Blender 侧形态由 blenderPath 按 Blender 平台推导，
  // 不再依赖 workDirWin 是否已被提前转换成 UNC（WSL 里的 Linux Blender 会因此 ENOENT）。
  livePngWin: blenderSidePath(CFG.workDirWsl, 'dsh_live_viewport.png'),
  livePngWsl: path.join(CFG.workDirWsl, 'dsh_live_viewport.png'),
  viewPngWin: blenderSidePath(CFG.workDirWsl, 'dsh_view_capture.png'),
  viewPngWsl: path.join(CFG.workDirWsl, 'dsh_view_capture.png'),
}

/** 配置摘要（自检/排错时打印；blender_run 缺 exe 时也用它给提示）
 *  over.httpPort：后端实际监听的端口（命令行 --port 会覆盖配置值，摘要要跟着走） */
export function describeConfig(over: { httpPort?: number | string } = {}) {
  const o = over || {}
  return {
    platform: process.platform,
    // WSL 报发行版名（UNC 里用的就是它）；原生 Linux 报自己的发行版 —— 别再把 Manjaro 报成 'Ubuntu'（issue #11）
    distro: IS_WIN || IS_MAC ? null : IS_WSL ? DISTRO : linuxDistroName(),
    addon: CFG.addonHost + ':' + String(CFG.addonPort),
    http: '127.0.0.1:' + String(o.httpPort || CFG.httpPort),
    gateway: '127.0.0.1:' + String(CFG.gatewayPort),
    workDir: {
      win: CFG.workDirWin,
      wsl: CFG.workDirWsl,
      from: CFG.source.workDir,
      fallback: CFG.workDirFallback,
      // v0.9.6：这个工作目录 Windows 侧看不看得见（false = 无头脚本会打不开，必须换目录）
      shared: wslPathShared(CFG.workDirWsl),
    },
    blenderExe: CFG.blenderExe,
    blenderFrom: CFG.source.blenderExe,
    blenderUserConfig: CFG.blenderUserConfig,
    blenderUserScripts: CFG.blenderUserScripts,
    workerPort: CFG.workerPort,
    configFile: CFG.source.configFile,
    holder: CFG.holder,
    leaseTtlMs: CFG.leaseTtlMs,
    env: {
      DSH_BLENDER_WORKDIR: process.env.DSH_BLENDER_WORKDIR || null,
      DSH_BLENDER_EXE: process.env.DSH_BLENDER_EXE || null,
      DSH_BLENDER_HTTP_PORT: process.env.DSH_BLENDER_HTTP_PORT || null,
      DSH_BLENDER_ADDON_PROTOCOL: process.env.DSH_BLENDER_ADDON_PROTOCOL || null,
      DSH_BLENDER_CONFIG: process.env.DSH_BLENDER_CONFIG || null,
    },
  }
}
