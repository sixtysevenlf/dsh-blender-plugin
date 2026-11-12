#!/usr/bin/env node
/**
 * Parity gate for the runtime TypeScript build.
 *
 * The migrated runtime (runtime tree TS sources compiled to lib/runtime JS) must
 * behave exactly like the pre-TypeScript runtime sources at the parity base
 * commit. This script imports BOTH sides in the same process tree:
 *
 *   base:    git worktree at DSH_PARITY_BASE (default b8778c8) -> runtime/engine.mjs
 *   current: lib/runtime/engine.js (built by `npm run build:runtime`)
 *
 * and compares the stable, observable surface: export set (minus the declared
 * engine removals below), catalog fingerprint,
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

// Declared contract removals: the base runtime is patched by deleting exactly
// these catalog entries before import. This keeps the parity gate strict for
// every other byte while making an intentional, reviewed contract change
// explicit instead of silently weakening the comparison. v3.0.0 removes the
// Premium/Tripo channel; nothing else may disappear without adding it here.
// v3.1 is a declared breaking model-surface change: the 15-tool surface
// becomes exactly 6 tools, and the catalog family metadata grows the
// requires_session / write_scope target fields. Those two surfaces are frozen
// by config/contracts + tests/unit/js/tool-surface-3-1.test.ts and
// tests/unit/js/contract/op-contract.test.ts instead of being byte-compared
// against the pre-3.1 base. Everything else stays strict.
const DECLARED_TOOL_SURFACE_3_1 = {
  toolDetailKeys: { base: 7, current: 6 },
  tables: ['TOOL_DETAIL', 'PLAN_TOOL_MAP', 'MODEL_FACING_TOOLS'],
  catalogPayloadForms: ['default', 'family', 'tool', 'full', 'handoff', 'verify'],
}
const DECLARED_CATALOG_REMOVALS = [
  'get_tripo_status',
  'export_scene',
  'set_texture',
  'download_polyhaven_asset',
  'create_rodin_job',
  'import_generated_asset',
  'download_sketchfab_model',
  'download_polypizza_model',
  'create_hunyuan_job',
  'import_generated_asset_hunyuan',
  'get_telemetry_consent',
  'set_telemetry_consent',
  'drain_human_activity',
  'get_polyhaven_asset_preview',
  'ping',
]

// Declared user-visible catalog text patches: exact base -> current replacements
// for reviewed help-text fixes. The parity gate compares user-visible text
// strictly, so a prose fix is an explicit, reviewed contract change too — each
// pair must occur exactly once in the base catalog source.
const DECLARED_CATALOG_TEXT_PATCHES = [
  ['args={ref:"ref.png", render:"out.png"}', 'args={ref_path:"ref.png", render_path:"out.png"}'],
  [
    '透传 addon 命令（get_scene_info / export_scene / describe_node_type / bpy_api_lookup …）',
    '执行 addon 只读命令（get_scene_info / get_selection / describe_node_type / bpy_api_lookup …）',
  ],
]

// Declared public-surface removals: /4.2 shrinks runtime/engine.ts from the
// 87 re-exports of the TS migration to the ~20 names actually consumed through
// the built lib/runtime/engine.js path (config/facades.json is the registry).
// Filters the base export NAME SET (not the file): a name listed here must
// exist in the base engine and must be gone from the current one — so this
// cannot be used to hide an accidental deletion or a half-finished shrink.
const DECLARED_ENGINE_REMOVALS = [
  'launchBlender',
  'sceneHygieneAudit',
  'winToWsl',
  'wslToWin',
  'describeConfig',
  'PKG_ROOT',
  'blenderJoin',
  'PIPELINE_PATH',
  'ADDRESS',
  'WIN_TMP',
  'WSL_TMP',
  'LIVE_PNG_WIN',
  'LIVE_PNG_WSL',
  'KIT_PATH',
  'RUNNER_PATH',
  'PERF_PATH',
  'VIEW_PATH',
  'CONTRACT_PATH',
  'PLANNER_PATH',
  'WORKER_PATH',
  'TXN_PATH',
  'QC_PATH',
  'QC_RENDER_PATH',
  'PRESET_PATH',
  'AUDIT_PATH',
  'MONTAGE_PATH',
  'MOTION_PATH',
  'DELIVER_PATH',
  'GENERATOR_PATH',
  'SCULPT_PATH',
  'FIX_PATH',
  'UV_PATH',
  'PRINT_PATH',
  'SWEEP_PATH',
  'MATERIAL_PATH',
  'RENDER_GUARD_PATH',
  'GATE_PATH',
  'IMG_PATH',
  'CALIB_PATH',
  'FACE_PATH',
  'HUMAN_PATH',
  'CLEARANCE_PATH',
  'VEHICLE_PATH',
  'SHAPE_PATH',
  'USER_CONFIG_WIN',
  'USER_SCRIPTS_WIN',
  'GPU_PRELUDE',
  'VIEW_PNG_WIN',
  'VIEW_PNG_WSL',
  'BLENDER_EXE',
  'PATH_HELPERS',
  'ACT_WRAPPER',
  'ASSEMBLY_PATH',
  'BLOCKOUT_PATH',
  'VISUALREPORT_PATH',
  'SHAPESOLVE_PATH',
  'EVALHARNESS_PATH',
  'SURFACE_PATH',
  'KERNEL_BOOTSTRAP',
  'writeHeadlessScript',
  'tcpProbe',
  'freshPing',
  'diagnose',
  'protocolInfo',
  'AddonClient',
]

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
  'pluginVersion',
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
        c.slice(Math.max(0, i - 120), i + 240),
    )
  }
}
function ok(id, label, detail) {
  checks.push({ id, label, ok: true, ...(detail === undefined ? {} : { detail }) })
}

const tmp = fs.mkdtempSync(path.join(TMP_ROOT, 'dsh-parity-'))
let exitCode = 0
let report
try {
  // Extract the pre-migration runtime modules from git. (No second worktree:
  // the main checkout is outside this agent's write scope. No tar either: the
  // PATH tar is an MSYS build that cannot start under the harness sandbox.)
  const list = spawnSync('git', ['ls-tree', '-r', '--name-only', BASE, 'runtime'], {
    cwd: ROOT,
    encoding: 'utf8',
    maxBuffer: 64 * 1024 * 1024,
  })
  if (list.status !== 0)
    throw new Error('git ls-tree ' + BASE + ' failed: ' + String(list.stderr || '').slice(-800))
  const baseFiles = String(list.stdout || '')
    .split(/\r?\n/)
    .filter((f) => f.endsWith('.mjs') || f.endsWith('.json'))
  for (const rel of baseFiles) {
    const shown = spawnSync('git', ['show', BASE + ':' + rel], {
      cwd: ROOT,
      maxBuffer: 64 * 1024 * 1024,
    })
    if (shown.status !== 0)
      throw new Error(
        'git show ' + BASE + ':' + rel + ' failed: ' + String(shown.stderr || '').slice(-400),
      )
    const dest = path.join(tmp, ...rel.split('/'))
    fs.mkdirSync(path.dirname(dest), { recursive: true })
    fs.writeFileSync(dest, shown.stdout)
  }
  // Apply the declared removals to the extracted base catalog source. The
  // entries are one-line object properties, so a line-level filter is exact.
  const baseCatalogPath = path.join(tmp, 'runtime', 'catalog.mjs')
  const baseCatalogLines = fs
    .readFileSync(baseCatalogPath, 'utf8')
    .split(/\r?\n/)
    .filter((line) => !DECLARED_CATALOG_REMOVALS.some((name) => line.startsWith('  ' + name + ':')))
  let baseCatalogText = baseCatalogLines.join('\n')
  for (const [from, to] of DECLARED_CATALOG_TEXT_PATCHES) {
    const seen = baseCatalogText.split(from).length - 1
    if (seen !== 1) {
      throw new Error(
        'declared catalog text patch must occur once in base, found ' + seen + ': ' + from,
      )
    }
    baseCatalogText = baseCatalogText.replace(from, to)
  }
  fs.writeFileSync(baseCatalogPath, baseCatalogText)
  ok('declared-removals', 'declared catalog removals applied to base', DECLARED_CATALOG_REMOVALS)
  ok(
    'declared-text-patches',
    'declared catalog text patches applied to base',
    DECLARED_CATALOG_TEXT_PATCHES.length,
  )

  const pkgShown = spawnSync('git', ['show', BASE + ':package.json'], {
    cwd: ROOT,
    maxBuffer: 8 * 1024 * 1024,
  })
  if (pkgShown.status !== 0) throw new Error('git show ' + BASE + ':package.json failed')
  fs.writeFileSync(path.join(tmp, 'package.json'), pkgShown.stdout)
  ensureBuild({ log: (m) => console.error('parity: ' + m) })

  const baseEngine = await import(pathToFileURL(path.join(tmp, 'runtime', 'engine.mjs')).href)
  const baseRoutes = await import(
    pathToFileURL(path.join(tmp, 'runtime', 'http', 'routes', 'index.mjs')).href
  )
  const curEngine = await import(pathToFileURL(path.join(ROOT, 'lib', 'runtime', 'engine.js')).href)
  const curRoutes = await import(
    pathToFileURL(path.join(ROOT, 'lib', 'runtime', 'http', 'routes', 'index.js')).href
  )

  // 1. Public export set must be identical after the declared engine removals.
  const bKeysRaw = Object.keys(baseEngine).sort()
  const cKeys = Object.keys(curEngine).sort()
  const notInBase = DECLARED_ENGINE_REMOVALS.filter((n) => !bKeysRaw.includes(n))
  if (notInBase.length)
    throw new Error('declared engine removals missing from base: ' + notInBase.join(', '))
  const stillExported = DECLARED_ENGINE_REMOVALS.filter((n) => cKeys.includes(n))
  if (stillExported.length)
    throw new Error('declared engine removals still exported: ' + stillExported.join(', '))
  const bKeys = bKeysRaw.filter((n) => !DECLARED_ENGINE_REMOVALS.includes(n))
  eq('exports', 'engine export set', bKeys, cKeys)
  ok('declared-engine-removals', 'declared engine exports removed', DECLARED_ENGINE_REMOVALS.length)
  ok('exports-count', 'engine exports', bKeys.length)

  // 2. Catalog fingerprint (36 families / 239 ops / 25 commands / 6 tool details).
  // The 3.1 tool-surface change is declared: toolDetailKeys shrinks 7 -> 6 and
  // the family metadata grows target fields, so the hash itself is not compared;
  // the structural contract counts are.
  const bFp = baseEngine.catalogFingerprint()
  const cFp = curEngine.catalogFingerprint()
  if (bFp.families !== cFp.families || bFp.opsCount !== cFp.opsCount)
    throw new Error(
      'catalog fingerprint counts changed: ' +
        bFp.families +
        '/' +
        bFp.opsCount +
        ' -> ' +
        cFp.families +
        '/' +
        cFp.opsCount,
    )
  if (bFp.commandCatalogKeys !== cFp.commandCatalogKeys)
    throw new Error(
      'command catalog keys changed: ' + bFp.commandCatalogKeys + ' -> ' + cFp.commandCatalogKeys,
    )
  if (bFp.toolDetailKeys !== DECLARED_TOOL_SURFACE_3_1.toolDetailKeys.base)
    throw new Error('base toolDetailKeys changed: ' + bFp.toolDetailKeys)
  if (cFp.toolDetailKeys !== DECLARED_TOOL_SURFACE_3_1.toolDetailKeys.current)
    throw new Error('current toolDetailKeys must be 6, got ' + cFp.toolDetailKeys)
  ok('declared-tool-surface-3-1', 'declared 15 -> 6 tool surface', {
    from: bFp.toolDetailKeys,
    to: cFp.toolDetailKeys,
  })
  ok('contract-counts', 'catalog contract counts', cFp)

  // 3. Catalog payload in every output form, provenance hashes normalised.
  const payloadForms = [
    ['default', {}],
    ['family', { family: 'sculpt' }],
    ['full', { full: true }],
    ['handoff', { handoff: true }],
    ['verify', { verify: true }],
  ]
  for (const [id, opts] of payloadForms) {
    // Declared 3.1 surface change: the catalog prose embeds the tool names and
    // the new target fields, so it cannot be byte-compared to the 15-tool base.
    // Compare the structure every form keeps and forbid legacy names from
    // leaking back into the model-facing text (the old names are migration-only).
    const body = curEngine.catalogPayload(opts)
    const baseBody = baseEngine.catalogPayload(opts)
    if (JSON.stringify(body.families ?? null) !== JSON.stringify(baseBody.families ?? null))
      throw new Error('catalog-' + id + ' family list drifted')
    if (body.ops_count !== baseBody.ops_count)
      throw new Error('catalog-' + id + ' op count drifted')
    if (!body.text || typeof body.text !== 'string')
      throw new Error('catalog-' + id + ' has no text')
    if (
      /blender_rt_(see|do|watch|plan|loop|opt|preset|headless|job|perf|cmd|commands|txn|worker)|blender_viewport/.test(
        body.text,
      )
    )
      throw new Error('catalog-' + id + ' still advertises a legacy 3.0 tool name')
    ok('catalog-' + id + '-len', 'catalogPayload text length', { text: body.text.length })
  }
  // The per-tool view cannot be executed against the base (the 3.0 surface has
  // no blender_* names). Freeze it on the 6 current tools instead.
  for (const name of curEngine.MODEL_FACING_TOOLS) {
    const body = curEngine.catalogPayload({ tool: name })
    if (!body.text || typeof body.text !== 'string')
      throw new Error('catalogPayload({tool:' + name + '}) has no text')
    if (!body.text.includes(name))
      throw new Error('catalogPayload({tool:' + name + '}) omits the tool')
    if (
      /blender_rt_(see|do|watch|plan|loop|opt|preset|headless|job|perf|cmd|commands|txn|worker)|blender_viewport/.test(
        body.text,
      )
    )
      throw new Error('catalogPayload({tool:' + name + '}) advertises a legacy name')
  }
  ok(
    'declared-catalog-payload-forms',
    'declared catalog payload forms compared structurally (+6 per-tool views)',
    DECLARED_TOOL_SURFACE_3_1.catalogPayloadForms.length,
  )

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
    'PATH_GUARD',
  ]
  for (const name of tables) {
    if (DECLARED_TOOL_SURFACE_3_1.tables.includes(name)) {
      // Declared 3.1 surface change; the frozen replacement is
      // tests/unit/js/tool-surface-3-1.test.ts + capability.lock.json.
      const k = Object.keys(curEngine[name] || {}).length
      if (k !== DECLARED_TOOL_SURFACE_3_1.toolDetailKeys.current)
        throw new Error(name + ' must expose 6 entries, got ' + k)
      ok('table-' + name + '-declared', name + ' declared 3.1 surface', k)
      continue
    }
    eq(
      'table-' + name,
      name,
      Object.keys(baseEngine[name] || {}).sort(),
      Object.keys(curEngine[name] || {}).sort(),
    )
    ok('table-' + name + '-entries', name + ' entries', Object.keys(curEngine[name] || {}).length)
  }

  // 5. Route contract (29 routes).
  eq('routes', 'CONTRACT_ROUTES', baseRoutes.CONTRACT_ROUTES, curRoutes.CONTRACT_ROUTES)
  ok('routes-count', 'CONTRACT_ROUTES routes', curRoutes.CONTRACT_ROUTES.length)

  // 6. Pure helpers called by tooling / matrix.
  eq('plan-op-names', 'planOpNames()', baseEngine.planOpNames(), curEngine.planOpNames())
  eq(
    'plan-op-suggestion',
    'planOpSuggestion()',
    baseEngine.planOpSuggestion('nonsense_op'),
    curEngine.planOpSuggestion('nonsense_op'),
  )
  eq(
    'plan-op-hint',
    'planOpCrossChannelHint()',
    baseEngine.planOpCrossChannelHint('headless'),
    curEngine.planOpCrossChannelHint('headless'),
  )
  eq(
    'class-guide-stats',
    'classGuideStats()',
    baseEngine.classGuideStats(),
    curEngine.classGuideStats(),
  )
  {
    // Declared 3.1 surface change: the handoff text lists the tool surface, so
    // it cannot be byte-compared. Freeze its current invariants instead.
    const handoff = curEngine.renderHandoff()
    if (typeof handoff !== 'string' || !handoff.length) throw new Error('renderHandoff() is empty')
    for (const name of curEngine.MODEL_FACING_TOOLS)
      if (!handoff.includes(name)) throw new Error('renderHandoff() omits ' + name)
    if (
      /blender_rt_(see|do|watch|plan|loop|opt|preset|headless|job|perf|cmd|commands|txn|worker)|blender_viewport/.test(
        handoff,
      )
    )
      throw new Error('renderHandoff() advertises a legacy 3.0 tool name')
    ok('render-handoff-declared', 'renderHandoff() declared 3.1 surface', handoff.length)
  }
  {
    // Declared 3.1 surface change: the class text embeds the entry-tool call.
    const text = curEngine.renderClassText(curEngine.MODELING_CLASS_GUIDE[0])
    if (typeof text !== 'string' || !text.length) throw new Error('renderClassText() is empty')
    if (
      /blender_rt_(see|do|watch|plan|loop|opt|preset|headless|job|perf|cmd|commands|txn|worker)|blender_viewport/.test(
        text,
      )
    )
      throw new Error('renderClassText() advertises a legacy 3.0 tool name')
    ok('render-class-text-declared', 'renderClassText() declared 3.1 surface', text.length)
  }
  eq('plan-read-only', 'PLAN_READ_ONLY', baseEngine.PLAN_READ_ONLY, curEngine.PLAN_READ_ONLY)
  eq(
    'plan-read-only-ops',
    'PLAN_READ_ONLY_OPS',
    baseEngine.PLAN_READ_ONLY_OPS,
    curEngine.PLAN_READ_ONLY_OPS,
  )

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
      toolDetailKeys: cFp.toolDetailKeys,
      routes: curRoutes.CONTRACT_ROUTES.length,
    },
    parity: 'PASS',
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
    parity: 'FAIL',
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
      c.routes,
  )
  for (const chk of checks) if (chk.label) console.log('  ok  ' + chk.id + '  ' + chk.label)
} else {
  console.error('parity: FAIL')
  for (const chk of checks)
    if (!chk.ok) console.error('  fail  ' + chk.id + '  ' + (chk.error || chk.label))
}
process.exit(exitCode)
