#!/usr/bin/env node
/**
 * F-14 · 无头进程硬退出自检 —— 离线，不需要 Blender / 后端。
 *
 * 钉住的行为（issue：Blender 5.2.3 退出期 AV 在 done 之后发生，结果其实已拿到）：
 *  ① 默认（opts 缺省 / 非 false）：done 之后依次是 sys.stdout/stderr 冲刷 → os._exit(0)
 *  ② 顺序即契约：冲刷必须在 _exit 之前（反过来 = 丢输出）；三行必须在 done 之后（done 前硬退出 = 截断结果）
 *  ③ hardExit=false → 两条路径都零改动（旧行为逐字保留）
 *  ④ Python 行与 issue 定稿片段逐字一致（防"等价改写"漂移）
 *  ⑤ 接线静态断言：engine 两条装配路径的调用点在位、位置正确（job 在 writeHeadlessScript 之前；
 *     sync 在 body 收尾之后、`if (body)` 守卫之内），body 已从 const 改 let
 *
 * 风格同 windows_hide_selftest：纯函数 + 源码断言，`npm test` 主链内跑。
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { hardExitJobParts, hardExitSyncTail } from '../runtime/hard_exit.mjs';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.join(HERE, '..');
let pass = 0; let fail = 0; const fails = [];
const ok = (name, cond, extra) => { if (cond) pass++; else { fail++; fails.push({ clause: name, detail: extra }); } };

// ---- ① + ④ 默认开启、与 issue 定稿逐字一致 ----
const DEF = ['import sys as _dsh_sys', '_dsh_sys.stdout.flush(); _dsh_sys.stderr.flush()', 'import os as _dsh_os; _dsh_os._exit(0)'];
ok('缺省 opts → 三行硬退出（默认开）', JSON.stringify(hardExitJobParts()) === JSON.stringify(DEF));
ok('opts={} → 三行', JSON.stringify(hardExitJobParts({})) === JSON.stringify(DEF));
ok('hardExit:true → 三行', JSON.stringify(hardExitJobParts({ hardExit: true })) === JSON.stringify(DEF));
ok('hardExit:undefined → 三行（!== false 语义）', JSON.stringify(hardExitJobParts({ hardExit: undefined })) === JSON.stringify(DEF));

// ---- ③ 显式关闭 = 旧行为零改动 ----
ok('hardExit:false → job 零行', hardExitJobParts({ hardExit: false }).length === 0);
ok('hardExit:false → sync 空尾', hardExitSyncTail({ hardExit: false }, '\n') === '');

// ---- ② 顺序即契约（在 done 之后的完整拼装里核对） ----
const NL = String.fromCharCode(10);
const jobScript = ['_dsh_stage_emit("done")'].concat(hardExitJobParts({})).join(NL);
const iDone = jobScript.indexOf('_dsh_stage_emit("done")');
const iFlush = jobScript.indexOf('_dsh_sys.stdout.flush(); _dsh_sys.stderr.flush()');
const iExit = jobScript.indexOf('_dsh_os._exit(0)');
ok('job：done < flush < _exit（顺序契约）', iDone >= 0 && iDone < iFlush && iFlush < iExit, { iDone, iFlush, iExit });
const syncBody = 'USER_SCRIPT' + NL + '_dsh_stage_emit("done")' + NL + hardExitSyncTail({}, NL);
ok('sync：done 之后依次 flush、_exit', syncBody.indexOf('_dsh_stage_emit("done")') < syncBody.indexOf('_dsh_sys.stdout.flush()')
   && syncBody.indexOf('_dsh_sys.stdout.flush()') < syncBody.indexOf('_dsh_os._exit(0)'));
ok('sync：尾巴以 NL 开头（不与 done 行粘连）', hardExitSyncTail({}, NL).startsWith(NL) && !hardExitSyncTail({}, NL).endsWith(NL));
ok('sync：false 时 body 逐字不变（零改动）', ('OLD' + hardExitSyncTail({ hardExit: false }, NL)) === 'OLD');

// ---- ⑤ 接线静态断言（防"模块在、没人调"的假修复） ----
const eng = fs.readFileSync(path.join(ROOT, 'runtime', 'engine.mjs'), 'utf8');
ok('engine 导入 hard_exit', eng.includes("from './hard_exit.mjs'"));
ok('job 路径：done push 之后、写脚本之前挂硬退出', /_dsh_stage_emit\("done"\)'\);\s*(\/\/[^\n]*\n\s*)?for \(const hx of hardExitJobParts\(opts\)\) parts\.push\(hx\);\s*const sp = writeHeadlessScript/.test(eng));
ok('sync 路径：body 收尾之后挂硬退出（if (body) 守卫内）', /body \+= hardExitSyncTail\(opts, NL\)/.test(eng));
ok('sync body 已从 const 改 let（否则 += 抛 TypeError）', /let body = \(script \|\| pre \|\| gpuPre \|\| shotsPre\)/.test(eng));
ok('sync：硬退出在 args=[\'-b\'] 之前（先拼完再装配命令行）', eng.indexOf('body += hardExitSyncTail(opts, NL)') < eng.indexOf("const args = ['-b'];", eng.indexOf('body += hardExitSyncTail')));

// ---- ⑥ 工具端接线（重大-2：模型侧必须可关，且不得静默丢参） ----
// 注：hard_exit 只暴露在 blender_rt_headless（schema 预算 12000 字卡死在基线，job 路径经 as_job=true
//     走 headless 工具同样带 hardExit；直连 /job 亦可传 opts.hardExit —— server 整包转发）。
const ts = fs.readFileSync(path.join(ROOT, 'src', 'index.ts'), 'utf8');
ok('schema：headless 暴露 hard_exit(boolean)', (ts.match(/hard_exit: \{ type: 'boolean'/g) || []).length === 1);
ok('schema 描述含"默认 true"与 false 语义（可关性写进模型可见面）',
  /hard_exit: \{ type: 'boolean', description: '[^']*默认 true[^']*false[^']*atexit/.test(ts));
ok('headless body 转发 hard_exit → hardExit（boolean 透传，非真值化）',
  ts.includes("hardExit: (args && typeof args.hard_exit === 'boolean') ? args.hard_exit : undefined,"));

console.log('hard-exit 自检：' + pass + ' 通过 / ' + fail + ' 失败');
if (fail) for (const f of fails) console.log('  x ' + f.clause + (f.detail !== undefined ? ' — ' + JSON.stringify(f.detail) : ''));
process.exit(fail ? 1 : 0);
