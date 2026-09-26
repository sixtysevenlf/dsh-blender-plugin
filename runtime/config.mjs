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
 *   4) 内置默认     addon 127.0.0.1:9876 · 后端 127.0.0.1:9877 · 租约 TTL 600000 ms
 */
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
export const PKG_ROOT = path.join(HERE, '..');
export const IS_WIN = process.platform === 'win32';
/** WSL 发行版名（拼 UNC 用） */
export const DISTRO = process.env.WSL_DISTRO_NAME || 'Ubuntu';

export const CONFIG_FILES = [
  process.env.DSH_BLENDER_CONFIG,
  path.join(PKG_ROOT, 'dsh-blender.config.json'),
  path.join(os.homedir(), '.dsh', 'dsh-blender.config.json'),
].filter(Boolean);

function readJson(p) {
  try { return JSON.parse(fs.readFileSync(p, 'utf8')); } catch (e) { return null; }
}

/** 载入第一个存在的配置文件（并记住它，方便 doctor/日志说明配置来自哪里） */
function loadFileConfig() {
  for (const p of CONFIG_FILES) {
    const j = readJson(p);
    if (j && typeof j === 'object') return { cfg: j, from: p };
  }
  return { cfg: {}, from: null };
}

const FILE = loadFileConfig();

/** Windows 路径 → WSL 路径（D:\a\b → /mnt/d/a/b；UNC → /…；Windows 上原样返回） */
export function winToWsl(p) {
  const s = String(p || '');
  if (IS_WIN) return s;
  const m = s.match(/^([A-Za-z]):[\\/](.*)$/);
  if (m) return '/mnt/' + m[1].toLowerCase() + '/' + m[2].replace(/\\/g, '/');
  // v0.9.3（D4）：UNC 形态（\\wsl.localhost\<distro>\home\… 或 \\wsl$\…）→ WSL 可见的 /home/…
  // 旧版**原样返回** → 一旦 workDir 是 WSL 路径（wslToWin 先转成 UNC），台账 / results / trajectory
  // 就全落在一个 Node 打不开的 UNC 串上（实测：stale 对账查不到台账）。
  const u = s.match(/^\\\\wsl(?:\.localhost)?\\[^\\]+\\(.*)$/i);
  if (u) return '/' + u[1].replace(/\\/g, '/');
  return s;
}

/** WSL/相对路径 → Windows 侧可用路径（/mnt/x/... → X:\...；WSL 内部 → \\wsl.localhost\<distro>\...） */
export function wslToWin(p) {
  const s = String(p || '');
  if (IS_WIN) return s.replace(/\//g, '\\');
  const m = s.match(/^\/mnt\/([a-z])\/(.*)$/i);
  if (m) return m[1].toUpperCase() + ':' + '\\' + m[2].replace(/\//g, '\\');
  if (s.startsWith('/')) return '\\\\wsl.localhost\\' + DISTRO + s.replace(/\//g, '\\');
  return s;
}

/**
 * v0.9.6（D3 · workDir 共享性自证）：这个 WSL 路径，Windows 侧的 Blender 到底看不看得见？
 *
 * 为什么需要它（现场实测 2026-09-26，租约自检 D2 失败）：
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
let _mountEntries;
function mountEntries() {
  if (_mountEntries !== undefined) return _mountEntries;
  _mountEntries = [];
  try {
    const oct = (s) => String(s).replace(/\\(040|011|012|134)/g, (m, o) => String.fromCharCode(parseInt(o, 8)));
    for (const line of fs.readFileSync('/proc/self/mounts', 'utf8').split('\n')) {
      const f = line.trim().split(' ');
      if (f.length < 3) continue;
      _mountEntries.push({ source: oct(f[0]), target: oct(f[1]), fstype: oct(f[2]) });
    }
  } catch (e) { _mountEntries = []; }
  return _mountEntries;
}

export function wslPathShared(p) {
  const s = String(p || '');
  if (IS_WIN) return { shared: true, why: 'windows' };
  if (s.charAt(0) !== '/') return { shared: null, why: 'not-absolute' };
  if (/^\/mnt\/[a-z](\/|$)/i.test(s)) return { shared: true, why: 'drvfs-drive' };
  const ms = mountEntries();
  if (!ms.length) return { shared: null, why: 'no-mount-table' };
  const root = ms.find((m) => m.target === '/');
  let best = null;
  for (const m of ms) {
    const t = m.target === '/' ? '/' : m.target.replace(/\/+$/, '');
    const hit = (t === '/') ? (s.charAt(0) === '/') : (s === t || s.indexOf(t + '/') === 0);
    if (hit && (!best || t.length > best.target.length)) best = m;
  }
  if (!best) return { shared: null, why: 'no-mount-match' };
  if (root && best.source === root.source) {
    return { shared: true, why: 'distro-root-fs', source: best.source, fstype: best.fstype, mount: best.target };
  }
  return { shared: false, why: 'separate-mount-not-guaranteed-windows-visible',
           source: best.source, fstype: best.fstype, mount: best.target };
}

/** 在 WSL 里问 Windows 要 %LOCALAPPDATA%（没有互操作时返回 null） */
let _lad;
function winLocalAppData() {
  if (_lad !== undefined) return _lad;
  _lad = null;
  if (IS_WIN) { _lad = process.env.LOCALAPPDATA || null; return _lad; }
  try {
    // cwd 要给 Windows 侧目录：在 UNC 路径下 cmd.exe 会警告 "UNC 路径不支持" 并回退到 C:Windows
    const cwd = fs.existsSync('/mnt/c') ? '/mnt/c' : (fs.existsSync('/mnt/d') ? '/mnt/d' : undefined);
    const out = execFileSync('cmd.exe', ['/c', 'echo', '%LOCALAPPDATA%'], { encoding: 'utf8', timeout: 5000, cwd: cwd }).trim();
    if (/^[A-Za-z]:\\/.test(out)) _lad = out;
  } catch (e) { /* 无 interop（纯 Linux）→ 走 os.tmpdir() */ }
  return _lad;
}

/** 工作目录：帧 PNG / 无头脚本 / 临时产物。要求"两端都能读写"。 */
function resolveWorkDir() {
  const given = process.env.DSH_BLENDER_WORKDIR || FILE.cfg.workDir;
  if (given) {
    const win = /^[A-Za-z]:[\\/]/.test(given) ? given : wslToWin(given);
    return { win: win, wsl: winToWsl(given), from: 'config' };
  }
  const lad = winLocalAppData();
  if (lad) {
    const win = path.win32.join(lad, 'dsh-blender-rt');
    return { win: win, wsl: winToWsl(win), from: 'auto:localappdata' };
  }
  const t = path.join(os.tmpdir(), 'dsh-blender-rt');
  return { win: t, wsl: t, from: 'auto:tmpdir' };
}

function listDirSafe(p) {
  try { return fs.readdirSync(p); } catch (e) { return []; }
}

/** 找 Blender：配置 → 常见安装位置 → PATH（Windows / WSL 都能用） */
function detectBlenderExe() {
  const given = process.env.DSH_BLENDER_EXE || FILE.cfg.blenderExe;
  if (given) return { exe: given, from: 'config' };
  const cands = [];
  if (IS_WIN) {
    const pf = process.env.ProgramFiles || 'C:\\Program Files';
    for (const d of listDirSafe(path.join(pf, 'Blender Foundation'))) {
      if (/^Blender/i.test(d)) cands.push(path.join(pf, 'Blender Foundation', d, 'blender.exe'));
    }
    cands.push(path.join(pf, 'Blender Foundation', 'Blender', 'blender.exe'));
  } else {
    // WSL：扫 /mnt/<盘>/Program Files/Blender Foundation/Blender*/blender.exe（含 Steam 版常见位置）
    for (const drive of ['c', 'd', 'e', 'f']) {
      const base = '/mnt/' + drive;
      if (!fs.existsSync(base)) continue;
      for (const d of listDirSafe(path.join(base, 'Program Files', 'Blender Foundation'))) {
        if (/^Blender/i.test(d)) cands.push(path.join(base, 'Program Files', 'Blender Foundation', d, 'blender.exe'));
      }
      for (const rel of ['SteamLibrary/steamapps/common/Blender/blender.exe', 'Steam/steamapps/common/Blender/blender.exe',
                         'Program Files/Blender Foundation/Blender/blender.exe']) {
        cands.push(path.join(base, rel));
      }
    }
  }
  for (const c of cands) { try { if (fs.existsSync(c)) return { exe: c, from: 'auto:install' }; } catch (e) {} }
  // PATH 兜底（Linux 用 which，Windows 用 where）
  try {
    const cmd = IS_WIN ? 'where.exe' : 'which';
    const out = execFileSync(cmd, ['blender'], { encoding: 'utf8', timeout: 5000 }).split(/\r?\n/).filter(Boolean)[0];
    if (out) return { exe: IS_WIN ? out.trim() : out.trim(), from: 'auto:path' };
  } catch (e) { /* 没装到 PATH */ }
  return { exe: null, from: 'not-found' };
}

const WORK = resolveWorkDir();
const BLENDER = detectBlenderExe();

function num(v, d) { const n = Number(v); return Number.isFinite(n) && n > 0 ? n : d; }

/** 最终配置（一次性算好，模块级常量） */
export const CFG = {
  // 通道
  addonHost: process.env.DSH_BLENDER_ADDON_HOST || FILE.cfg.addonHost || '127.0.0.1',
  addonPort: num(process.env.DSH_BLENDER_ADDON_PORT || FILE.cfg.addonPort, 9876),
  // Blender 侧 addon 的线上协议：auto（默认，探测一次）/ ahujasid / category-action。
  // 见 runtime/addon-protocol.mjs；探测结果会出现在 /doctor 与 /who 里。
  addonProtocol: (function () {
    const v = String(process.env.DSH_BLENDER_ADDON_PROTOCOL || FILE.cfg.addonProtocol || 'auto').trim().toLowerCase();
    return v === 'ahujasid' || v === 'category-action' ? v : 'auto';
  })(),
  httpPort: num(process.env.DSH_BLENDER_HTTP_PORT || FILE.cfg.httpPort, 9877),
  holder: process.env.DSH_BLENDER_HOLDER || FILE.cfg.holder || ('plugin-pid-' + String(process.pid)),
  leaseTtlMs: num(process.env.DSH_BLENDER_LEASE_TTL_MS || FILE.cfg.leaseTtlMs, 600000),
  // 目录（成对：Windows 侧给 Blender 写，宿主侧给 Node 读）
  workDirWin: WORK.win,
  workDirWsl: WORK.wsl,
  // 无头进程
  blenderExe: BLENDER.exe,
  /** 用户 Blender 配置目录（GPU 偏好所在）：无头进程默认读不到 → 要继承就设它（配合 factory_startup=false） */
  blenderUserConfig: process.env.DSH_BLENDER_USER_CONFIG || process.env.BLENDER_USER_CONFIG || FILE.cfg.blenderUserConfig || null,
  blenderUserScripts: process.env.DSH_BLENDER_USER_SCRIPTS || process.env.BLENDER_USER_SCRIPTS || FILE.cfg.blenderUserScripts || null,
  /** 热无头 worker 的本地端口（与 addon 9876 / 后端 9877 区分开） */
  workerPort: num(process.env.DSH_BLENDER_WORKER_PORT || FILE.cfg.workerPort, 9879),
  // 溯源（doctor/日志/教程排查用）
  source: { configFile: FILE.from, workDir: WORK.from, blenderExe: BLENDER.from },
};

export const PATHS = {
  livePngWin: path.win32.join(CFG.workDirWin, 'dsh_live_viewport.png'),
  livePngWsl: path.join(CFG.workDirWsl, 'dsh_live_viewport.png'),
  viewPngWin: path.win32.join(CFG.workDirWin, 'dsh_view_capture.png'),
  viewPngWsl: path.join(CFG.workDirWsl, 'dsh_view_capture.png'),
};

/** 配置摘要（自检/排错时打印；blender_rt_headless 缺 exe 时也用它给提示）
 *  over.httpPort：后端实际监听的端口（命令行 --port 会覆盖配置值，摘要要跟着走） */
export function describeConfig(over) {
  const o = over || {};
  return {
    platform: process.platform,
    distro: IS_WIN ? null : DISTRO,
    addon: CFG.addonHost + ':' + String(CFG.addonPort),
    addonProtocol: CFG.addonProtocol,
    http: '127.0.0.1:' + String(o.httpPort || CFG.httpPort),
    workDir: { win: CFG.workDirWin, wsl: CFG.workDirWsl, from: CFG.source.workDir,
               // v0.9.6（D3）：这个工作目录 Windows 侧看不看得见（false = 无头脚本会打不开，必须换目录）
               shared: wslPathShared(CFG.workDirWsl) },
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
  };
}
