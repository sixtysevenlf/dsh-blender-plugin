#!/usr/bin/env node
// F6.1: no flat compatibility shims and no retired dual-entry files.
import { existsSync, readdirSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const failures = []
const runtimePy = readdirSync(path.join(ROOT, 'runtime')).filter((f) => f.endsWith('.py'))
if (runtimePy.length) failures.push('flat runtime/*.py files: ' + runtimePy.join(', '))
for (const retired of [
  'src/tui-host.ts',
  'runtime/execution/headless.ts',
  'runtime/core/server-compat.ts',
]) {
  if (existsSync(path.join(ROOT, retired))) failures.push('retired file still present: ' + retired)
}
if (failures.length) {
  console.error('check-no-shims: FAIL')
  for (const f of failures) console.error('  x ' + f)
  process.exit(1)
}
console.log('check-no-shims: PASS (no flat shims, no retired entry files)')
