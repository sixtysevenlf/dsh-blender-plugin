#!/usr/bin/env node
/**
 * scripts/check-function-budget.mjs — finalization F1.0 function/file budget.
 *
 * Target: every production function <=80 lines; 80-120 allowed only as a
 * reported target warning; >120 fails. Files: TS/addon <=600, runtime/py <=1000.
 * The allowlist is a debt ledger: every entry needs reason/owner/expires. Adding
 * a new >120 function is a hard failure, so a split cannot silently regress.
 *
 * Usage:
 *   node scripts/check-function-budget.mjs
 *   node scripts/check-function-budget.mjs --json --out <path>
 *   node scripts/check-function-budget.mjs --baseline
 */
import { spawnSync } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import ts from 'typescript'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const ROOT = path.resolve(HERE, '..')
const ARGV = process.argv.slice(2)
const JSON_ONLY = ARGV.includes('--json')
const BASELINE = ARGV.includes('--baseline')
const OUT_IDX = ARGV.indexOf('--out')
const OUT_PATH = OUT_IDX >= 0 ? ARGV[OUT_IDX + 1] : null
const ALLOWLIST = path.join(ROOT, 'config', 'function-budget-allowlist.json')
const TARGET = 80
const HARD = 120
const TS_FILE_BUDGET = 600
const PY_FILE_BUDGET = 1000
const TODAY = new Date().toISOString().slice(0, 10)
const rel = (p) => path.relative(ROOT, p).split(path.sep).join('/')

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
  const candidates = [
    process.env.PYTHON,
    path.join(ROOT, '.venv', 'Scripts', 'python.exe'),
    path.join(ROOT, '.venv', 'bin', 'python'),
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

function loadAllowlist() {
  try {
    const raw = JSON.parse(fs.readFileSync(ALLOWLIST, 'utf8'))
    return { functions: raw.functions || [], files: raw.files || [] }
  } catch {
    return { functions: [], files: [] }
  }
}

function key(f) {
  return f.file + '::' + f.name
}

function validEntry(e) {
  return (
    e &&
    typeof e.reason === 'string' &&
    e.reason.length > 0 &&
    typeof e.owner === 'string' &&
    e.owner.length > 0 &&
    typeof e.expires === 'string' &&
    e.expires.length > 0
  )
}

const tsFiles = []
walk(path.join(ROOT, 'src'), ['.ts'], tsFiles)
walk(path.join(ROOT, 'runtime'), ['.ts'], tsFiles)
const tsFns = tsFiles.flatMap(tsFunctions)
const pyFns = pythonFunctions()
const allFns = tsFns.concat(pyFns)
const fileRows = []
for (const file of tsFiles) {
  const lines = fs.readFileSync(file, 'utf8').split(/\r?\n/).length
  if (lines > TS_FILE_BUDGET) fileRows.push({ file: rel(file), lines, budget: TS_FILE_BUDGET })
}
for (const file of [...new Set(pyFns.map((f) => f.file))]) {
  const lines = fs.readFileSync(path.join(ROOT, file), 'utf8').split(/\r?\n/).length
  if (lines > PY_FILE_BUDGET) fileRows.push({ file, lines, budget: PY_FILE_BUDGET })
}
const hard = allFns.filter((f) => f.lines > HARD)
const targetWarn = allFns.filter((f) => f.lines > TARGET && f.lines <= HARD)
const allow = loadAllowlist()
const allowFns = new Map(allow.functions.map((e) => [e.file + '::' + e.name, e]))
const allowFiles = new Map(allow.files.map((e) => [e.file, e]))

if (BASELINE) {
  const payload = {
    generatedAt: new Date().toISOString(),
    reason: 'Baseline debt captured by F1.0; burn down in F1/F2/F3.',
    owner: 'lead',
    expires: '2026-12-31',
    functions: hard.map((f) => ({
      file: f.file,
      name: f.name,
      lines: f.lines,
      reason: 'Baseline; split in F1.1/F2.1/F2.2/F3.2',
      owner: 'lead',
      expires: '2026-12-31',
    })),
    files: fileRows.map((f) => ({
      file: f.file,
      lines: f.lines,
      reason: 'Baseline; split in F1/F2/F3',
      owner: 'lead',
      expires: '2026-12-31',
    })),
  }
  fs.mkdirSync(path.dirname(ALLOWLIST), { recursive: true })
  fs.writeFileSync(ALLOWLIST, JSON.stringify(payload, null, 2) + '\n', 'utf8')
  console.log(
    'check-function-budget: baseline written to ' +
      rel(ALLOWLIST) +
      ' (' +
      hard.length +
      ' functions, ' +
      fileRows.length +
      ' files)',
  )
  process.exit(0)
}

const violations = []
for (const f of hard) {
  const e = allowFns.get(key(f))
  if (!e)
    violations.push({
      kind: 'function',
      file: f.file,
      name: f.name,
      lines: f.lines,
      why: 'not allowlisted',
    })
  else if (!validEntry(e))
    violations.push({
      kind: 'function',
      file: f.file,
      name: f.name,
      lines: f.lines,
      why: 'allowlist entry missing reason/owner/expires',
    })
  else if (e.expires < TODAY)
    violations.push({
      kind: 'function',
      file: f.file,
      name: f.name,
      lines: f.lines,
      why: 'allowlist expired ' + e.expires,
    })
}
for (const f of fileRows) {
  const e = allowFiles.get(f.file)
  if (!e)
    violations.push({
      kind: 'file',
      file: f.file,
      lines: f.lines,
      budget: f.budget,
      why: 'not allowlisted',
    })
  else if (!validEntry(e))
    violations.push({
      kind: 'file',
      file: f.file,
      lines: f.lines,
      budget: f.budget,
      why: 'allowlist entry missing reason/owner/expires',
    })
  else if (e.expires < TODAY)
    violations.push({
      kind: 'file',
      file: f.file,
      lines: f.lines,
      budget: f.budget,
      why: 'allowlist expired ' + e.expires,
    })
}
const stale = allow.functions.filter(
  (e) => !hard.some((f) => f.file === e.file && f.name === e.name),
)
const report = {
  ok: violations.length === 0,
  tool: 'check-function-budget',
  generatedAt: new Date().toISOString(),
  node: process.version,
  thresholds: { target: TARGET, hard: HARD, tsFile: TS_FILE_BUDGET, pyFile: PY_FILE_BUDGET },
  counts: {
    tsFiles: tsFiles.length,
    tsFunctions: tsFns.length,
    pythonFunctions: pyFns.length,
    hard: hard.length,
    targetWarn: targetWarn.length,
    filesOver: fileRows.length,
  },
  hardFunctions: hard,
  fileOverBudget: fileRows,
  violations,
  staleAllowlistFunctions: stale.length,
  hidden: targetWarn.slice(0, 20),
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
      '; 80-120: ' +
      targetWarn.length,
  )
  console.log(
    '  files over budget: ' +
      fileRows.length +
      ' / ' +
      (tsFiles.length + new Set(pyFns.map((f) => f.file)).size),
  )
  console.log('  stale allowlist entries: ' + stale.length)
  for (const v of violations.slice(0, 20))
    console.log(
      '  x ' +
        v.kind +
        ' ' +
        v.file +
        (v.name ? '::' + v.name : '') +
        ' ' +
        v.lines +
        ' (' +
        v.why +
        ')',
    )
  if (violations.length > 20) console.log('  ... ' + (violations.length - 20) + ' more')
}
process.exit(violations.length ? 1 : 0)
