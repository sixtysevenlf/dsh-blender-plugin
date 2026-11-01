#!/usr/bin/env node
// F6.1: modules.json is the only Python module map; every module resolves on disk.
import { existsSync, readFileSync, readdirSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const PY = path.join(ROOT, 'runtime', 'py')
const manifest = JSON.parse(readFileSync(path.join(PY, 'modules.json'), 'utf8'))
const failures = []
for (const [name, domain] of Object.entries(manifest.modules || {})) {
  if (!domain || domain === '.') failures.push(name + ': invalid domain ' + JSON.stringify(domain))
  const file = path.join(PY, String(domain), name + '.py')
  if (!existsSync(file)) failures.push(name + ': missing ' + path.relative(ROOT, file))
  if (existsSync(path.join(ROOT, 'runtime', name + '.py')))
    failures.push(name + ': flat shim still exists')
}
for (const domain of manifest.domains || []) {
  const dir = path.join(PY, domain)
  if (!existsSync(path.join(dir, '__init__.py')))
    failures.push('domain ' + domain + ': missing __init__.py')
}
const flat = readdirSync(path.join(ROOT, 'runtime')).filter((f) => f.endsWith('.py'))
if (flat.length) failures.push('runtime/*.py not empty: ' + flat.join(', '))
if (!/modules\.json/.test(readFileSync(path.join(ROOT, 'runtime', 'paths.ts'), 'utf8'))) {
  failures.push('runtime/paths.ts does not reference modules.json')
}
if (failures.length) {
  console.error('check-runtime-layout: FAIL')
  for (const f of failures) console.error('  x ' + f)
  process.exit(1)
}
console.log(
  'check-runtime-layout: PASS (' +
    Object.keys(manifest.modules).length +
    ' modules in ' +
    (manifest.domains || []).length +
    ' domains)',
)
