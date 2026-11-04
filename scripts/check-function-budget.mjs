#!/usr/bin/env node
/**
 * scripts/check-function-budget.mjs — function hard limit + soft-band debt registry.
 *
 * Hard rule: no production function may exceed 120 lines. Functions over 120
 * always fail; the registry cannot waive them.
 *
 * Soft band: functions from 81 to 120 lines are owned debt, not a silent
 * warning. Every band function needs an entry in
 * config/function-budget-allowlist.json with a real classification (see
 * CLASSES), a reason, the class owner and a future reviewBy date. The gate
 * fails on an unregistered, unclassified, expired or stale entry, so the band
 * cannot turn into an unowned dumping ground (design D11, finalization
 * §1.1.4: no unowned exceptions).
 *
 * Path-level exceptions (file line budgets) are owned by config/exceptions.json
 * (kind=file-budget) and enforced by scripts/check-structure.mjs; this gate
 * only owns the function level. That is the two-level exception model.
 *
 * Usage:
 *   node scripts/check-function-budget.mjs                      # gate
 *   node scripts/check-function-budget.mjs --json --out <path>  # machine report
 *   node scripts/check-function-budget.mjs --baseline           # rewrite the ledger for the current tree
 *   node scripts/check-function-budget.mjs --refresh            # alias of --baseline
 *   node scripts/check-function-budget.mjs --root <dir> [--config <path>]  # fixture roots (tests)
 */
import { spawnSync } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import ts from 'typescript'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const DEFAULT_ROOT = path.resolve(HERE, '..')
const ARGV = process.argv.slice(2)
const JSON_ONLY = ARGV.includes('--json')
const REFRESH = ARGV.includes('--baseline') || ARGV.includes('--refresh')
const TARGET = 80
const HARD = 120
const MIN_REASON_LENGTH = 16
const DEFAULT_REVIEW_BY = '2027-06-30'
const TODAY = new Date().toISOString().slice(0, 10)
const GENERATED_HEADER = '// AUTO-GENERATED FILE'
const REGISTRY_DESCRIPTION =
  'Function-level budget registry (two-level exception model): every 80-120 line function is registered with a real classification, reason, class owner and future reviewBy; unregistered, unclassified, expired and stale entries fail. Functions over 120 lines always fail. Path-level file-budget exceptions live in config/exceptions.json.'

/** Soft-band classes: the role a function plays, why it legitimately exceeds 80 lines, and its owner. */
const CLASSES = [
  {
    id: 'dispatch-table',
    owner: 'runtime',
    description:
      'Name-to-handler tables, route tables, API factories and op switches whose length tracks the number of cases in the wire/protocol surface.',
  },
  {
    id: 'linear-pipeline',
    owner: 'runtime',
    description:
      'Ordered domain stages (phase _p1/_p2/_p3, scene/render/verify steps) whose length is the sequence of contract-ordered steps, not nested branching.',
  },
  {
    id: 'generated-catalog',
    owner: 'catalog',
    description:
      'Catalog data builders generated or derived from a config source; AUTO-GENERATED sources themselves are skipped and validated by check:routes.',
  },
  {
    id: 'orchestration',
    owner: 'runtime',
    description:
      'Lifecycle, process, recovery and E2E orchestration where state spans the whole operation and splitting would leak mutable state across closures.',
  },
  {
    id: 'formatting',
    owner: 'host',
    description:
      'Presentation surfaces: human/JSON report assembly, command-string formatting and script part assembly where output order is the contract.',
  },
  {
    id: 'validation',
    owner: 'qa',
    description:
      'Checks, audits, gates, verification and module self-tests: one rule block per accepted criterion, kept next to the evidence it produces.',
  },
  {
    id: 'parsing',
    owner: 'host',
    description:
      'Input normalization and structural walks (sanitizer, spec normalize, tree build) where one guard/rule per value shape dominates.',
  },
  {
    id: 'integration-io',
    owner: 'addon',
    description:
      'External-system adapters (bpy scene/material access, Blender file loading, HTTP asset services, screenshot/raycast IO) whose length is the API surface.',
  },
]
const CLASS_IDS = CLASSES.map((entry) => entry.id)
const CLASS_BY_ID = new Map(CLASSES.map((entry) => [entry.id, entry]))

function usage(message) {
  console.error('check-function-budget: ' + message)
  process.exit(2)
}

function option(name) {
  const index = ARGV.indexOf(name)
  if (index < 0) return null
  const value = ARGV[index + 1]
  if (!value || value.startsWith('--')) usage(name + ' needs a value')
  return value
}

const OUT_IDX = ARGV.indexOf('--out')
const OUT_PATH = OUT_IDX >= 0 ? ARGV[OUT_IDX + 1] : null
if (OUT_IDX >= 0 && !OUT_PATH) usage('--out needs a value')
const rootArg = option('--root')
const configArg = option('--config')
const ROOT = rootArg ? path.resolve(rootArg) : DEFAULT_ROOT
const CONFIG = configArg
  ? path.resolve(ROOT, configArg)
  : path.join(ROOT, 'config', 'function-budget-allowlist.json')
const REGISTRY = path.relative(ROOT, CONFIG).split(path.sep).join('/')

function rel(p) {
  return path.relative(ROOT, p).split(path.sep).join('/')
}

function isPlainObject(value) {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function walk(dir, exts, out) {
  if (!fs.existsSync(dir)) return
  for (const ent of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, ent.name)
    if (ent.isDirectory()) {
      if (ent.name === 'node_modules' || ent.name === '__pycache__' || ent.name === '.git') continue
      if (rel(p) === 'runtime/ext') continue
      walk(p, exts, out)
    } else if (exts.some((e) => ent.name.endsWith(e)) && !ent.name.endsWith('.d.ts')) {
      out.push(p)
    }
  }
}

function sourceName(node, _sf) {
  if (node.name && ts.isIdentifier(node.name)) return node.name.text
  if (node.name && ts.isStringLiteral(node.name)) return node.name.text
  const p = node.parent
  if (p && ts.isVariableDeclaration(p) && ts.isIdentifier(p.name)) return p.name.text
  if (p && ts.isPropertyAssignment(p) && (ts.isIdentifier(p.name) || ts.isStringLiteral(p.name)))
    return p.name.text
  if (p && ts.isMethodDeclaration(p) && p.name && ts.isIdentifier(p.name)) return p.name.text
  if (p && ts.isPropertyDeclaration(p) && ts.isIdentifier(p.name)) return p.name.text
  return '<anonymous>'
}

function tsFunctions(file) {
  const text = fs.readFileSync(file, 'utf8')
  const sf = ts.createSourceFile(
    file,
    text,
    ts.ScriptTarget.Latest,
    true,
    file.endsWith('.tsx') ? ts.ScriptKind.TSX : ts.ScriptKind.TS,
  )
  const out = []
  const visit = (node) => {
    if (
      ts.isFunctionDeclaration(node) ||
      ts.isFunctionExpression(node) ||
      ts.isArrowFunction(node) ||
      ts.isMethodDeclaration(node) ||
      ts.isGetAccessorDeclaration(node) ||
      ts.isSetAccessorDeclaration(node) ||
      ts.isConstructorDeclaration(node)
    ) {
      const start = sf.getLineAndCharacterOfPosition(node.getStart(sf)).line + 1
      const end = sf.getLineAndCharacterOfPosition(node.getEnd()).line + 1
      out.push({
        file: rel(file),
        name: sourceName(node, sf),
        line: start,
        endLine: end,
        lines: end - start + 1,
      })
    }
    ts.forEachChild(node, visit)
  }
  visit(sf)
  return out
}

function pythonFunctions() {
  // The interpreter belongs to this checkout, not to a fixture --root.
  const candidates = [
    process.env.PYTHON,
    path.join(DEFAULT_ROOT, '.venv', 'Scripts', 'python.exe'),
    path.join(DEFAULT_ROOT, '.venv', 'bin', 'python'),
    process.platform === 'win32' ? 'python' : 'python3',
  ].filter(Boolean)
  const py = candidates.find((p) => (p.includes(path.sep) ? fs.existsSync(p) : true))
  const r = spawnSync(py, [path.join(HERE, 'python_function_budget.py'), ROOT], {
    cwd: ROOT,
    encoding: 'utf8',
    timeout: 120000,
  })
  if (r.status !== 0)
    throw new Error('python function scan failed: ' + String(r.stderr || r.error || ''))
  return JSON.parse(r.stdout)
}

function key(f) {
  return f.file + '::' + f.name
}

function validDate(value) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return false
  const parts = value.split('-').map(Number)
  const date = new Date(Date.UTC(parts[0], parts[1] - 1, parts[2]))
  return (
    date.getUTCFullYear() === parts[0] &&
    date.getUTCMonth() === parts[1] - 1 &&
    date.getUTCDate() === parts[2]
  )
}

/** Minimal shape validation for the registry file itself. */
function readRegistry() {
  const failures = []
  let raw
  try {
    raw = JSON.parse(fs.readFileSync(CONFIG, 'utf8'))
  } catch (error) {
    const reason = error instanceof Error ? error.message : String(error)
    return {
      entries: [],
      failures: [
        REGISTRY +
          ': registry unreadable (' +
          reason +
          '); every soft-band function is therefore unregistered',
      ],
    }
  }
  if (!isPlainObject(raw)) {
    failures.push(REGISTRY + ': registry must be a JSON object')
    return { entries: [], failures }
  }
  if (raw.version !== 1) failures.push(REGISTRY + ': version must be 1')
  if (typeof raw.description !== 'string' || raw.description.trim().length < 40) {
    failures.push(REGISTRY + ': description must document the registry role (>= 40 characters)')
  }
  if (!Array.isArray(raw.functions)) failures.push(REGISTRY + ': functions must be an array')
  return { entries: Array.isArray(raw.functions) ? raw.functions : [], failures }
}

/** Validate one registry entry; returns human-readable failures. */
function checkEntry(entry) {
  const failures = []
  if (!isPlainObject(entry)) return ['entry must be an object']
  for (const field of ['file', 'name', 'classification', 'reason', 'owner', 'reviewBy']) {
    if (typeof entry[field] !== 'string' || entry[field].trim() === '') {
      failures.push('missing non-empty string field ' + field)
    }
  }
  if (entry.lines !== undefined && (!Number.isInteger(entry.lines) || entry.lines <= 0)) {
    failures.push('lines must be a positive integer when present')
  }
  if (typeof entry.classification === 'string' && entry.classification.trim() !== '') {
    const cls = CLASS_BY_ID.get(entry.classification)
    if (!cls) {
      failures.push(
        'classification "' +
          entry.classification +
          '" is not registered; use one of ' +
          CLASS_IDS.join(', '),
      )
    } else if (entry.owner !== cls.owner) {
      failures.push(
        'owner "' + entry.owner + '" must be the class owner "' + cls.owner + '" for ' + cls.id,
      )
    }
  }
  if (typeof entry.reason === 'string' && entry.reason.trim().length < MIN_REASON_LENGTH) {
    failures.push('reason must be at least ' + MIN_REASON_LENGTH + ' characters')
  }
  if (typeof entry.reviewBy === 'string' && entry.reviewBy.trim() !== '') {
    const reviewBy = entry.reviewBy.trim()
    if (!validDate(reviewBy)) {
      failures.push('reviewBy must be a valid YYYY-MM-DD date - ' + reviewBy)
    } else if (reviewBy < TODAY) {
      failures.push('reviewBy expired - ' + reviewBy + ' < ' + TODAY)
    }
  }
  if (typeof entry.file === 'string' && entry.file.trim() !== '') {
    const normalized = entry.file.replace(/\\/g, '/')
    if (path.isAbsolute(entry.file) || normalized.split('/').includes('..')) {
      failures.push('file must be a repo-relative path - ' + entry.file)
    } else if (!fs.existsSync(path.resolve(ROOT, normalized))) {
      failures.push('file does not exist - ' + normalized)
    }
  }
  return failures
}

const tsFiles = []
walk(path.join(ROOT, 'src'), ['.ts'], tsFiles)
walk(path.join(ROOT, 'runtime'), ['.ts'], tsFiles)
// Generated sources are derived from config/routing.json, validated by check:routes,
// and are not hand-authored production code; they stay out of the budget and the ledger.
const isGeneratedSource = (file) => {
  try {
    return fs.readFileSync(file, 'utf8').startsWith(GENERATED_HEADER)
  } catch {
    return false
  }
}
const authoredTsFiles = tsFiles.filter((file) => !isGeneratedSource(file))
const generatedSkipped = tsFiles.length - authoredTsFiles.length
const tsFns = authoredTsFiles.flatMap(tsFunctions)
const pyFns = pythonFunctions()
const allFns = tsFns.concat(pyFns)
const hard = allFns.filter((f) => f.lines > HARD)
const band = allFns.filter((f) => f.lines > TARGET && f.lines <= HARD)
const bandKeys = new Set(band.map(key))

if (REFRESH) {
  const existing = new Map()
  const previous = readRegistry()
  for (const entry of previous.entries) {
    if (isPlainObject(entry) && typeof entry.file === 'string' && typeof entry.name === 'string') {
      existing.set(entry.file + '::' + entry.name, entry)
    }
  }
  let added = 0
  const functions = band
    .map((f) => {
      const previousEntry = existing.get(key(f))
      if (previousEntry) return { ...previousEntry, file: f.file, name: f.name, lines: f.lines }
      added += 1
      return {
        file: f.file,
        name: f.name,
        lines: f.lines,
        classification: 'unclassified',
        reason:
          'New soft-band function; classify its real role during review before the gate passes.',
        owner: 'lead',
        reviewBy: DEFAULT_REVIEW_BY,
      }
    })
    .sort((a, b) => a.file.localeCompare(b.file) || a.name.localeCompare(b.name))
  const payload = {
    version: 1,
    description: REGISTRY_DESCRIPTION,
    generatedAt: new Date().toISOString(),
    functions,
  }
  fs.mkdirSync(path.dirname(CONFIG), { recursive: true })
  fs.writeFileSync(CONFIG, JSON.stringify(payload, null, 2) + '\n', 'utf8')
  console.log(
    'check-function-budget: registry refreshed at ' +
      REGISTRY +
      ' (' +
      functions.length +
      ' band functions; ' +
      added +
      ' new/unclassified)',
  )
  if (hard.length > 0) {
    console.log(
      '  warning: ' +
        hard.length +
        ' function(s) exceed the 120-line hard limit and will fail the gate',
    )
  }
  process.exit(0)
}

const registry = readRegistry()
const failures = registry.failures.slice()
const byKey = new Map()
registry.entries.forEach((entry, index) => {
  const label = 'functions[' + index + ']'
  const entryFailures = checkEntry(entry)
  const named =
    isPlainObject(entry) && typeof entry.file === 'string' && typeof entry.name === 'string'
  const suffix = named ? ' (' + entry.file + '::' + entry.name + ')' : ''
  for (const failure of entryFailures) failures.push(label + suffix + ': ' + failure)
  if (!named) return
  const entryKey = entry.file + '::' + entry.name
  if (byKey.has(entryKey)) failures.push(label + suffix + ': duplicate registry entry')
  else byKey.set(entryKey, entry)
})

const violations = []
for (const f of hard) {
  violations.push({
    kind: 'hard-function',
    file: f.file,
    name: f.name,
    lines: f.lines,
    why: 'exceeds the 120-line hard limit (no registry waiver)',
  })
}
for (const f of band) {
  if (!byKey.has(key(f))) {
    violations.push({
      kind: 'band-function',
      file: f.file,
      name: f.name,
      lines: f.lines,
      why: 'not registered in ' + REGISTRY,
    })
  }
}
const staleFunctions = registry.entries
  .filter(
    (entry) =>
      isPlainObject(entry) &&
      typeof entry.file === 'string' &&
      typeof entry.name === 'string' &&
      !bandKeys.has(entry.file + '::' + entry.name),
  )
  .map((entry) => ({ file: entry.file, name: entry.name, lines: entry.lines }))
for (const entry of staleFunctions) {
  violations.push({
    kind: 'stale-function',
    file: entry.file,
    name: entry.name,
    why: 'registered but no longer in the 80-120 band; run --refresh',
  })
}

const report = {
  ok: failures.length === 0 && violations.length === 0,
  tool: 'check-function-budget',
  generatedAt: new Date().toISOString(),
  node: process.version,
  thresholds: { target: TARGET, hard: HARD },
  registry: REGISTRY,
  classes: CLASSES.map((entry) => ({
    id: entry.id,
    owner: entry.owner,
    description: entry.description,
  })),
  counts: {
    tsFiles: authoredTsFiles.length,
    generatedSkipped,
    tsFunctions: tsFns.length,
    pythonFunctions: pyFns.length,
    band: band.length,
    hard: hard.length,
    stale: staleFunctions.length,
    registered: byKey.size,
  },
  bandFunctions: band,
  hardFunctions: hard,
  staleFunctions,
  registryFailures: failures,
  violations,
}
const text = JSON.stringify(report, null, 2) + '\n'
if (OUT_PATH) {
  fs.mkdirSync(path.dirname(path.resolve(ROOT, OUT_PATH)), { recursive: true })
  fs.writeFileSync(path.resolve(ROOT, OUT_PATH), text, 'utf8')
}
if (JSON_ONLY) process.stdout.write(text)
else {
  console.log('check-function-budget: ' + (report.ok ? 'PASS' : 'FAIL'))
  console.log(
    '  functions: ' +
      allFns.length +
      ' total; >120: ' +
      hard.length +
      '; 80-120 band: ' +
      band.length +
      ' (' +
      byKey.size +
      ' registered; stale ' +
      staleFunctions.length +
      ')',
  )
  console.log('  registry: ' + REGISTRY)
  for (const failure of failures.slice(0, 20)) console.log('  x ' + failure)
  if (failures.length > 20)
    console.log('  ... ' + (failures.length - 20) + ' more registry failures')
  for (const v of violations.slice(0, 20))
    console.log(
      '  x ' +
        v.kind +
        ' ' +
        v.file +
        (v.name ? '::' + v.name : '') +
        (v.lines ? ' ' + v.lines : '') +
        ' (' +
        v.why +
        ')',
    )
  if (violations.length > 20) console.log('  ... ' + (violations.length - 20) + ' more violations')
}
process.exit(report.ok ? 0 : 1)
