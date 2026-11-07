#!/usr/bin/env node
// Doc/config port gate: the Gateway default moved from 9877 to 39877 in 3.1.
//
// 9877 may only appear in tests, historical release notes, archived docs, and
// migration snippets that explicitly describe the old default. Everything a
// user is told today must use 39877, so a stray 9877 is a documentation bug or
// a config regression, not a style choice.
//
// The add-on no longer listens on an inbound port in 3.1, so this gate only
// checks the Gateway port. Legacy 9876 mentions are reviewed by hand where
// they describe an old 3.0.x addon or a third-party MCP bridge.

import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const DEFAULT_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const LEGACY_PORT = '9877'
const LEGACY_PORT_RE = new RegExp('(?<!\\d)' + LEGACY_PORT + '(?!\\d)')
const SKIP_DIRS = new Set([
  '.git',
  '.dsh',
  '.dsh-ops',
  '.review-tmp',
  'node_modules',
  'lib',
  'coverage',
  'local-workspace',
  'openspec',
  'tests',
  'docs/archive',
  'pytest-of-villa',
  'tmp',
])
const SKIP_FILES = new Set(['CHANGELOG.md', 'package-lock.json', 'scripts/check-ports.mjs'])
const HISTORY_LINE = /(?:3[.]0[.]x|历史|legacy|migration)/i

function posix(p) {
  return p.split(path.sep).join('/')
}

function walk(root, rel, out) {
  const dir = path.join(root, rel)
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const next = posix(path.join(rel, entry.name))
    if (entry.isDirectory()) {
      if (SKIP_DIRS.has(next) || SKIP_DIRS.has(entry.name) || entry.name.startsWith('.venv'))
        continue
      walk(root, next, out)
    } else if (entry.isFile() && !SKIP_FILES.has(next)) {
      if (/[.](md|json|ts|mts|cts|js|mjs|cjs|py|txt|yml|yaml|sh|ps1)$/i.test(entry.name))
        out.push(next)
    }
  }
}

export function findLegacyPortReferences(root = DEFAULT_ROOT) {
  const files = []
  walk(root, '', files)
  const hits = []
  for (const file of files) {
    const text = fs.readFileSync(path.join(root, file), 'utf8')
    text.split(/\r?\n/).forEach((line, index) => {
      if (LEGACY_PORT_RE.test(line) && !HISTORY_LINE.test(line))
        hits.push({ file, line: index + 1, text: line.trim() })
    })
  }
  return hits
}

function main() {
  const rootArg = process.argv.indexOf('--root')
  const root = rootArg >= 0 ? path.resolve(process.argv[rootArg + 1]) : DEFAULT_ROOT
  const hits = findLegacyPortReferences(root)
  if (hits.length === 0) {
    console.log('check-ports: PASS — no legacy 9877 default-port references outside tests/history')
    return
  }
  console.error('check-ports: FAIL — legacy 9877 default-port references in current files:')
  for (const hit of hits) console.error('  ' + hit.file + ':' + hit.line + '  ' + hit.text)
  console.error('Replace them with 39877 (the 3.1 Gateway default).')
  process.exit(1)
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) main()
