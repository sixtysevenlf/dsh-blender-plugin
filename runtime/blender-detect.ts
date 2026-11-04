/**
 * blender.exe 探测 —— 从 config.ts 拆出来的独立模块（纯探测，不 import config.ts，无环）。
 *
 * 为什么单独一个文件：scripts/check-function-budget.mjs 给 TS 文件定的预算是 600 行，config.ts
 * 本身就排到了 550+；Windows Steam 探测（多盘符根 + libraryfolders.vdf 容错解析 + 依赖注入面）
 * 必须另找地方，否则只能靠加债务白名单过关。
 *
 * 优先级（高 → 低，与 docs/配置参考.md §4 一致）：
 *   1) DSH_BLENDER_EXE 环境变量    → from='config'
 *   2) 配置文件 blenderExe        → from='config'
 *   3) 自动扫描安装位置           → from='auto:install'（官方安装器）/ 'auto:steam'（Steam 库）
 *   4) PATH（Windows where / Linux which）→ from='auto:path'
 *   5) 都没有                     → { exe: null, from: 'not-found' }
 *
 * 现场（2026-11-04 报告 §3.1）：Windows 只扫 %ProgramFiles%\Blender Foundation 时，
 * Blender 装在 D:\Program Files\Steam\steamapps\common\Blender 的机器上 exe 恒为 null，
 * headless worker 直接 spawn(null) —— 所以 Windows 必须补 Steam。
 *
 * 依赖注入：默认值就是生产实现（process.env / fs / execFileSync / os.homedir），
 * 测试（tests/unit/js/blender-detect.test.ts）可整组替换，在 os.tmpdir() 里构造 Steam 布局，
 * 不依赖真实盘符 / 注册表 / Steam 客户端，Linux CI 也能跑。
 */
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { execFileSync } from 'node:child_process'
import { readString } from './core/json.js'

/** 探测依赖面：默认 = 生产实现；测试按需覆盖（未给的键仍走生产实现） */
export interface BlenderDetectDeps {
  env: Record<string, string | undefined>
  isWin: boolean
  isMac: boolean
  homedir: () => string
  exists: (p: string) => boolean
  listDir: (p: string) => string[]
  readText: (p: string) => string | null
  exec: (cmd: string, args: string[]) => string
  /** 配置文件内容（config.ts 的 FILE.cfg）；单独给是为了让 env/config 覆盖也能被测到 */
  fileCfg: Record<string, unknown>
}

export interface BlenderDetectResult {
  /** 命中的 blender 可执行文件；都没有就是 null */
  exe: string | null
  /** 溯源：config / auto:install / auto:steam / auto:path / not-found */
  from: string
}

interface Candidate {
  exe: string
  from: string
}

/** Windows Steam 安装根的常见相对位置（盘符后面那一段） */
const STEAM_REL_DIRS: string[][] = [
  ['Program Files', 'Steam'],
  ['Program Files (x86)', 'Steam'],
  ['Steam'],
  ['SteamLibrary'],
]
const STEAM_ENV_KEYS = ['ProgramFiles', 'ProgramFiles(x86)', 'ProgramW6432']
const DRIVE_LETTERS = ['C', 'D', 'E', 'F']

function normPath(p: string): string {
  return String(p).replace(/\\/g, '/').replace(/\/+$/, '').toLowerCase()
}

/** 去重保序（Windows 上同一个根会从 env 与盘符两条路各来一次） */
function pushUnique(out: string[], p: string): void {
  const k = normPath(p)
  if (!k) return
  if (out.some((x) => normPath(x) === k)) return
  out.push(p)
}

function existsSafe(p: string): boolean {
  try {
    return fs.existsSync(p)
  } catch (e) {
    return false
  }
}

function listDirSafe(p: string): string[] {
  try {
    return fs.readdirSync(p)
  } catch (e) {
    return []
  }
}

function readTextSafe(p: string): string | null {
  try {
    return fs.readFileSync(p, 'utf8')
  } catch (e) {
    return null
  }
}

function execText(cmd: string, args: string[]): string {
  // windowsHide: where.exe 是 console-subsystem，宿主无控制台时会被新配控制台（issue #43；非 Windows no-op）
  return execFileSync(cmd, args, {
    encoding: 'utf8',
    timeout: 5000,
    windowsHide: true,
    stdio: ['ignore', 'pipe', 'ignore'],
  })
}

function detectDeps(over: Partial<BlenderDetectDeps> | undefined): BlenderDetectDeps {
  const d: BlenderDetectDeps = {
    env: process.env,
    isWin: process.platform === 'win32',
    isMac: process.platform === 'darwin',
    homedir: () => os.homedir(),
    exists: existsSafe,
    listDir: listDirSafe,
    readText: readTextSafe,
    exec: execText,
    fileCfg: {},
  }
  const o = over || {}
  if (o.env !== undefined) d.env = o.env
  if (o.isWin !== undefined) d.isWin = o.isWin
  if (o.isMac !== undefined) d.isMac = o.isMac
  if (o.homedir !== undefined) d.homedir = o.homedir
  if (o.exists !== undefined) d.exists = o.exists
  if (o.listDir !== undefined) d.listDir = o.listDir
  if (o.readText !== undefined) d.readText = o.readText
  if (o.exec !== undefined) d.exec = o.exec
  if (o.fileCfg !== undefined) d.fileCfg = o.fileCfg
  return d
}

/**
 * 从 libraryfolders.vdf 里取所有 "path" 值（容错：不解析完整 VDF，只抓这一种键）。
 *
 * 只认带引号的 path 值：空值、裸 "path" 键行、注释/垃圾行都跳过；VDF 的 \\ 转义还原成 \。
 * 读不到文件（readText 返回 null）→ 空数组，调用方仍会试默认根。
 */
function parseVdfPaths(text: string | null): string[] {
  const out: string[] = []
  if (!text) return out
  const re = /"path"\s+"((?:[^"\\]|\\.)*)"/gi
  let m = re.exec(text)
  while (m) {
    const p = m[1].replace(/\\(.)/g, '$1').trim()
    if (p) out.push(p)
    m = re.exec(text)
  }
  return out
}

/** 一个 Steam 根下的库目录：根自己 + libraryfolders.vdf 列出的（vdf 缺失/损坏就只剩根） */
function steamLibraries(d: BlenderDetectDeps, root: string): string[] {
  const libs: string[] = []
  pushUnique(libs, root)
  for (const lib of parseVdfPaths(d.readText(path.join(root, 'steamapps', 'libraryfolders.vdf')))) {
    pushUnique(libs, lib)
  }
  return libs
}

/** 存在的 Steam 根：%ProgramFiles%/%ProgramFiles(x86)%/%ProgramW6432% 下的 Steam，再补 C/D/E/F 常见位置 */
function steamRoots(d: BlenderDetectDeps): string[] {
  const all: string[] = []
  for (const key of STEAM_ENV_KEYS) {
    const base = readString(d.env[key])
    if (base) pushUnique(all, path.join(base, 'Steam'))
  }
  for (const drive of DRIVE_LETTERS) {
    for (const rel of STEAM_REL_DIRS) {
      pushUnique(all, path.join(drive + ':' + path.sep, ...rel))
    }
  }
  return all.filter((root) => d.exists(root))
}

/**
 * Windows 候选：官方安装器（版本化目录）优先，然后每个 Steam 根 / 库。
 * 路径用 path.join（Windows 上就是 path.win32.join），盘符根显式带分隔符，
 * 避免 path.win32.join('C:', …) 变成驱动器相对路径。
 */
function winCandidates(d: BlenderDetectDeps): Candidate[] {
  const out: Candidate[] = []
  const pf = readString(d.env.ProgramFiles) || 'C:\\Program Files'
  const foundation = path.join(pf, 'Blender Foundation')
  for (const dir of d.listDir(foundation)) {
    if (/^Blender/i.test(dir)) {
      out.push({ exe: path.join(foundation, dir, 'blender.exe'), from: 'auto:install' })
    }
  }
  out.push({ exe: path.join(foundation, 'Blender', 'blender.exe'), from: 'auto:install' })
  for (const root of steamRoots(d)) {
    for (const lib of steamLibraries(d, root)) {
      out.push({
        exe: path.join(lib, 'steamapps', 'common', 'Blender', 'blender.exe'),
        from: 'auto:steam',
      })
    }
  }
  return out
}

/** macOS 候选：.app bundle，可执行文件在 Contents/MacOS 下（行为与拆分前一致） */
function macCandidates(d: BlenderDetectDeps): Candidate[] {
  const out: Candidate[] = []
  const homes = ['/Applications', path.join(d.homedir(), 'Applications')]
  for (const v of d.listDir('/Volumes')) homes.push(path.join('/Volumes', v, 'Applications'))
  for (const base of homes) {
    for (const dir of d.listDir(base)) {
      if (/^Blender.*\.app$/i.test(dir)) {
        out.push({
          exe: path.join(base, dir, 'Contents', 'MacOS', 'Blender'),
          from: 'auto:install',
        })
      }
    }
    out.push({
      exe: path.join(base, 'Blender.app', 'Contents', 'MacOS', 'Blender'),
      from: 'auto:install',
    })
  }
  return out
}

/** Linux/WSL 候选：/mnt/<盘>/… 下的官方安装与 Steam 库（行为与拆分前一致，from 也保持 auto:install） */
function linuxCandidates(d: BlenderDetectDeps): Candidate[] {
  const out: Candidate[] = []
  for (const drive of ['c', 'd', 'e', 'f']) {
    const base = '/mnt/' + drive
    if (!d.exists(base)) continue
    const foundation = path.join(base, 'Program Files', 'Blender Foundation')
    for (const dir of d.listDir(foundation)) {
      if (/^Blender/i.test(dir)) {
        out.push({ exe: path.join(foundation, dir, 'blender.exe'), from: 'auto:install' })
      }
    }
    for (const rel of [
      'SteamLibrary/steamapps/common/Blender/blender.exe',
      'Steam/steamapps/common/Blender/blender.exe',
      'Program Files/Blender Foundation/Blender/blender.exe',
    ]) {
      out.push({ exe: path.join(base, rel), from: 'auto:install' })
    }
  }
  return out
}

export function detectBlenderExe(over: Partial<BlenderDetectDeps> = {}): BlenderDetectResult {
  const d = detectDeps(over)
  const given = readString(d.env.DSH_BLENDER_EXE) || readString(d.fileCfg.blenderExe)
  if (given) return { exe: given, from: 'config' }
  const cands = d.isWin ? winCandidates(d) : d.isMac ? macCandidates(d) : linuxCandidates(d)
  for (const c of cands) {
    if (d.exists(c.exe)) return { exe: c.exe, from: c.from }
  }
  // PATH 兜底（Linux 用 which，Windows 用 where）
  try {
    const out = d
      .exec(d.isWin ? 'where.exe' : 'which', ['blender'])
      .split(/\r?\n/)
      .filter(Boolean)[0]
    if (out) return { exe: out.trim(), from: 'auto:path' }
  } catch (e) {
    /* 没装到 PATH */
  }
  return { exe: null, from: 'not-found' }
}
