#!/usr/bin/env node
/**
 * F-09 · file= 参数守卫自检（issue：headless 静默成功）—— 离线，不需要 Blender / 后端。
 *
 * 钉住的行为（旧缺陷签名：file 缺失/空串/参数名误写 → ok=true / exit 0 / inputFile=null / 零警告 / 空场景）：
 *  ① validateFileOpt：''/空白 → throw file-empty；非字符串 → throw file-type；单元素字符串数组保留旧便利；null/undefined → 未传
 *  ② detectFileAlias：blend/path/filepath 等误写在**工具端**点名拦截（引擎侧守卫对工具路径永不触发）
 *  ③ describeMissingInput：「没给档」与「给了 .py」在回执里可区分（given/opened/reason）
 *  ④ noInputFileWarning：「成功但没开档」必须有显式警告
 *  ⑤ staging 脚本名 / stdout-stderr log 名：同毫秒零碰撞（issue A1/A2）
 *  ⑥ 接线静态断言：engine/index 两侧调用点在位（防"模块在、没人调"的假修复）
 *
 * 风格与 tests/windows_hide_selftest.mjs 同类：纯静态 + 纯函数，`npm test` 主链内跑。
 */
import path from 'node:path';
import fs from 'node:fs';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { FILE_ALIAS_KEYS, detectFileAlias, validateFileOpt, describeMissingInput, noInputFileWarning, headlessLogNames } from '../runtime/file_guard.mjs';
import { headlessScriptName } from '../runtime/staging.mjs';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.join(HERE, '..');
let pass = 0; let fail = 0; const fails = [];
const ok = (name, cond, extra) => { if (cond) pass++; else { fail++; fails.push({ clause: name, detail: extra }); } };
const throwsWith = (fn, code) => { try { fn(); return '<no-throw>'; } catch (e) { return (e && e.code) || ('plain:' + String((e && e.message) || e)); } };

// ---- ① validateFileOpt ----
ok('空串 → throw file-empty', throwsWith(() => validateFileOpt(''), 'file-empty') === 'file-empty');
ok('纯空白 → throw file-empty', throwsWith(() => validateFileOpt('   '), 'file-empty') === 'file-empty');
ok('空串错误带 hint（指路 file/不传）', (() => { try { validateFileOpt(''); } catch (e) { return typeof e.hint === 'string' && e.hint.includes('file'); } return false; })());
ok('数字 → throw file-type', throwsWith(() => validateFileOpt(123), 'file-type') === 'file-type');
ok('对象 {path} → throw file-type', throwsWith(() => validateFileOpt({ path: 'x.blend' }), 'file-type') === 'file-type');
ok('双元素数组 → throw file-type', throwsWith(() => validateFileOpt(['a.blend', 'b.blend']), 'file-type') === 'file-type');
ok('单元素字符串数组 → 取元素（保留 String([p])===p 旧便利）', validateFileOpt(['x.blend']) === 'x.blend');
ok('null → 未传（undefined）', validateFileOpt(null) === undefined);
ok('undefined → 未传（undefined）', validateFileOpt(undefined) === undefined);
ok('正常路径原样返回（不 trim 不改写）', validateFileOpt(' D:\\x\\y.blend ') === ' D:\\x\\y.blend ');
ok('中文+空格路径原样返回', validateFileOpt('D:\\档 案\\y.blend') === 'D:\\档 案\\y.blend');

// ---- ② detectFileAlias（工具端唯一防线） ----
ok('无 args → []', detectFileAlias(null).length === 0);
ok('只有 file → []', detectFileAlias({ script: 'x', file: 'a.blend' }).length === 0);
ok('blend= 误写 → 点名 blend', JSON.stringify(detectFileAlias({ blend: 'a.blend' })) === '["blend"]');
ok('path/filepath 同时误写 → 两个都点出', detectFileAlias({ path: 'a', filepath: 'b' }).length === 2);
ok('FILE_ALIAS_KEYS 覆盖 issue 列举的六个误写', ['blend', 'blendFile', 'path', 'filepath', 'blend_file', 'input'].every((k) => FILE_ALIAS_KEYS.includes(k)));
ok('显式 file:undefined 不算误写', detectFileAlias({ file: 'a.blend', blend: undefined }).length === 0);

// ---- ③ describeMissingInput ----
ok('未传 → given:false opened:false', (() => { const d = describeMissingInput(undefined, false); return d.given === false && d.opened === false && typeof d.reason === 'string'; })());
ok('.py → given:true opened:false 且 reason 说明 .py', (() => { const d = describeMissingInput('x.py', true); return d.given === true && d.opened === false && d.reason.includes('.py'); })());
ok('两态可区分（given 维度）', describeMissingInput(undefined, false).given !== describeMissingInput('x.py', true).given);

// ---- ④ noInputFileWarning ----
ok('警告带 code=no_input_file', noInputFileWarning(false).code === 'no_input_file');
ok('警告 hint 点出正名 file', noInputFileWarning(false).hint.includes('file'));
ok('.py 变体 hint 说明 .py 当脚本', noInputFileWarning(true).hint.includes('.py'));

// ---- ⑤ 撞名（issue A1/A2） ----
const names = new Set(); for (let i = 0; i < 5000; i++) names.add(headlessScriptName());
ok('headlessScriptName 5000 次全不撞（旧实现同毫秒 200 次只剩 ~97 个）', names.size === 5000);
ok('脚本名格式 dsh_headless_<ms36>_<seq+rand>.py', /^dsh_headless_[0-9a-z]+_[0-9a-z]+\.py$/.test(headlessScriptName()));
const l1 = headlessLogNames('2026-10-24T00-00-00-000Z', 'run-aaaa1111');
const l2 = headlessLogNames('2026-10-24T00-00-00-000Z', 'run-bbbb2222');
ok('同毫秒不同 runId → log 名互不相同', l1.stdout !== l2.stdout && l1.stderr !== l2.stderr && l1.stdout !== l1.stderr);
ok('log 名含 stamp 与 runId（人工可 glob）', l1.stdout.includes('2026-10-24T00-00-00-000Z') && l1.stdout.includes('run-aaaa1111') && l1.stdout.endsWith('.stdout.log'));
ok('runId 里的非法字符被中和（无路径注入）', !headlessLogNames('s', 'run-../../evil').stdout.includes('../'));

// ---- ⑥ 接线静态断言（防"模块在、没人调"的假修复） ----
const engineSrc = fs.readFileSync(path.join(ROOT, 'runtime', 'engine.mjs'), 'utf8');
const stagingSrc = fs.readFileSync(path.join(ROOT, 'runtime', 'staging.mjs'), 'utf8');
const indexSrc = fs.readFileSync(path.join(ROOT, 'src', 'index.ts'), 'utf8');
ok('engine 导入 file_guard', engineSrc.includes("from './file_guard.mjs'"));
ok('engine 两条路径都过 validateFileOpt（job+sync 各一处）', (engineSrc.match(/validateFileOpt\(opts\.file\)/g) || []).length === 2);
ok('engine 两条路径都用 describeMissingInput', (engineSrc.match(/describeMissingInput\(fileOpt, fileLooksPython\)/g) || []).length === 2);
ok('engine 台账记 file（三处：job running / job summary / headless 终态）+ sync running', (engineSrc.match(/file: \(fileOpt === undefined \? null : String\(fileOpt\)\)/g) || []).length >= 3);
ok('engine 台账终态记 inputFile', engineSrc.includes('file: (fileOpt === undefined ? null : String(fileOpt)), inputFile: inputFileInfo'));
ok('engine 两条路径都挂 no_input_file 警告', (engineSrc.match(/noInputFileWarning\(fileLooksPython\)/g) || []).length === 2);
ok('engine log 名走 headlessLogNames（主+兜底两处，旧毫秒裸档名清零）', (engineSrc.match(/headlessLogNames\(stamp, runId\)/g) || []).length === 2
   && !/'dsh_headless_' \+ stamp \+ '\.stdout\.log'/.test(engineSrc));
ok('staging 用 headlessScriptName（旧 Date.now 裸档名清除）', stagingSrc.includes('headlessScriptName()') && !stagingSrc.includes("Date.now().toString(36) + '.py'"));
ok('工具端 rt_headless 拦 ALIAS + 空串', indexSrc.includes('detectFileAlias(args)') && indexSrc.includes('file= 传了空字符串'));
ok('工具端不再用 (args && args.file) || undefined 剥空串', !indexSrc.includes("file: (args && args.file) || undefined"));

// ---- ⑦ 评审修正钉桩（渲染卡警告 / 显式 null 不误伤 / 空串防御直调） ----
ok('渲染卡渲染 r.warnings（重大-1：no_input_file 必须出现在模型第一眼读的卡片，不只结构化 JSON）',
  /Array\.isArray\(r\.warnings\)/.test(indexSrc) && indexSrc.includes("'⚠️ warnings（'"));
ok('别名键显式 null 不误伤（次要-2：blend:null 是"没有 blend"的显式声明，不是误写）',
  detectFileAlias({ blend: null, script: 'x' }).length === 0);
ok('describeMissingInput 空串分支可直调（次要-1：经引擎流被 validateFileOpt 先行 throw，属防御性；单测直接覆盖该分支语义）',
  describeMissingInput('', false).reason.includes('为空') && describeMissingInput('', false).given === true);

console.log('file-guard 自检：' + pass + ' 通过 / ' + fail + ' 失败');
if (fail) for (const f of fails) console.log('  x ' + f.clause + (f.detail !== undefined ? ' — ' + JSON.stringify(f.detail) : ''));
process.exit(fail ? 1 : 0);
