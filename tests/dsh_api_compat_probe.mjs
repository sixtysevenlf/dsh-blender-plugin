#!/usr/bin/env node
/**
 * tests/dsh_api_compat_probe.mjs —— DSH 更新后的「工具契约」一键回归
 *
 * 干什么：把本包的 lib/index.js 挂到一个假 cordis ctx 上，用**指定版本**的
 *         @deepseek-ai/dsh-tools 走一遍 defineTool（参数/输出 schema 规范化 + 校验），
 *         断言 15 个工具全部注册、0 报错、15 个静态 timeoutMs 都在，
 *         并把每个工具投影后的参数 JSON Schema 写成基线 JSON（可 diff）。
 *
 * 为什么需要：工具是用 defineTool 声明的，而 defineTool 来自宿主的 @deepseek-ai/dsh-tools。
 *         DSH 一升级，这份契约就跟着变；失败方式是「fiber failed → 15 个工具全消失」。
 *
 * 用法：
 *   node tests/dsh_api_compat_probe.mjs                          # 用宿主当前的 dsh-tools
 *   node tests/dsh_api_compat_probe.mjs --version 0.1.7-alpha.2  # 下载该版本再测（npm/curl/tar + 网络）
 *   node tests/dsh_api_compat_probe.mjs --tools /path/to/dsh-tools
 *   node tests/dsh_api_compat_probe.mjs --json baseline.json     # 写基线（便于跨版本 diff）
 *   node tests/dsh_api_compat_probe.mjs --smoke                  # 额外真调一次 blender_viewport（需后端在跑）
 *
 * 退出码：0 = 通过；1 = 断言失败；2 = 环境问题。
 */
import { spawnSync } from 'node:child_process'
import { cpSync, existsSync, mkdirSync, readdirSync, rmSync, symlinkSync, writeFileSync } from 'node:fs'
import { homedir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))
const argv = process.argv.slice(2)
const arg = (name, dflt) => {
  const i = argv.indexOf(name)
  return i >= 0 && argv[i + 1] ? argv[i + 1] : dflt
}
const EXPECTED_TOOLS = 15
const EXPECTED_TIMEOUTS = 15
const HOST_CANDIDATES = [
  process.env.DSH_HOST_PKGS,
  '/usr/lib/node_modules/@deepseek-ai/dsh/node_modules/@deepseek-ai',
  join(homedir(), 'AppData/Roaming/npm/node_modules/@deepseek-ai/dsh/node_modules/@deepseek-ai'),
].filter(Boolean)
const hostPkgs = HOST_CANDIDATES.find((p) => existsSync(join(p, 'dsh-tools')))
if (!hostPkgs) {
  console.error('probe: 找不到宿主 @deepseek-ai 包目录（可用 DSH_HOST_PKGS 指定）')
  process.exit(2)
}
const pluginLib = resolve(arg('--plugin', join(HERE, '..', 'lib', 'index.js')))
if (!existsSync(pluginLib)) {
  console.error('probe: 找不到插件 lib：' + pluginLib + '（先 npm run build）')
  process.exit(2)
}

const root = resolve(arg('--root', join(HERE, '..', 'tmp', 'dsh-compat-probe')))
rmSync(root, { recursive: true, force: true })
mkdirSync(join(root, 'node_modules', '@deepseek-ai'), { recursive: true })
mkdirSync(join(root, 'plugin'), { recursive: true })
for (const name of readdirSync(hostPkgs)) {
  if (name === 'dsh-tools') continue
  try { symlinkSync(join(hostPkgs, name), join(root, 'node_modules', '@deepseek-ai', name), 'dir') } catch { /* 已存在 */ }
}
// 源码里 import z from 'schemastery'（裸名）；宿主实际用的是 @deepseek-ai/schemastery，
// 真实环境靠 vendor/schemastery 的目录名解析 —— 探针里同样按目录名建裸名链接。
for (const bare of ['schemastery', 'cordis']) {
  if (existsSync(join(hostPkgs, bare))) {
    try { symlinkSync(join(hostPkgs, bare), join(root, 'node_modules', bare), 'dir') } catch { /* 已存在 */ }
  }
}
cpSync(join(hostPkgs, 'dsh-tools'), join(root, 'node_modules', '@deepseek-ai', 'dsh-tools'), { recursive: true })

const toolsDir = arg('--tools', '')
const version = arg('--version', '')
if (toolsDir) {
  if (!existsSync(join(toolsDir, 'lib', 'index.js'))) { console.error('probe: --tools 里没有 lib/index.js'); process.exit(2) }
  rmSync(join(root, 'node_modules', '@deepseek-ai', 'dsh-tools'), { recursive: true, force: true })
  cpSync(toolsDir, join(root, 'node_modules', '@deepseek-ai', 'dsh-tools'), { recursive: true })
} else if (version) {
  // 本机 ~/.npm 落在只读 fs（EROFS）→ 给子进程一个可写的 npm 缓存，否则 --version 会静默取不到 URL
  const npmEnv = { ...process.env, npm_config_cache: join(root, '.npmcache') }
  const view = spawnSync('npm', ['view', '@deepseek-ai/dsh-tools@' + version, 'dist.tarball'], { encoding: 'utf8', env: npmEnv })
  const url = (view.stdout || '').trim().replace(/^'|'$/g, '')
  if (!url.startsWith('http')) { console.error('probe: 取不到 ' + version + ' 的 tarball URL（网络/npm 不可用？）'); process.exit(2) }
  const tgz = join(root, 'dsh-tools-' + version + '.tgz')
  if (spawnSync('curl', ['-sSL', '-o', tgz, url], { encoding: 'utf8' }).status !== 0) { console.error('probe: 下载失败 ' + url); process.exit(2) }
  rmSync(join(root, 'node_modules', '@deepseek-ai', 'dsh-tools'), { recursive: true, force: true })
  mkdirSync(join(root, 'node_modules', '@deepseek-ai', 'dsh-tools'), { recursive: true })
  if (spawnSync('tar', ['-xzf', tgz, '-C', join(root, 'node_modules', '@deepseek-ai', 'dsh-tools'), '--strip-components=1']).status !== 0) {
    console.error('probe: 解包失败'); process.exit(2)
  }
}
cpSync(pluginLib, join(root, 'plugin', 'index.mjs'))

const inner = join(root, 'inner.mjs')
writeFileSync(inner, `
const root = process.argv[2]
const mod = await import(root + '/plugin/index.mjs')
const regs = []; const errors = []
const ctx = {
  effect(fn, label) { try { const d = fn(); return typeof d === 'function' ? d : () => {} } catch (e) { errors.push(String(label) + ': ' + String(e && e.message)) } return () => {} },
  get() { return undefined }, on() {}, plugin() {},
  tools: { register(def) { regs.push(def); return () => {} } },
}
try { mod.apply(ctx, { port: 19999, autoStart: false }) } catch (e) { errors.push('apply: ' + String(e && e.message)) }
const { readFileSync } = await import('node:fs')
const ver = JSON.parse(readFileSync(root + '/node_modules/@deepseek-ai/dsh-tools/package.json', 'utf8')).version
const summary = regs.map((t) => ({
  name: t.name,
  descriptionPrefix: String(t.description || '').slice(0, 8),
  parameters: t.parameters,
  outputSchema: t.output && t.output.schema,
  hasRender: typeof (t.output && t.output.render) === 'function',
  hasExecute: typeof t.execute === 'function',
  timeoutMs: t.timeoutMs === undefined ? null : t.timeoutMs,
}))
console.log(JSON.stringify({ dshToolsVersion: ver, count: regs.length, errors, summary }))
process.exit(0)
`)

const run = spawnSync(process.execPath, [inner, root], { encoding: 'utf8' })
if (run.status !== 0) {
  console.error('probe: 内层执行失败\n' + (run.stderr || '').slice(0, 2000))
  process.exit(2)
}
let parsed
try { parsed = JSON.parse(run.stdout.trim().split('\n').pop()) } catch (e) {
  console.error('probe: 解析内层输出失败\n' + (run.stdout || '').slice(0, 800) + (run.stderr || '').slice(0, 800))
  process.exit(2)
}
const jsonOut = arg('--json', '')
if (jsonOut) writeFileSync(resolve(jsonOut), JSON.stringify(parsed, null, 1) + '\n')

const withTimeout = parsed.summary.filter((t) => typeof t.timeoutMs === 'number')
const ok = parsed.count === EXPECTED_TOOLS && parsed.errors.length === 0 && withTimeout.length === EXPECTED_TIMEOUTS
console.log('dsh-tools 版本 : ' + parsed.dshToolsVersion)
console.log('注册工具数     : ' + parsed.count + '（期望 ' + EXPECTED_TOOLS + '）')
console.log('声明 timeoutMs : ' + withTimeout.length + '（期望 ' + EXPECTED_TIMEOUTS + '）'
  + (withTimeout.length ? ' · ' + withTimeout.map((t) => t.name.replace('blender_', '') + '=' + t.timeoutMs).join(' ') : ''))
console.log('注册期错误     : ' + (parsed.errors.length ? JSON.stringify(parsed.errors) : '无'))
console.log('版本前缀       : ' + (parsed.summary[0] ? parsed.summary[0].descriptionPrefix : '(无工具)'))
console.log(jsonOut ? '基线已写入     : ' + jsonOut : '')

if (argv.includes('--smoke')) {
  const smoke = spawnSync(process.execPath, ['-e', `
    const root = process.argv[1]
    const mod = await import(root + '/plugin/index.mjs')
    const regs = []
    const ctx = { effect(fn) { try { fn() } catch (e) {} return () => {} }, get() { return undefined }, on() {}, plugin() {}, tools: { register(d) { regs.push(d); return () => {} } } }
    mod.apply(ctx, { port: Number(process.argv[2] || 9877), autoStart: false })
    const vp = regs.find((t) => t.name === 'blender_viewport')
    const ac = new AbortController(); ac.abort()
    const t0 = Date.now()
    let out
    try { out = await vp.execute({ op: 'status' }, { signal: ac.signal }) } catch (e) { out = { err: String(e && e.message) } }
    const ms = Date.now() - t0
    const text = String(typeof out === 'string' ? out : JSON.stringify(out))
    console.log('smoke aborted-call ms=' + ms + ' mentionsCancel=' + /取消|abort/i.test(text))
    process.exit(ms < 5000 && /取消|abort/i.test(text) ? 0 : 1)
  `, root], { encoding: 'utf8' })
  console.log('exec.signal 冒烟: ' + ((smoke.stdout || '').trim() || (smoke.stderr || '').trim()).slice(0, 200))
}
console.log(ok ? '结果           : PASS（工具契约与本次 DSH 版本兼容）' : '结果           : FAIL（见上）')
process.exit(ok ? 0 : 1)
