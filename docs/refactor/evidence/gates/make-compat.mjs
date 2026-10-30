#!/usr/bin/env node
/**
 * A7 / B5 compatibility snapshot: compare the frozen v2.6.2 surface with the
 * current 3.0.0 surface. Generated artifacts:
 *   compat-base.json, compat-v300.json  - normalized snapshots
 *   tool-schema.diff                    - empty when all 15 tool schemas match
 *   compat.diff                         - every difference, each one must be declared
 *
 * Historical version stamps in descriptions ("[v2.6.2] ...") are normalized;
 * the addon bl_info version (1.10 -> 1.11) and the plugin version (2.6.2 ->
 * 3.0.0) are declared release changes in MIGRATIONS/CHANGELOG, so they are
 * recorded rather than treated as behavior changes.
 *
 * Usage (from the repo root):
 *   node docs/refactor/evidence/gates/make-compat.mjs [base-worktree]
 */
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const ROOT = path.resolve(HERE, '../../../..')
const BASE = path.resolve(process.argv[2] || process.env.DSH_COMPAT_BASE || 'D:/DSH/worktrees/base-262')

const VERSION_STAMP = /\[v\d+\.\d+\.\d+\]\s*/g
const normalizeText = (s) => (s || '').replace(VERSION_STAMP, '').trim()

async function loadTools(pluginFile) {
  const mod = await import(pathToFileURL(pluginFile).href + '?compat=' + Math.random())
  const tools = []
  const disposers = []
  const ctx = {
    tools: { register(t) { tools.push(t); return t } },
    effect(fn) { const d = fn(); if (typeof d === 'function') disposers.push(d); return d },
    inject() { return () => {} },
    on() { return () => {} },
    get() { return undefined },
    logger: { info() {}, warn() {}, error() {}, debug() {} }
  }
  mod.apply(ctx, { autoStart: false, tui: false })
  const out = tools.map((t) => ({
    name: t.name,
    description: normalizeText(t.description),
    parameters: t.parameters,
    output: t.output,
    timeoutMs: t.timeoutMs,
    isConcurrencySafe: typeof t.isConcurrencySafe === 'function' ? t.isConcurrencySafe() : t.isConcurrencySafe
  }))
  for (const d of disposers.reverse()) { try { d() } catch {} }
  return out
}

async function loadEngine(engineFile) {
  return await import(pathToFileURL(engineFile).href + '?compat=' + Math.random())
}

function readAddonVersion(file) {
  const text = fs.readFileSync(file, 'utf8')
  const m = /"version"\s*:\s*\((\d+),\s*(\d+)\)/.exec(text)
  return m ? [Number(m[1]), Number(m[2])] : null
}

const baseTools = await loadTools(path.join(BASE, 'lib/index.js'))
const currentTools = await loadTools(path.join(ROOT, 'lib/index.js'))

const baseEngine = await loadEngine(path.join(BASE, 'runtime/engine.mjs'))
const currentEngine = await loadEngine(path.join(ROOT, 'lib/runtime/engine.js'))

const baseAddonFile = path.join(BASE, 'addon/dsh_blender_bridge.py')
const currentAddonFile = path.join(ROOT, 'addon/dsh_blender_bridge/protocol.py')
const baseAddonVersion = readAddonVersion(baseAddonFile)
const currentAddonVersion = readAddonVersion(path.join(ROOT, 'addon/dsh_blender_bridge/__init__.py'))
const baseProtocol = Number(/ADDON_PROTOCOL_VERSION\s*=\s*(\d+)/.exec(fs.readFileSync(baseAddonFile, 'utf8'))[1])
const currentProtocol = Number(/ADDON_PROTOCOL_VERSION\s*=\s*(\d+)/.exec(fs.readFileSync(currentAddonFile, 'utf8'))[1])

const snapshot = (tools, engine, addonVersion, protocol, pluginVersion) => ({
  pluginVersion,
  addonVersion,
  protocol,
  tools,
  commands: Object.fromEntries(Object.entries(engine.COMMAND_CATALOG).map(([name, c]) => [name, normalizeText(c.d)])),
  plan: engine.PLAN_CATALOG.map((f) => ({ family: f.f, ops: f.ops }))
})
const base = snapshot(baseTools, baseEngine, baseAddonVersion, baseProtocol, '2.6.2')
const current = snapshot(currentTools, currentEngine, currentAddonVersion, currentProtocol, '3.0.0')

const diffs = []
function compare(label, a, b) {
  if (JSON.stringify(a) === JSON.stringify(b)) return
  diffs.push({ field: label, base: a, current: b })
}
compare('tool count', base.tools.length, current.tools.length)
for (let i = 0; i < Math.max(base.tools.length, current.tools.length); i++) {
  const a = base.tools[i] || {}
  const b = current.tools[i] || {}
  compare('tool ' + (a.name || b.name) + ' name', a.name, b.name)
  compare('tool ' + (a.name || b.name) + ' parameters', a.parameters, b.parameters)
  compare('tool ' + (a.name || b.name) + ' output', a.output, b.output)
  compare('tool ' + (a.name || b.name) + ' timeoutMs', a.timeoutMs, b.timeoutMs)
  compare('tool ' + (a.name || b.name) + ' concurrency', a.isConcurrencySafe, b.isConcurrencySafe)
  compare('tool ' + (a.name || b.name) + ' description', a.description, b.description)
}
compare('command names', Object.keys(base.commands).sort(), Object.keys(current.commands).sort())
for (const name of Object.keys(base.commands)) compare('command ' + name + ' description', base.commands[name], current.commands[name])
compare('plan families/ops', base.plan, current.plan)

const declared = []
if (base.pluginVersion !== current.pluginVersion) declared.push({ field: 'pluginVersion', base: base.pluginVersion, current: current.pluginVersion, declaredIn: 'CHANGELOG.md v3.0.0 / MIGRATIONS.md' })
if (JSON.stringify(base.addonVersion) !== JSON.stringify(current.addonVersion)) declared.push({ field: 'addonVersion', base: base.addonVersion, current: current.addonVersion, declaredIn: 'CHANGELOG.md v3.0.0 / MIGRATIONS.md' })

fs.writeFileSync(path.join(HERE, 'compat-base.json'), JSON.stringify(base, null, 2) + '\n')
fs.writeFileSync(path.join(HERE, 'compat-v300.json'), JSON.stringify(current, null, 2) + '\n')

const toolSchemaDiff = base.tools.map((t) => {
  const c = current.tools.find((x) => x.name === t.name) || {}
  return JSON.stringify({ name: t.name, parameters: t.parameters, timeoutMs: t.timeoutMs }) ===
    JSON.stringify({ name: c.name, parameters: c.parameters, timeoutMs: c.timeoutMs }) ? '' : 'tool ' + t.name + ' schema changed'
}).filter(Boolean)
fs.writeFileSync(path.join(HERE, 'tool-schema.diff'), toolSchemaDiff.join('\n') + (toolSchemaDiff.length ? '\n' : ''))

const lines = []
lines.push('# v2.6.2 -> 3.0.0 compatibility diff')
lines.push('# Generated by make-compat.mjs. Declared changes are release metadata;')
lines.push('# any undeclared difference is a gate failure.')
if (!declared.length) lines.push('# declared changes: none')
for (const d of declared) lines.push('DECLARED ' + d.field + ': ' + JSON.stringify(d.base) + ' -> ' + JSON.stringify(d.current) + ' (' + d.declaredIn + ')')
for (const d of diffs) lines.push('UNDECLARED ' + d.field + ': ' + JSON.stringify(d.base) + ' -> ' + JSON.stringify(d.current))
lines.push(diffs.length ? 'RESULT FAIL: ' + diffs.length + ' undeclared difference(s)' : 'RESULT PASS: no undeclared behavior/schema difference')
fs.writeFileSync(path.join(HERE, 'compat.diff'), lines.join('\n') + '\n')

console.log(lines.join('\n'))
console.log('tools=' + current.tools.length + ' commands=' + Object.keys(current.commands).length + ' families=' + current.plan.length + ' ops=' + current.plan.reduce((n, f) => n + f.ops.length, 0))
if (diffs.length) process.exitCode = 1
