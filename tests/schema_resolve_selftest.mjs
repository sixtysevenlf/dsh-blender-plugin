#!/usr/bin/env node
/**
 * schema_resolve_selftest —— issue #10 的回归自检（不需要 Blender，也不需要真宿主）
 *
 * 被复现的失效模式：插件在**模块载入期**解析 schemastery 失败 → 顶层 throw →
 *   宿主只回一句 `failed to import`（真因被吞）、entry 的 fiber 始终 undefined →
 *   15 个工具整体消失、后端也不起（现象与真因不在同一层）。
 *
 * 本自检把"宿主的 node_modules 里到底有哪个名字"变成**可造的 fixture**：每个 fixture 都是
 * 一份真的 lib/index.js 拷贝（lib/ + runtime/backend_probe.mjs），只换它上方 node_modules
 * 里的可解析名字，然后在**子进程里真 import() 一次**：
 *   A 只给带 scope 的 @deepseek-ai/schemastery → 必须 import 成功，且走 esm: 通道
 *   B 只给无 scope 的 schemastery             → 必须 import 成功
 *   C 两个都不给                              → 必须**不抛**；降级为诊断占位（Config=undefined，
 *                                               apply() 注册的占位工具直接回报原因与修法）
 * 外加 D：本包真身（真 node_modules）也必须 import 成功并给出 via。
 * 外加 E：specifier 形态的回归保护 —— Windows 盘符裸路径（C:\…）不得再撞
 *         ERR_UNSUPPORTED_ESM_URL_SCHEME（issue #10 跟进项；这条在任何平台都能跑）。
 */
import { mkdtempSync, mkdirSync, writeFileSync, cpSync, rmSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { execFileSync } from 'node:child_process'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const PKG = path.resolve(HERE, '..')
let fails = 0
const ok = (name, cond, extra = '') => {
  console.log((cond ? '  ✓ ' : '  ✗ ') + name + (cond ? '' : ('   ' + extra)))
  if (!cond) fails++
}

/** 最小 schemastery 替身：只要能被 .object()/.natural()/.boolean().default() 串起来即可 */
const SHIM_Z = [
  'const chain = () => { const o = {}; o.default = () => o; o.object = () => o; o.natural = () => o; o.boolean = () => o; o.string = () => o; return o };',
  'export const object = () => chain();',
  'export const natural = () => chain();',
  'export const boolean = () => chain();',
  'export const string = () => chain();',
  'export function Schema() {}',
  'export default { object, natural, boolean, string, Schema };',
  '',
].join('\n')
/** 最小 dsh-tools 替身：defineTool 原样返回 spec 就够（lib 只用到它） */
const SHIM_TOOLS = 'export const defineTool = (spec) => spec;\n'

function fixture(root, name, smNames) {
  const dir = path.join(root, name)
  mkdirSync(dir, { recursive: true })
  cpSync(path.join(PKG, 'lib'), path.join(dir, 'lib'), { recursive: true })
  mkdirSync(path.join(dir, 'runtime'), { recursive: true })
  cpSync(path.join(PKG, 'runtime', 'backend_probe.mjs'), path.join(dir, 'runtime', 'backend_probe.mjs'))
  writeFileSync(path.join(dir, 'package.json'), JSON.stringify({ name: 'fixture-' + name, type: 'module' }), 'utf8')
  const nm = path.join(dir, 'node_modules')
  const tools = path.join(nm, '@deepseek-ai', 'dsh-tools')
  mkdirSync(tools, { recursive: true })
  writeFileSync(path.join(tools, 'package.json'), JSON.stringify({ name: '@deepseek-ai/dsh-tools', version: '0.0.0-fixture', type: 'module', main: 'index.js' }), 'utf8')
  writeFileSync(path.join(tools, 'index.js'), SHIM_TOOLS, 'utf8')
  for (const t of smNames) {
    const d = t.scoped ? path.join(nm, '@deepseek-ai', 'schemastery') : path.join(nm, 'schemastery')
    mkdirSync(d, { recursive: true })
    writeFileSync(path.join(d, 'package.json'), JSON.stringify({ name: t.scoped ? '@deepseek-ai/schemastery' : 'schemastery', version: '0.0.0-fixture', type: 'module', main: 'index.js' }), 'utf8')
    writeFileSync(path.join(d, 'index.js'), SHIM_Z, 'utf8')
  }
  return path.join(dir, 'lib', 'index.js')
}

const PROBE = [
  "import { pathToFileURL } from 'node:url';",
  // 子进程只拿到一个 specifier：file:// URL 直接用，裸路径自己转 URL。
  // 裸路径在 Windows 上是 `C:\…`，import() 会把它解析成 scheme 'c:' ⇒
  // ERR_UNSUPPORTED_ESM_URL_SCHEME（issue #10 的跟进项：本文件 A/B/C 三组因此在 Windows 上全 FAIL）。
  'const spec = process.argv[1];',
  "const mod = await import(spec.startsWith('file:') ? spec : pathToFileURL(spec).href);",
  'const r = (mod.__internals && mod.__internals.schemaResolve) || null;',
  'const out = { via: (r && r.via) || null, error: (r && r.error) || null, tried: (r && r.tried) || [],',
  '  hasConfig: mod.Config !== undefined, tools: [], placeholder: null };',
  'if (!out.via) {',
  '  const captured = [];',
  '  const ctx = { effect: (fn) => { fn(); }, tools: { register: (t) => { captured.push(t); return t; } } };',
  '  mod.apply(ctx, {});',
  '  out.tools = captured.map((t) => t.name);',
  '  if (captured.length) out.placeholder = String(await captured[0].execute({}, {}));',
  '}',
  "console.log('FIXTURE_JSON ' + JSON.stringify(out));",
  '',
].join('\n')

/** 原样投喂 specifier（用来验证子进程对不同形态 specifier 的鲁棒性） */
function probeSpec(spec) {
  let out
  try {
    out = execFileSync(process.execPath, ['--input-type=module', '-e', PROBE, spec], { encoding: 'utf8' })
  } catch (e) {
    const tail = String((e && e.stderr) || (e && e.message) || e).trim().split('\n').slice(-6).join(' | ')
    return { threw: tail.slice(0, 500) }
  }
  const line = out.split('\n').find((l) => l.startsWith('FIXTURE_JSON '))
  if (!line) return { threw: 'fixture 没回 JSON：' + out.slice(-300) }
  return JSON.parse(line.slice('FIXTURE_JSON '.length))
}

/** 父进程一律传 file:// URL —— 裸 Windows 路径（C:\…）会被 import() 当成 scheme 'c:' */
function probe(entry) {
  return probeSpec(pathToFileURL(entry).href)
}

const root = mkdtempSync(path.join(os.tmpdir(), 'dsh-sm-resolve-'))
try {
  console.log('== A：只提供带 scope 的 @deepseek-ai/schemastery ==')
  const a = probe(fixture(root, 'scoped-only', [{ scoped: true }]))
  ok('import 成功（不再顶层 throw）', !a.threw && !!a.via, a.threw || JSON.stringify(a))
  ok('走 ESM 通道（esm: 优先）', String(a.via || '').startsWith('esm:@deepseek-ai/schemastery'), String(a.via))
  ok('Config schema 正常构建', a.hasConfig === true, JSON.stringify(a))

  console.log('== B：只提供无 scope 的 schemastery ==')
  const b = probe(fixture(root, 'unscoped-only', [{ scoped: false }]))
  ok('import 成功', !b.threw && !!b.via, b.threw || JSON.stringify(b))
  ok('解析到 schemastery', String(b.via || '').endsWith('schemastery'), String(b.via))
  ok('Config schema 正常构建', b.hasConfig === true, JSON.stringify(b))

  console.log('== C：两个名字都没有（旧版在这里整体消失）==')
  const c = probe(fixture(root, 'none', []))
  ok('import 仍然成功（不再抛）', !c.threw && c.via === null && !!c.error, c.threw || JSON.stringify(c))
  ok('Config 为 undefined（不给宿主二次抛错的机会）', c.hasConfig === false, JSON.stringify(c))
  ok('注册的是诊断占位工具', Array.isArray(c.tools) && c.tools.length === 1 && c.tools[0] === 'blender_viewport', JSON.stringify(c.tools))
  ok('占位工具直接回报原因与修法', !!c.placeholder && /schemastery/.test(c.placeholder) && /修法/.test(c.placeholder))
  ok('tried 里两条通道都留了痕', Array.isArray(c.tried) && c.tried.length >= 2, JSON.stringify(c.tried))

  console.log('== D：本包真身（真 node_modules）==')
  const real = await import(pathToFileURL(path.join(PKG, 'lib', 'index.js')).href)
  const rr = real.__internals && real.__internals.schemaResolve
  ok('lib/index.js 在本机可 import', typeof real.apply === 'function')
  ok('__internals 暴露 schemaResolve', !!(rr && Array.isArray(rr.tried)))
  ok('真环境下解析成功（有 via）', !!(rr && rr.via), JSON.stringify(rr))

  console.log('== E：Windows 盘符路径 / 裸路径 specifier（issue #10 跟进项 · 任何平台可跑）==')
  // 实测：import('C:\…') 在**任何平台**都被解析成 scheme 'c:' ⇒ ERR_UNSUPPORTED_ESM_URL_SCHEME。
  // 子进程现在自己把裸路径转 file:// ⇒ 至少已越过 URL scheme 层。
  // 这条在任何平台都能跑，所以 Windows 的失效模式在这里就有回归保护（不必真去借一台 Windows）。
  const win = probeSpec('C:\\dsh\\no-such-dir\\lib\\index.js')
  ok('Windows 风格裸路径 → 不再是 ERR_UNSUPPORTED_ESM_URL_SCHEME',
    !/ERR_UNSUPPORTED_ESM_URL_SCHEME/.test(String(win.threw || '')), win.threw)
  ok('…已越过 URL scheme 层（POSIX 上被反斜杠编码拦下 = ERR_INVALID_MODULE_SPECIFIER）',
    /ERR_INVALID_MODULE_SPECIFIER|Cannot find|ENOENT|MODULE_NOT_FOUND/.test(String(win.threw || '')), win.threw)
  // 本平台形态的"不存在的文件"：Windows 用盘符路径、POSIX 用绝对路径 —— 期望都是文件系统层的 ENOENT
  const missing = process.platform === 'win32'
    ? 'C:\\dsh\\no-such-dir\\lib\\index.js'
    : path.join(root, 'no-such-dir', 'lib', 'index.js')
  const miss = probeSpec(missing)
  ok('不存在的文件 → 走到文件系统层（Cannot find module / ENOENT）',
    /Cannot find|ENOENT|MODULE_NOT_FOUND/.test(String(miss.threw || '')), miss.threw)
  const bare = probeSpec(fixture(root, 'bare-path', [{ scoped: true }]))
  ok('裸路径 specifier 也能载入（子进程自转 file://）', !bare.threw && !!bare.via, bare.threw || JSON.stringify(bare))
} finally {
  rmSync(root, { recursive: true, force: true })
}

console.log('')
console.log(fails ? ('schema-resolve-selftest：' + fails + ' 项 FAIL') : 'schema-resolve-selftest：ALL PASS')
process.exit(fails ? 1 : 0)
