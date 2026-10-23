/**
 * S8 · 无头脚本暂存（从 engine.mjs 外移）—— 让 launch.mjs 也能用它（S3-c 搬 launchBlender 时漏了这个依赖）。
 *
 * 只管「Windows 侧看不看得见」；后端**自身** status/ledger 的可写性另见 server 侧 ensureWritableWorkDir。
 */
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { wslPathShared, wslToWin } from './config.mjs';
import { WIN_TMP, WSL_TMP, HERE, blenderJoin } from './paths.mjs';


/**
 * 把无头脚本落到一个**两端都能读**的位置（v0.9.6 D3 起带共享性自证）。
 *
 * 现场踩到：workDir 落在 WSL 的独立挂载（如 /tmp tmpfs）时，Node 侧写成功、Windows 的 blender.exe
 * 却打不开 → `OSError: Python file "\\wsl.localhost\Ubuntu\tmp\…\dsh_headless_*.py" could not be opened`，
 * 回执 resultJson=null，被误读成"租约/通道坏了"。所以这里不讲"能不能写"（Node 永远说能），
 * 只讲**Windows 侧看不看得见**（wslPathShared，读挂载表），并优先选一个共享目录。
 * 返回 {wsl, win, staging}；staging 会原样进回执（替换了目录就必须说清，不许静默）。
 */
/**
 * 无头脚本文件名（issue A1：旧名只到毫秒 → 同毫秒并发两个 run 共写同一个 .py，
 * 一方脚本被另一方覆写——"跑的不是我给的那份"这类难查症状的根）。
 * 唯一性 = 毫秒 + 进程内单调序号（同进程零碰撞）+ 4 位随机（跨进程防撞）。
 * 纯命名逻辑，tests/headless_file_guard_selftest.mjs 直接对它做 5000 次碰撞测试。
 */
let _nameSeq = 0;
export function headlessScriptName() {
  _nameSeq = (_nameSeq + 1) % 0x1000000;
  const rand = Math.random().toString(36).slice(2).padEnd(4, '0').slice(0, 4);
  return 'dsh_headless_' + Date.now().toString(36) + '_' + _nameSeq.toString(36) + rand + '.py';
}

export function writeHeadlessScript(code) {
  const name = headlessScriptName();
  const requested = WSL_TMP;
  const reqShared = wslPathShared(requested);
  // 候选：请求的 workDir（判为不可共享时降级）→ 用户 home（发行版根文件系统，Windows 可见）→ 包内 tmp
  const wanted = { dir: requested, win: blenderJoin(WIN_TMP, name), label: 'workDir' };
  const homeTmp = { dir: path.join(os.homedir(), '.dsh', 'dsh-blender-rt'), win: null, label: 'fallback:home' };
  const pkgTmp = { dir: path.join(HERE, 'tmp'), win: null, label: 'fallback:pkg' };
  const cands = (reqShared.shared === false) ? [homeTmp, pkgTmp, wanted] : [wanted, homeTmp, pkgTmp];
  const errs = [];
  const tryWrite = (c) => {
    fs.mkdirSync(c.dir, { recursive: true });
    const wsl = path.join(c.dir, name);
    fs.writeFileSync(wsl, code, 'utf8');
    const sh = wslPathShared(c.dir);
    const substituted = path.resolve(c.dir) !== path.resolve(requested);
    return { wsl: wsl, win: c.win || wslToWin(wsl),
             staging: { requested: requested, requestedShared: reqShared.shared, requestedWhy: reqShared.why,
                        used: c.dir, usedWin: c.win || wslToWin(wsl), usedShared: sh.shared, usedWhy: sh.why,
                        label: c.label, substituted: substituted,
                        note: substituted
                          ? ('workDir ' + requested + ' 在 Windows 侧不保证可见（' + reqShared.why + '）→ 本次脚本改落 '
                             + c.dir + '（shared=' + String(sh.shared) + '，' + sh.why + '）；无头脚本必须放在 Blender 读得到的地方')
                          : 'workDir 共享性检查通过（' + sh.why + '）' } };
  };
  for (const c of cands) {
    if (wslPathShared(c.dir).shared === false) { errs.push(c.label + ' ' + c.dir + '：Windows 侧不保证可见，跳过'); continue; }
    try { return tryWrite(c); } catch (e) { errs.push(c.dir + ': ' + String((e && e.message) || e)); }
  }
  // 兜底：所有共享候选都写不了 → 宁可写进（可能不共享的）workDir，也不要静默无脚本
  for (const c of cands) {
    try { const r = tryWrite(c); r.staging.degraded = true;
          r.staging.note += ' ⚠ 这是**降级**路径：共享候选全部写失败，脚本可能被 Windows 侧 Blender 读不到。'; return r; }
    catch (e) { errs.push(c.dir + ': ' + String((e && e.message) || e)); }
  }
  throw new Error('无法写无头脚本：' + errs.join(' · '));
}
