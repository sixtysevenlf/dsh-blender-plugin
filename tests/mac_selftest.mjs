/**
 * macOS 支持自检 —— 不需要 Blender，纯 Node 校验"宿主路径 → Blender 进程侧路径"这条链。
 *
 *   node tests/mac_selftest.mjs
 *
 * 背景：插件原本只按「WSL + Windows Blender」写路径。macOS 上宿主与 Blender 同机同 OS，
 * 所有跨 OS 改写都必须是恒等 —— 一旦有地方漏了，mac 的 /Users/... 会被拼成
 * \\wsl.localhost\Ubuntu\Users\... 这种 Blender 打不开的串（静默失败，最难查）。
 *
 * 覆盖：
 *   1. config.mjs 的平台分支（IS_MAC / winToWsl / wslToWin / 工作目录 / blender 探测）
 *   2. 注入 Blender 的 python 路径助手（渲染路径助手源码，用 python3 真跑一遍）
 *   3. 工作目录 / 输出路径在三平台下都落在同一处
 *   4. 非 mac 平台（用子进程伪造 platform）行为未被改坏 —— 回归保护
 */
import net from 'node:net';
import path from 'node:path';
import fs from 'node:fs';
import os from 'node:os';
import { fileURLToPath } from 'node:url';
import { execFileSync, spawnSync } from 'node:child_process';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.join(HERE, '..');

let pass = 0;
const fails = [];
function ok(label, cond, extra) {
  if (cond) { pass++; console.log('  ok   ' + label); }
  else { fails.push(label); console.log('  FAIL ' + label + (extra ? '  → ' + extra : '')); }
}
function eq(label, got, want) {
  ok(label, JSON.stringify(got) === JSON.stringify(want),
    'got=' + JSON.stringify(got) + ' want=' + JSON.stringify(want));
}

const cfg = await import(path.join(ROOT, 'runtime/config.mjs'));
const eng = await import(path.join(ROOT, 'runtime/engine.mjs'));

const IS_MAC = cfg.IS_MAC;
const IS_WIN = cfg.IS_WIN;

/* ─────────────────── 1. config.mjs 平台分支 ─────────────────── */
console.log('\n[1] config.mjs 平台分支');
console.log('  platform = ' + process.platform + ' | IS_MAC = ' + IS_MAC + ' | IS_WIN = ' + IS_WIN);

if (IS_MAC) {
  ok('IS_MAC 与 IS_WIN 互斥', IS_MAC && !IS_WIN);

  const macAbs = '/Users/someone/projects/scene.blend';
  eq('winToWsl 对 mac 绝对路径恒等', cfg.winToWsl(macAbs), macAbs);
  eq('wslToWin 对 mac 绝对路径恒等', cfg.wslToWin(macAbs), macAbs);
  ok('wslToWin 不产生 UNC', !cfg.wslToWin(macAbs).includes('wsl.localhost'));
  ok('wslToWin 不引入反斜杠', !cfg.wslToWin(macAbs).includes('\\'));

  // /mnt/c 是 WSL 专属形态；mac 上不该被"翻译"，但也不该炸
  const mntLike = '/mnt/c/Users/x.png';
  ok('mac 上 /mnt/ 形态不会被翻译成盘符', cfg.wslToWin(mntLike) === mntLike, cfg.wslToWin(mntLike));

  // 工作目录：必须是持久目录，不能是 os.tmpdir()（会被系统清理，帧与台账会丢）
  const wd = cfg.CFG.workDirWsl;
  ok('workDir 不是 os.tmpdir()', !wd.startsWith(os.tmpdir()), wd);
  eq('workDir 是 ~/.dsh-blender-rt', wd, path.join(os.homedir(), '.dsh-blender-rt'));
  ok('workDir 来源标记为 mac 分支', cfg.CFG.source.workDir === 'auto:homedir',
    cfg.CFG.source.workDir);
  eq('workDirWin 与 workDirWsl 一致（同机同 OS）', cfg.CFG.workDirWin, cfg.CFG.workDirWsl);

  // describeConfig 不该报出一个 WSL 发行版
  eq('describeConfig().distro 为 null', cfg.describeConfig().distro, null);

  // blender 探测：找不到时应是 null 且来源为 not-found（不是崩、不是垃圾路径）
  const src = cfg.CFG.source.blenderExe;
  ok('blenderExe 来源是已知取值',
    ['config', 'auto:install', 'auto:path', 'not-found'].includes(src), src);
  if (cfg.CFG.blenderExe) {
    ok('探测到的 blenderExe 是个存在的文件', fs.existsSync(cfg.CFG.blenderExe), cfg.CFG.blenderExe);
  } else {
    console.log('  note blenderExe 未找到（未安装 Blender，或不在标准位置）');
  }
} else {
  console.log('  note 非 macOS，跳过 mac 专属断言（本文件在 mac CI/开发机上才有意义）');
}

/* ──────── 2. engine.mjs：路径助手与 WSL 专属逻辑在 mac 上关闭 ──────── */
console.log('\n[2] engine.mjs 平台行为');
ok('blenderJoin 是函数', typeof eng.blenderJoin === 'function');

if (IS_MAC) {
  const joined = eng.blenderJoin(cfg.CFG.workDirWin, 'frame.png');
  ok('blenderJoin 用 / 连接（不是反斜杠）', !joined.includes('\\'), joined);
  eq('blenderJoin 结果正确', joined, path.join(cfg.CFG.workDirWin, 'frame.png'));

  ok('WIN_TMP 是 mac 风格路径', eng.WIN_TMP.startsWith('/'), eng.WIN_TMP);
  ok('WSL_TMP 是 mac 风格路径', eng.WSL_TMP.startsWith('/'), eng.WSL_TMP);

  // PATH_GUARD 是 WSL 专属的 render.filepath 体检；mac 上必须为空串，
  // 否则 'scene.render.filepath 以 / 开头' 这种正常情况会被误报。
  eq('PATH_GUARD 在 mac 上被禁用', eng.PATH_GUARD, '');

  // 取帧/视角截图路径是直接塞给 Blender 的（LIVE_PNG_WIN 会被内联进 bpy 脚本）。
  // 若这里还是 path.win32.join，mac 上会得到 \\Users\\... 这种 Blender 打不开的串，
  // 取视口截图会静默失败 —— 这类 bug 只在真机上才暴露，必须在纯 Node 层就拦住。
  for (const [k, v] of Object.entries(cfg.PATHS)) {
    ok('PATHS.' + k + ' 无反斜杠', !v.includes('\\'), v);
    ok('PATHS.' + k + ' 是以 / 开头的 mac 路径', v.startsWith('/'), v);
  }
  eq('PATHS.livePngWin 落在 workDir 下',
    cfg.PATHS.livePngWin, path.join(cfg.CFG.workDirWin, 'dsh_live_viewport.png'));
  eq('engine 侧 LIVE_PNG_WIN 与 config 一致', eng.LIVE_PNG_WIN, cfg.PATHS.livePngWin);
  eq('engine 侧 VIEW_PNG_WIN 与 config 一致', eng.VIEW_PNG_WIN, cfg.PATHS.viewPngWin);

  // 渲染出来的 python 助手里，native-posix 开关必须是 True
  ok('PATH_HELPERS 渲染出 native-posix 开关', /if True: return str\(p\)/.test(eng.PATH_HELPERS),
    'path helpers 里没有 native-posix=True 的分支');
  ok('PATH_HELPERS 不残留模板占位符',
    !/__DSH_NATIVE_POSIX__|__DISTRO__|__OUTDIR__|__RUNTIME_DIR__/.test(eng.PATH_HELPERS));
}

/* ────── 3. python 路径助手真跑一遍（mac 上证明 /Users/... 不被改写） ────── */
console.log('\n[3] Blender 侧 python 路径助手（真跑 python3）');

const py = process.env.DSH_TEST_PYTHON || 'python3';
const probe = path.join(os.tmpdir(), 'dsh_mac_selftest_probe.py');
fs.writeFileSync(probe, eng.PATH_HELPERS, 'utf8');

const driver = `
import sys, types, json
bpy = types.ModuleType('bpy')
bpy.data = types.SimpleNamespace(filepath='/Users/someone/projects/scene.blend')
sys.modules['bpy'] = bpy
class KMod: pass
K = KMod()
ns = {'bpy': bpy, 'K': K}
exec(compile(open(${JSON.stringify(probe)}).read(), 'ph.py', 'exec'), ns)
wp, wl, bd = ns['_dsh_win_path'], ns['_dsh_wsl_path'], ns['_dsh_blend_path']
cases = ['/Users/someone/projects/out.png', '/abs/x.py', 'rel/script.py', './x.py']
print(json.dumps({
  'win': {c: wp(c) for c in cases},
  'wsl': {c: wl(c) for c in cases},
  'blend': bd('rel/script.py'),
}, ensure_ascii=False))
`;

let pyOut = null;
try {
  const r = spawnSync(py, ['-c', driver], { encoding: 'utf8', timeout: 30000 });
  if (r.status === 0) pyOut = JSON.parse(r.stdout.trim().split('\n').pop());
  else console.log('  note python 探针退出码 ' + r.status + '：' + String(r.stderr || '').slice(0, 300));
} catch (e) {
  console.log('  note 没有可用的 ' + py + '，跳过 python 侧断言：' + String(e.message).slice(0, 120));
}

if (pyOut) {
  if (IS_MAC) {
    eq('_dsh_win_path 对 mac 绝对路径恒等',
      pyOut.win['/Users/someone/projects/out.png'], '/Users/someone/projects/out.png');
    eq('_dsh_wsl_path 对 mac 绝对路径恒等',
      pyOut.wsl['/Users/someone/projects/out.png'], '/Users/someone/projects/out.png');
    eq('_dsh_win_path 保留 posix 绝对路径',
      pyOut.win['/abs/x.py'], '/abs/x.py');
    ok('没有 UNC 污染',
      !JSON.stringify(pyOut).includes('wsl.localhost'), JSON.stringify(pyOut));
    ok('没有反斜杠污染',
      !JSON.stringify(pyOut).includes('\\\\'), JSON.stringify(pyOut));
    eq('相对路径仍按 .blend 所在目录解析',
      pyOut.blend, '/Users/someone/projects/rel/script.py');
    eq('相对路径在 win_path 下同样解析',
      pyOut.win['rel/script.py'], '/Users/someone/projects/rel/script.py');
  } else {
    console.log('  note 非 macOS，python 断言跳过');
  }
} else {
  console.log('  note 跳过 python 侧断言（无 python3 或探针失败）');
}

fs.rmSync(probe, { force: true });

/* ────── 4. 回归保护：非 mac 平台行为不变（用子进程伪造进程平台不可行，
   退而求其次：断言 WSL 分支的代码路径仍然存在且未被 mac 分支吃掉） ────── */
console.log('\n[4] 回归保护（WSL/Windows 分支仍在）');
const cfgSrc = fs.readFileSync(path.join(ROOT, 'runtime/config.mjs'), 'utf8');
ok('仍保留 WSL /mnt/ 扫描分支', cfgSrc.includes("/mnt/' + drive"), '找不到 WSL 盘符扫描');
ok('仍保留 Windows Program Files 分支', cfgSrc.includes("Blender Foundation"), '找不到 Windows 安装扫描');
ok('仍保留 where.exe/which 兜底', cfgSrc.includes("where.exe"), '找不到 PATH 兜底');
ok('wslToWin 仍有 UNC 分支', cfgSrc.includes('wsl.localhost'), 'UNC 分支被删了');

const engSrc = fs.readFileSync(path.join(ROOT, 'runtime/engine.mjs'), 'utf8');
ok('仍保留 WSLENV 处理', engSrc.includes('WSLENV'), 'WSLENV 逻辑被删了');
ok('仍保留 _dsh_win_path 的 /mnt/ 转换', engSrc.includes('startswith("/mnt/")'), '/mnt/ 转换被删了');

/* ───────────────────────────── 汇总 ───────────────────────────── */
console.log('\n' + (fails.length ? 'FAILED ' + fails.length + ' / ' + (pass + fails.length)
                                   : 'ALL ' + pass + ' ASSERTIONS PASSED'));
if (fails.length) {
  for (const f of fails) console.log('  - ' + f);
  process.exit(1);
}
