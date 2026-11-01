#!/usr/bin/env node
// F6.1: relative doc links and npm scripts referenced by the AI/human guides exist.
import { existsSync, readFileSync, readdirSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const DOCS = ['README.md', 'AGENTS.md', 'CONTRIBUTING.md', 'MIGRATIONS.md']
for (const f of readdirSync(path.join(ROOT, 'docs'))) if (f.endsWith('.md')) DOCS.push('docs/' + f)
const pkg = JSON.parse(readFileSync(path.join(ROOT, 'package.json'), 'utf8'))
const stripCode = (text) => text.replace(/```[\s\S]*?```/g, '').replace(/`[^`\n]*`/g, '')
const failures = []
for (const rel of DOCS) {
  const file = path.join(ROOT, rel)
  if (!existsSync(file)) continue
  const text = stripCode(readFileSync(file, 'utf8'))
  for (const m of text.matchAll(/\[[^\]]*\]\(([^)]+)\)/g)) {
    const target = m[1].trim()
    if (!target || target.startsWith('#') || /^[a-z]+:/i.test(target)) continue
    const clean = target.split('#')[0].split('?')[0]
    if (!clean) continue
    const resolved = path.resolve(path.dirname(file), clean)
    if (!resolved.startsWith(ROOT)) failures.push(rel + ': link escapes repo: ' + target)
    else if (!existsSync(resolved)) failures.push(rel + ': missing link target: ' + target)
  }
  for (const m of text.matchAll(/npm run ([a-z0-9:_-]+)/g)) {
    if (!pkg.scripts || !Object.prototype.hasOwnProperty.call(pkg.scripts, m[1])) {
      failures.push(rel + ': unknown npm script: ' + m[1])
    }
  }
}
if (failures.length) {
  console.error('check-doc-paths: FAIL')
  for (const f of failures.slice(0, 40)) console.error('  x ' + f)
  if (failures.length > 40) console.error('  ... ' + (failures.length - 40) + ' more')
  process.exit(1)
}
console.log('check-doc-paths: PASS (' + DOCS.length + ' docs)')
