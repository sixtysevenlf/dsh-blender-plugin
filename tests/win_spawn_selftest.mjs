#!/usr/bin/env node
/**
 * tests/win_spawn_selftest.mjs —— issue #51 的回归自检（离线；不需要 Windows / Blender / 后端）。
 *
 * 钉住三件事（旧缺陷签名：dsh-desktop-host 每 15 s 被打死一次；win32 上 stop/restart 后残留后端清不掉）：
 *  ① `spawnChild`（src/index.ts）：显式 `ELECTRON_RUN_AS_NODE=1` + `windowsHide: true`，
 *     且 `child.on('error')` 必须**先于** `unref()` —— 异步 EPERM/EACCES 不能在宿主里冒成 uncaughtException；
 *  ② win32 清理接线：`killBackendByCmd`（host）与 `samePortHolders`（后端 EADDRINUSE 自愈）都要走 win_proc.mjs，
 *     而且 win32 分支要**排在** /proc 探测之前（否则先读 /proc 抛错再兜底也行，但顺序写反就等于没接上）；
 *  ③ win_proc 的纯解析器：netstat LISTENING 精确端口 + tasklist CSV 映像名（真实样例夹具，无需 Windows）。
 *
 * 风格同 tests/windows_hide_selftest.mjs：静态 + 纯函数，`npm test` 主链内跑。
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { IS_WIN, parseNetstatListeners, parseTasklistImage, winSamePortHolders, winKillPids } from '../runtime/win_proc.mjs';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.join(HERE, '..');
let pass = 0;
let fail = 0;
const fails = [];
const ok = (name, cond, extra) => { if (cond) pass++; else { fail++; fails.push({ clause: name, detail: extra }); } };

/** 读源文件并把换行统一成 LF（本仓工作区是 CRLF 检出，core.autocrlf=true）。 */
const read = (rel) => fs.readFileSync(path.join(ROOT, rel), 'utf8').replace(/\r\n/g, '\n');

const indexSrc = read('src/index.ts');
const serverSrc = read('runtime/server.mjs');
const catalogSrc = read('runtime/catalog.mjs');
const winProcSrc = read('runtime/win_proc.mjs');
const pkg = JSON.parse(fs.readFileSync(path.join(ROOT, 'package.json'), 'utf8'));

/** 取一个顶层函数体（从签名到行首收尾的 '}'；内层块的 '}' 都有缩进，不会提前截断）。 */
function fnBody(src, signature) {
  const i = src.indexOf(signature);
  if (i < 0) return '';
  const m = /\n\}\n/.exec(src.slice(i));
  return m ? src.slice(i, i + m.index + 3) : '';
}

// ---- ① spawnChild：env / windowsHide / error 监听顺序 ----
const spawnBody = fnBody(indexSrc, 'function spawnChild(');
ok('spawnChild 存在', spawnBody.length > 0);
ok('spawnChild 显式 ELECTRON_RUN_AS_NODE=1（宿主跑在 Electron 里时 execPath 是 GUI 可执行体）',
  spawnBody.includes('ELECTRON_RUN_AS_NODE'), spawnBody.includes('ELECTRON_RUN_AS_NODE'));
ok('spawnChild 带 windowsHide: true', /windowsHide\s*:\s*true/.test(spawnBody));
ok("spawnChild 挂 child.on('error')（否则异步 EPERM 冒成宿主 uncaughtException）", spawnBody.includes("on('error'"));
ok('error 监听在 unref() 之前挂上（unref 后的 error 同样会冒泡，顺序必须是先挂后 unref）',
  spawnBody.indexOf("on('error'") >= 0 && spawnBody.indexOf("on('error'") < spawnBody.indexOf('.unref()'));
ok('error 回调里把 child 句柄置空（失败降级后看护能重新拉起）', /child\s*=\s*null/.test(spawnBody));
ok('spawnChild 不再把 detached/stdio 写在单行对象里（保持可读的多行 options）',
  spawnBody.includes('detached: true') && spawnBody.includes("stdio: 'ignore'"));

// ---- ② win32 清理接线 ----
ok('src/index.ts 从 ../runtime/win_proc.mjs 引入清理原语',
  indexSrc.includes("from '../runtime/win_proc.mjs'") && indexSrc.includes('winSamePortHolders') && indexSrc.includes('winKillPids'));
const killBody = fnBody(indexSrc, 'function killBackendByCmd(');
ok('killBackendByCmd 有 IS_WIN 分支', killBody.includes('IS_WIN'));
ok('killBackendByCmd 的 win32 分支在 /proc 探测之前返回（不再先跑必失败的两条 POSIX 链）',
  killBody.indexOf('IS_WIN') >= 0 && killBody.indexOf('IS_WIN') < killBody.indexOf("readdirSync('/proc')"));
ok('killBackendByCmd win32 分支走 winSamePortHolders + winKillPids',
  killBody.includes('winSamePortHolders(port)') && killBody.includes('winKillPids('));
ok('killBackendByCmd 仍保留 POSIX 的 /proc + ps 路径（非 win32 行为不变）',
  killBody.includes("readdirSync('/proc')") && killBody.includes("execFileSync('ps'"));

ok('runtime/server.mjs 引入 win_proc', serverSrc.includes("from './win_proc.mjs'"));
const holdersBody = fnBody(serverSrc, 'function samePortHolders(');
ok('samePortHolders 有 win32 兜底分支', holdersBody.includes('winSamePortHolders(') && holdersBody.includes("process.platform === 'win32'"));
ok('samePortHolders 仍保留 /proc 主路径（Linux 行为不变）', holdersBody.includes("readdirSync('/proc')"));
const killHoldersBody = fnBody(serverSrc, 'function killHolders(');
ok('killHolders 在 win32 上走 taskkill（/T /F 连 headless 子进程一起收）', killHoldersBody.includes('winKillPids(') && killHoldersBody.includes("process.platform === 'win32'"));

ok('catalog.mjs 的干净 node 探测也带 ELECTRON_RUN_AS_NODE（#51 同族）', catalogSrc.includes('ELECTRON_RUN_AS_NODE'));

ok('win_proc 导出 winSamePortHolders / winKillPids（函数）',
  typeof winSamePortHolders === 'function' && typeof winKillPids === 'function');
ok('win_proc 的 IS_WIN 与本机平台一致', IS_WIN === (process.platform === 'win32'));
// 不在测试里真的去 taskkill / 扫 CIM：只做静态守卫断言（每个探测入口都必须有 !IS_WIN 早退）。
ok('win_proc 每个探测/杀伤入口都有 !IS_WIN 早退（非 Windows 恒空数组，绝不误杀）',
  (winProcSrc.match(/if \(!IS_WIN\) return/g) || []).length >= 5,
  (winProcSrc.match(/if \(!IS_WIN\) return/g) || []).length);

// ---- ③ netstat / tasklist 纯解析器（真实输出夹具） ----
const NETSTAT_ZH = [
  '活动连接',
  '',
  '  协议  本地地址          外部地址        状态           PID',
  '  TCP    0.0.0.0:9877           0.0.0.0:0              LISTENING       1234',
  '  TCP    127.0.0.1:9877         127.0.0.1:50013        ESTABLISHED     1234',
  '  TCP    [::]:9877              [::]:0                 LISTENING       5678',
  '  TCP    0.0.0.0:17860          0.0.0.0:0              LISTENING       9999',
  '  TCP    0.0.0.0:98770          0.0.0.0:0              LISTENING       7777',
  '  TCP    0.0.0.0:19877          0.0.0.0:0              LISTENING       8888',
].join('\r\n');
ok('netstat：只认 LISTENING（ESTABLISHED 不算占用者）', JSON.stringify(parseNetstatListeners(NETSTAT_ZH, 9877)) === '[1234,5678]');
ok('netstat：端口精确匹配（98770 / 19877 / 17860 不误命中）',
  parseNetstatListeners(NETSTAT_ZH, 9877).length === 2 && parseNetstatListeners(NETSTAT_ZH, 98770).length === 1);
ok('netstat：IPv6 [::]:port 也能解析', parseNetstatListeners('  TCP    [::]:9877              [::]:0                 LISTENING       5678', 9877)[0] === 5678);
ok('netstat：英文表头同构解析', parseNetstatListeners('  TCP    0.0.0.0:9877   0.0.0.0:0   LISTENING   4321', 9877)[0] === 4321);
ok('netstat：同 PID 多行去重', parseNetstatListeners('  TCP    0.0.0.0:9877  0.0.0.0:0  LISTENING  42\n  TCP  10.0.0.1:9877  10.0.0.2:1  LISTENING  42', 9877).length === 1);
ok('netstat：垃圾输入 / 空串 → []', parseNetstatListeners('', 9877).length === 0 && parseNetstatListeners('活动连接\r\n', 9877).length === 0 && parseNetstatListeners(null, 9877).length === 0);
ok('netstat：非法端口（0 / NaN / 负）→ []', parseNetstatListeners(NETSTAT_ZH, 0).length === 0 && parseNetstatListeners(NETSTAT_ZH, NaN).length === 0 && parseNetstatListeners(NETSTAT_ZH, -1).length === 0);

ok('tasklist：node.exe 映像名', parseTasklistImage('"node.exe","1234","Console","1","35,216 K"') === 'node.exe');
ok('tasklist：带空格的可执行体名（DeepSeek Harness.exe）', parseTasklistImage('"DeepSeek Harness.exe","4321","Console","1","120,000 K"') === 'DeepSeek Harness.exe');
ok('tasklist：无匹配（INFO 行）→ 空串，调用方据此保守跳过', parseTasklistImage('INFO: No tasks are running which match the specified criteria.') === '');
ok('tasklist：空串 → 空串', parseTasklistImage('') === '' && parseTasklistImage(null) === '');

// ---- ④ 接线完整性：npm test 主链必须包含本文件 ----
ok('package.json 的 test 链包含 win_spawn_selftest.mjs', String(pkg.scripts && pkg.scripts.test || '').includes('tests/win_spawn_selftest.mjs'));
ok('package.json 的 test 链仍包含 windows_hide_selftest.mjs（#38/#43 防线没被挤掉）',
  String(pkg.scripts && pkg.scripts.test || '').includes('tests/windows_hide_selftest.mjs'));

console.log('win-spawn 自检：' + pass + ' 通过 / ' + fail + ' 失败');
if (fail) for (const f of fails) console.log('  x ' + f.clause + (f.detail !== undefined && f.detail !== true ? ' — ' + JSON.stringify(f.detail) : ''));
process.exit(fail ? 1 : 0);
