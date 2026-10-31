#!/usr/bin/env node
/**
 * Explicit-any gate.
 *
 * Scans production TypeScript (src/ + runtime/, excluding generated/vendored
 * trees) for the explicit `any` keyword using the TypeScript AST. This is an
 * AST check, not a regex: comments and string literals never match, and
 * `unknown` / `never` are unaffected.
 *
 * T5a / B8: the foundation surface and both production trees must stay at
 * zero explicit any. Tests may use any for host doubles.
 */
import { readdirSync, readFileSync } from 'node:fs'
import path from 'node:path'
import ts from 'typescript'

const ROOT = process.cwd()
const ROOTS = ['src', 'runtime']
const IGNORED_DIRS = new Set([
  // Generated, vendored or non-TS trees only. runtime/ext is tracked source
  // (gltf_validate_runner.ts) and must be scanned.
  'node_modules',
  'lib',
  'coverage',
  'dist',
  '.venv',
  '__pycache__',
])
const IGNORED_FILES = new Set()

/** @param {string} dir @param {string[]} out */
function walk(dir, out) {
  let entries
  try {
    entries = readdirSync(dir, { withFileTypes: true })
  } catch {
    return
  }
  for (const entry of entries) {
    const full = path.join(dir, entry.name)
    if (entry.isDirectory()) {
      if (IGNORED_DIRS.has(entry.name)) continue
      walk(full, out)
    } else if (entry.isFile()) {
      if (IGNORED_FILES.has(entry.name)) continue
      if (!/\.(ts|mts|cts)$/.test(entry.name)) continue
      if (entry.name.endsWith('.d.ts')) continue
      out.push(full)
    }
  }
}

/** @param {string} file @returns {{line:number,column:number,text:string}[]} */
function anyInFile(file) {
  const source = readFileSync(file, 'utf8')
  const kind = file.endsWith('.mts') || file.endsWith('.cts') ? ts.ScriptKind.TS : ts.ScriptKind.TS
  const sf = ts.createSourceFile(file, source, ts.ScriptTarget.Latest, true, kind)
  const hits = []
  /** @param {ts.Node} node */
  const visit = (node) => {
    if (node.kind === ts.SyntaxKind.AnyKeyword) {
      const pos = sf.getLineAndCharacterOfPosition(node.getStart(sf))
      hits.push({
        line: pos.line + 1,
        column: pos.character + 1,
        text: source.split(/\r?\n/)[pos.line].trim().slice(0, 160),
      })
    }
    ts.forEachChild(node, visit)
  }
  visit(sf)
  return hits
}

const files = []
for (const root of ROOTS) walk(path.join(ROOT, root), files)
files.sort()

const results = []
let total = 0
for (const file of files) {
  const hits = anyInFile(file)
  if (!hits.length) continue
  total += hits.length
  results.push({ file: path.relative(ROOT, file).replaceAll(path.sep, '/'), hits })
}

if (!total) {
  console.log(
    'check:any PASS — ' + String(files.length) + ' files, 0 explicit any in src/ and runtime/',
  )
  process.exit(0)
}

console.error(
  'check:any FAIL — ' + String(total) + ' explicit any in ' + String(results.length) + ' files:',
)
for (const { file, hits } of results) {
  console.error('  ' + file)
  for (const hit of hits) {
    console.error('    ' + String(hit.line) + ':' + String(hit.column) + '  ' + hit.text)
  }
}
process.exit(1)
