#!/usr/bin/env node
/**
 * tests/linux_selftest.mjs —— 原生 Linux（非 WSL）支持自检（issue #11）
 *
 *   node tests/linux_selftest.mjs
 *
 * 背景：插件原本只按「WSL + Windows 版 Blender」写路径。`wslToWin()` 只分了 Windows / macOS，
 * **其余一律当成 WSL** —— 于是原生 Linux（Manjaro/Ubuntu/…）上任何绝对路径都被映射成
 * `\\wsl.localhost\<distro>\…`。Blender 与该串同机，会把它当**相对路径**，产物写进一个字面量
 * 反斜杠目录，后端再按原路径读就 ENOENT ⇒ `blender_rt_see` 恒报 `frame http 502`（issue #11）。
 * 这与 macOS 当年的坑同形（同机同 OS 就不该做跨 OS 改写），只是 macOS 修了、原生 Linux 漏了。
 *
 * 覆盖：
 *   1. `detectWsl()` 判据决策表 —— **任何平台都能跑**（本文件的主判据，不依赖所在机器是不是 Linux）
 *   2. 当前平台的真实分支自洽（Windows / macOS / WSL 原行为逐字节不变；原生 Linux 恒等）
 *   3. 源码级回归保护（UNC 分支没被删、判据没退化成"只认环境变量"）
 *   4. 真在原生 Linux 上时（WSL 里不执行）：端到端恒等 —— 想在这台机器上强制走到这一段，
 *      在私有挂载命名空间里伪造 /proc/version 即可（见文件末注释）。
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.join(HERE, '..');

let pass = 0;
const fails = [];
function ok(label, cond, extra) {
  if (cond) { pass++; console.log('  ok   ' + label); }
  else { fails.push(label); console.log('  FAIL ' + label + (extra !== undefined ? '  → ' + extra : '')); }
}
function eq(label, got, want) {
  ok(label, JSON.stringify(got) === JSON.stringify(want),
    'got=' + JSON.stringify(got) + ' want=' + JSON.stringify(want));
}

const cfg = await import(path.join(ROOT, 'runtime/config.mjs'));

const IS_WIN = cfg.IS_WIN;
const IS_MAC = cfg.IS_MAC;
const IS_WSL = cfg.IS_WSL;

/* ───────── 1. detectWsl 判据决策表（与所在平台无关，纯函数） ───────── */
console.log('\n[1] detectWsl 判据决策表');
ok('detectWsl 是导出的纯函数（判据可单独验，不必真换一台机器）', typeof cfg.detectWsl === 'function');

if (typeof cfg.detectWsl === 'function') {
  const D = cfg.detectWsl;
  const MANJARO = 'Linux version 6.18.49-1-MANJARO (gcc (GCC) 15.2.1) #1 SMP PREEMPT';
  const WSL2 = 'Linux version 5.15.90.1-microsoft-standard-WSL2 (root@x) #1 SMP';

  eq('Windows 上恒 false（哪怕环境里有 WSL 变量）', D('win32', { WSL_DISTRO_NAME: 'Ubuntu' }, WSL2), false);
  eq('macOS 上恒 false（哪怕环境里有 WSL 变量）', D('darwin', { WSL_DISTRO_NAME: 'Ubuntu' }, WSL2), false);

  eq('原生 Linux：环境变量没有 + 内核不是 microsoft ⇒ false', D('linux', {}, MANJARO), false);
  eq('原生 Linux：有 WSL_INTEROP 但内核是 MANJARO ⇒ 仍按环境变量判 true（互操作已在跑）',
    D('linux', { WSL_INTEROP: '/run/WSL/1_interop' }, MANJARO), true);
  eq('WSL：WSL_DISTRO_NAME 命中 ⇒ true', D('linux', { WSL_DISTRO_NAME: 'Ubuntu' }, MANJARO), true);
  eq('WSL：systemd 服务里环境变量为空（issue #6 现场）⇒ 靠 /proc/version 认出来',
    D('linux', {}, WSL2), true);
  eq('读不到 /proc/version（非 Linux 挂载、容器裁剪）⇒ 不硬猜 WSL', D('linux', {}, ''), false);
  eq('undefined 入参不炸', D('linux', undefined, undefined), false);
  ok('内核串里出现 Microsoft 也算（旧版 WSL 的 /proc/version 长这样）',
    D('linux', {}, 'Linux 4.4.0-19041-Microsoft') === true);
}

/* ───────── 2. 当前平台的真实分支自洽 ───────── */
console.log('\n[2] 当前平台分支');
console.log('  platform = ' + process.platform + ' | IS_WIN = ' + IS_WIN + ' | IS_MAC = ' + IS_MAC
  + ' | IS_WSL = ' + IS_WSL);

if (IS_WIN) {
  ok('IS_WSL 在 Windows 上为 false', IS_WSL === false);
  eq('wslToWin 仍是"反斜杠化"（Windows 原行为不变）', cfg.wslToWin('D:/a/b'), 'D:\\a\\b');
} else if (IS_MAC) {
  ok('IS_WSL 在 macOS 上为 false', IS_WSL === false);
  eq('wslToWin 仍是恒等（macOS 原行为不变）', cfg.wslToWin('/Users/x/f.png'), '/Users/x/f.png');
} else if (IS_WSL) {
  ok('WSL 判据命中（IS_WSL = true）', IS_WSL === true);
  eq('wslToWin 仍映射成 UNC（WSL 原行为逐字节不变）',
    cfg.wslToWin('/home/x/f.png'), '\\\\wsl.localhost\\' + cfg.DISTRO + '\\home\\x\\f.png');
  eq('wslToWin 仍把 /mnt/<盘>/ 翻成盘符', cfg.wslToWin('/mnt/d/a/b.py'), 'D:\\a\\b.py');
} else {
  console.log('  note 这是原生 Linux；端到端恒等断言见 [4]');
}

/* ───────── 3. 源码级回归保护（防"修 Linux 把 WSL 修坏"） ───────── */
console.log('\n[3] 回归保护（WSL/Windows 分支仍在）');
const cfgSrc = fs.readFileSync(path.join(ROOT, 'runtime/config.mjs'), 'utf8');
ok('wslToWin 仍有 UNC 分支', cfgSrc.includes('wsl.localhost'), 'UNC 分支被删了');
ok('wslToWin 仍有 /mnt/<盘> → 盘符分支', cfgSrc.includes("^\\/mnt\\/([a-z])\\/"), '盘符分支被删了');
ok('原生 Linux 提前返回（!IS_WSL ⇒ 恒等）', /if\s*\(!IS_WSL\)\s*return\s*s;/.test(cfgSrc),
  '找不到 "if (!IS_WSL) return s;"');
ok('判据不只看环境变量（退回 /proc/version）',
  cfgSrc.includes('WSL_INTEROP') && cfgSrc.includes('/proc/version'),
  'WSL_INTEROP 或 /proc/version 判据被删了');

/* ───────── 4. 原生 Linux 端到端恒等（本段只在真·原生 Linux 上执行） ───────── */
console.log('\n[4] 原生 Linux 端到端恒等');
if (IS_WIN || IS_MAC || IS_WSL) {
  console.log('  note 当前不是原生 Linux（platform=' + process.platform + ', IS_WSL=' + IS_WSL
    + '），跳过本段 —— 该分支的判据由 [1] 的决策表覆盖');
} else {
  const abs = '/home/someone/.dsh-blender-rt';
  eq('wslToWin 对原生 Linux 绝对路径恒等（这正是 issue #11 的修复点）', cfg.wslToWin(abs), abs);
  ok('不产生 UNC', !String(cfg.wslToWin(abs)).includes('wsl.localhost'), cfg.wslToWin(abs));
  ok('不引入反斜杠', !String(cfg.wslToWin(abs)).includes('\\'), cfg.wslToWin(abs));
  eq('相对路径不被改写', cfg.wslToWin('rel/x.py'), 'rel/x.py');
  eq('workDir 两端同构（win === wsl）', cfg.CFG.workDirWin, cfg.CFG.workDirWsl);
  for (const [k, v] of Object.entries(cfg.PATHS)) {
    ok('PATHS.' + k + ' 无反斜杠', !String(v).includes('\\'), v);
  }
  eq('PATHS.livePngWin 落在 workDir 下',
    cfg.PATHS.livePngWin, path.join(cfg.CFG.workDirWsl, 'dsh_live_viewport.png'));

  // /doctor 的 distro 字段：原生 Linux 上报的必须是自己的发行版，不是 WSL 的兜底名（'Ubuntu'）
  let osRel = '';
  try { osRel = fs.readFileSync('/etc/os-release', 'utf8'); } catch (e) { /* 读不到就按兜底验 */ }
  const m = osRel.match(/^PRETTY_NAME="?([^"\n]+)"?/m) || osRel.match(/^NAME="?([^"\n]+)"?/m);
  const want = m ? m[1].trim() : 'linux';
  eq('describeConfig().distro 报真实 Linux 发行版（不是 WSL 的兜底名）', cfg.describeConfig().distro, want);
}

/* ───────────────────────────── 汇总 ───────────────────────────── */
console.log('\n' + (fails.length ? 'FAILED ' + fails.length + ' / ' + (pass + fails.length)
                                   : 'ALL ' + pass + ' ASSERTIONS PASSED'));
if (fails.length) {
  for (const f of fails) console.log('  - ' + f);
  console.log('\n想在本机强制走到 [4]（原生 Linux 分支）：在私有挂载命名空间里伪造 /proc/version ——');
  console.log("  unshare --mount --map-root-user bash -c 'mount --bind /tmp/fake_ver /proc/version; \\");
  console.log('    env -u WSL_DISTRO_NAME -u WSL_INTEROP node tests/linux_selftest.mjs"\'');
  process.exit(1);
}
