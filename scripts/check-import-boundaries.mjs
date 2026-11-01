#!/usr/bin/env node
// F6.1: layer boundaries for TS host -> lib/runtime, Python domains, and addon layers.
import { readFileSync, readdirSync, existsSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const failures = []
const rel = (p) => path.relative(ROOT, p).split(path.sep).join('/')
const walk = (dir, out = []) => {
  if (!existsSync(dir)) return out
  for (const ent of readdirSync(dir, { withFileTypes: true })) {
    if (ent.name === 'node_modules' || ent.name === '__pycache__') continue
    const p = path.join(dir, ent.name)
    if (ent.isDirectory()) walk(p, out)
    else out.push(p)
  }
  return out
}
for (const file of walk(path.join(ROOT, 'src'))) {
  if (!file.endsWith('.ts')) continue
  const text = readFileSync(file, 'utf8')
  if (
    /from\s+['"](?:\.\.\/)+lib\/runtime|from\s+['"]lib\/runtime/.test(text) &&
    rel(file) !== 'src/host/runtime-bridge.ts'
  ) {
    failures.push('src imports lib/runtime internals: ' + rel(file))
  }
}
const PY = path.join(ROOT, 'runtime', 'py')
const manifest = JSON.parse(readFileSync(path.join(PY, 'modules.json'), 'utf8'))
for (const domain of manifest.domains || []) {
  for (const file of walk(path.join(PY, domain))) {
    if (!file.endsWith('.py')) continue
    const text = readFileSync(file, 'utf8')
    for (const other of manifest.domains || []) {
      if (other === domain) continue
      const re = new RegExp(
        '(from|import)\\s+\\.\\.' + other + '\\b|runtime\\.py\\.' + other + '\\b',
      )
      if (re.test(text))
        failures.push('python cross-domain ' + domain + ' -> ' + other + ': ' + rel(file))
    }
  }
}
const addonAssets = path.join(ROOT, 'addon', 'dsh_blender_bridge', 'assets', 'io.py')
if (
  existsSync(addonAssets) &&
  /^\s*(from|import)\s+.*commands/m.test(readFileSync(addonAssets, 'utf8'))
) {
  failures.push('addon assets/io.py imports commands')
}
for (const file of walk(path.join(ROOT, 'addon', 'dsh_blender_bridge', 'ui', 'panels'))) {
  if (
    file.endsWith('.py') &&
    /from\s+\.\.\.integrations|from\s+\.\.integrations|import\s+integrations/.test(
      readFileSync(file, 'utf8'),
    )
  ) {
    failures.push('addon ui/panels imports integrations: ' + rel(file))
  }
}
if (failures.length) {
  console.error('check-import-boundaries: FAIL')
  for (const f of failures) console.error('  x ' + f)
  process.exit(1)
}
console.log('check-import-boundaries: PASS (src/runtime, python domains, addon layers)')
