#!/usr/bin/env node
/**
 * Parity gate for the T15 runtime TypeScript migration (plan §8.2 B5 / §10.4 T15e).
 *
 * The migrated runtime (runtime tree TS sources compiled to lib/runtime JS) must
 * behave exactly like the pre-migration runtime sources at the refactor base
 * commit. This script imports BOTH sides in the same process tree:
 *
 *   base:    git worktree at DSH_PARITY_BASE (default b8778c8) -> runtime/engine.mjs
 *   current: lib/runtime/engine.js (built by `npm run build:runtime`)
 *
 * and compares the stable, observable surface: export set, catalog fingerprint,
 * catalog payloads (all output forms), catalog / plan / command / tool tables,
 * route contract and a few pure helpers. Absolute provenance hashes are
 * normalised (they change when source files change) but structure and user
 * visible text are compared strictly.
 *
 * Exit code 0 = parity holds. Any mismatch is a hard failure (never skipped).
 */
import { spawnSync } from 'node:child_process'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { ensureBuild } from './ensure-build.mjs'

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const BASE = process.env.DSH_PARITY_BASE || 'b8778c8'
const TMP_ROOT = process.env.DSH_TMP || os.tmpdir()

function arg(name) {
  const i = process.argv.indexOf(name)
  return i >= 0 ? process.argv[i + 1] : null
}
const OUT = arg('--out')

/** Provenance hashes change whenever the migrated sources change; normalise them. */
const HASH_RE = /[0-9a-f]{12}/g
// Provenance fields that depend on where/how the engine was loaded (source
// file size changes when .mjs -> .ts -> .js, absolute path is environment
// specific, timestamps differ per run). Structure and everything else is strict.
const ENV_KEYS = new Set([
  'sourcePath',
  'loadedAt',
  'loadedBytes',
  'currentBytes',
  'currentMtime',
  'loadedMtime',
  'diskMtime',
  'mtime',
  'mtimeMs',
  // The plugin version is a declared, user-visible release change (2.6.2 ->
  // 3.0.0), not a behavior change. Everything else in provenance stays strict.
  'pluginVersion'
])
function normalize(v) {
  if (typeof v === 'string') return v.replace(HASH_RE, '<hash>')
  if (Array.isArray(v)) return v.map(normalize)
  if (v && typeof v === 'object') {
    const out = {}
    for (const k of Object.keys(v)) out[k] = ENV_KEYS.has(k) ? '<env>' : normalize(v[k])
    return out
  }
  return v
}
const canon = (v) => JSON.stringify(normalize(v))

const checks = []
function eq(id, label, base, cur) {
  const ok = canon(base) === canon(cur)
  checks.push({ id, label, ok })
  if (!ok) {
    const b = canon(base)
    const c = canon(cur)
    let i = 0
    while (i < b.length && i < c.length && b[i] === c[i]) i++
    throw new Error(
      label +
        ' mismatch at offset ' +
        i +
        '\n  base: ' +
        b.slice(Math.max(0, i - 120), i + 240) +
        '\n  cur:  ' +
        c.slice(Math.max(0, i - 120), i + 240)
    )
  }
}
function ok(id, label, detail) {
  checks.push({ id, label, ok: true, ...(detail === undefined ? {} : { detail }) })
}

const tmp = fs.mkdtempSync(path.join(TMP_ROOT, 'dsh-parity-'))
let exitCode = 0
let report = null
try {
  // Extract the pre-migration runtime modules from git. (No second worktree:
  // the main checkout is outside this agent's write scope. No tar either: the
  // PATH tar is an MSYS build that cannot start under the harness sandbox.)
  const list = spawnSync('git', ['ls-tree', '-r', '--name-only', BASE, 'runtime'], {
    cwd: ROOT,
    encoding: 'utf8',
    maxBuffer: 64 * 1024 * 1024
  })
  if (list.status !== 0) throw new Error('git ls-tree ' + BASE + ' failed: ' + String(list.stderr || '').slice(-800))
  const baseFiles = String(list.stdout || '')
    .split(/\r?\n/)
    .filter((f) => f.endsWith('.mjs') || f.endsWith('.json'))
  for (const rel of baseFiles) {
    const shown = spawnSync('git', ['show', BASE + ':' + rel], { cwd: ROOT, maxBuffer: 64 * 1024 * 1024 })
    if (shown.status !== 0) throw new Error('git show ' + BASE + ':' + rel + ' failed: ' + String(shown.stderr || '').slice(-400))
    const dest = path.join(tmp, ...rel.split('/'))
    fs.mkdirSync(path.dirname(dest), { recursive: true })
    fs.writeFileSync(dest, shown.stdout)
  }
  const pkgShown = spawnSync('git', ['show', BASE + ':package.json'], { cwd: ROOT, maxBuffer: 8 * 1024 * 1024 })
  if (pkgShown.status !== 0) throw new Error('git show ' + BASE + ':package.json failed')
  fs.writeFileSync(path.join(tmp, 'package.json'), pkgShown.stdout)
  ensureBuild({ log: (m) => console.error('parity: ' + m) })

  const baseEngine = await import(pathToFileURL(path.join(tmp, 'runtime', 'engine.mjs')).href)
  const baseRoutes = await import(pathToFileURL(path.join(tmp, 'runtime', 'http', 'routes', 'index.mjs')).href)
  const curEngine = await import(pathToFileURL(path.join(ROOT, 'lib', 'runtime', 'engine.js')).href)
  const curRoutes = await import(pathToFileURL(path.join(ROOT, 'lib', 'runtime', 'http', 'routes', 'index.js')).href)

  // 1. Public export set must be identical.
  const bKeys = Object.keys(baseEngine).sort()
  const cKeys = Object.keys(curEngine).sort()
  eq('exports', 'engine export set', bKeys, cKeys)
  ok('exports-count', 'engine exports', bKeys.length)

  // 2. Catalog fingerprint (34 families / 228 ops / 39 commands / 7 tool details).
  const bFp = baseEngine.catalogFingerprint()
  const cFp = curEngine.catalogFingerprint()
  eq('fingerprint', 'catalogFingerprint()', bFp, cFp)
  ok('contract-counts', 'catalog contract counts', bFp)

  // 3. Catalog payload in every output form, provenance hashes normalised.
  const payloadForms = [
    ['default', {}],
    ['family', { family: 'sculpt' }],
    ['tool', { tool: 'rt_headless' }],
    ['full', { full: true }],
    ['handoff', { handoff: true }],
    ['verify', { verify: true }]
  ]
  for (const [id, opts] of payloadForms) {
    eq('catalog-' + id, 'catalogPayload(' + JSON.stringify(opts) + ')', baseEngine.catalogPayload(opts), curEngine.catalogPayload(opts))
    ok('catalog-' + id + '-len', 'catalogPayload text length', { text: curEngine.catalogPayload(opts).text.length })
  }

  // 4. Catalog / plan / command / tool tables (names + structure, not prose).
  const tables = [
    'COMMAND_CATALOG',
    'PLAN_CATALOG',
    'TOOL_DETAIL',
    'PLAN_TOOL_MAP',
    'MODEL_FACING_TOOLS',
    'GATE_FLAGS',
    'FLAGS_CODE',
    'MODELING_CLASS_GUIDE',
    'PATH_GUARD'
  ]
  for (const name of tables) {
    eq('table-' + name, name, Object.keys(baseEngine[name] || {}).sort(), Object.keys(curEngine[name] || {}).sort())
    ok('table-' + name + '-entries', name + ' entries', Object.keys(curEngine[name] || {}).length)
  }

  // 5. Route contract (29 routes).
  eq('routes', 'CONTRACT_ROUTES', baseRoutes.CONTRACT_ROUTES, curRoutes.CONTRACT_ROUTES)
  ok('routes-count', 'CONTRACT_ROUTES routes', curRoutes.CONTRACT_ROUTES.length)

  // 6. Pure helpers called by tooling / matrix.
  eq('plan-op-names', 'planOpNames()', baseEngine.planOpNames(), curEngine.planOpNames())
  eq('plan-op-suggestion', 'planOpSuggestion()', baseEngine.planOpSuggestion('nonsense_op'), curEngine.planOpSuggestion('nonsense_op'))
  eq(
    'plan-op-hint',
    'planOpCrossChannelHint()',
    baseEngine.planOpCrossChannelHint('headless'),
    curEngine.planOpCrossChannelHint('headless')
  )
  eq('class-guide-stats', 'classGuideStats()', baseEngine.classGuideStats(), curEngine.classGuideStats())
  eq('render-handoff', 'renderHandoff()', baseEngine.renderHandoff(), curEngine.renderHandoff())
  eq(
    'render-class-text',
    'renderClassText()',
    baseEngine.renderClassText(baseEngine.MODELING_CLASS_GUIDE[0]),
    curEngine.renderClassText(curEngine.MODELING_CLASS_GUIDE[0])
  )
  eq('plan-read-only', 'PLAN_READ_ONLY', baseEngine.PLAN_READ_ONLY, curEngine.PLAN_READ_ONLY)
  eq('plan-read-only-ops', 'PLAN_READ_ONLY_OPS', baseEngine.PLAN_READ_ONLY_OPS, curEngine.PLAN_READ_ONLY_OPS)

  report = {
    ok: true,
    base: BASE,
    checkedAt: new Date().toISOString(),
    node: process.version,
    runtime: 'lib/runtime/engine.js',
    checks,
    counts: {
      exports: bKeys.length,
      families: bFp.families,
      ops: bFp.opsCount,
      commands: bFp.commandCatalogKeys,
      toolDetailKeys: bFp.toolDetailKeys,
      routes: curRoutes.CONTRACT_ROUTES.length
    },
    parity: 'PASS'
  }
} catch (e) {
  exitCode = 1
  const message = String((e && e.message) || e)
  checks.push({ id: 'parity', label: 'parity gate', ok: false, error: message })
  report = {
    ok: false,
    base: BASE,
    checkedAt: new Date().toISOString(),
    node: process.version,
    checks,
    error: message,
    parity: 'FAIL'
  }
  console.error('parity: FAIL - ' + message)
} finally {
  fs.rmSync(tmp, { recursive: true, force: true })
}

if (OUT) {
  fs.mkdirSync(path.dirname(path.resolve(ROOT, OUT)), { recursive: true })
  fs.writeFileSync(path.resolve(ROOT, OUT), JSON.stringify(report, null, 2) + '\n')
}
if (report && report.ok) {
  const c = report.counts
  console.log(
    'parity: PASS (base ' +
      report.base +
      ') - exports ' +
      c.exports +
      ', families ' +
      c.families +
      ', ops ' +
      c.ops +
      ', commands ' +
      c.commands +
      ', toolDetail ' +
      c.toolDetailKeys +
      ', routes ' +
      c.routes
  )
  for (const chk of checks) if (chk.label) console.log('  ok  ' + chk.id + '  ' + chk.label)
} else {
  console.error('parity: FAIL')
  for (const chk of checks) if (!chk.ok) console.error('  fail  ' + chk.id + '  ' + (chk.error || chk.label))
}
process.exit(exitCode)
