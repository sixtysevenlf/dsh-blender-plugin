#!/usr/bin/env node
/**
 * scripts/test.mjs — one entry point for the layered test stack.
 *
 * Usage:
 *   node scripts/test.mjs            # Vitest (unit + integration) + pytest
 *   node scripts/test.mjs js         # Vitest only
 *   node scripts/test.mjs python     # pytest only
 *   node scripts/test.mjs unit       # Vitest unit only
 *   node scripts/test.mjs integration  # Vitest integration only
 *   node scripts/test.mjs extension-unit    # pytest: Blender addon unit tests only
 *   node scripts/test.mjs python-static # ruff + pure-logic pyright gate
 *
 * Legacy selftest scripts are retired; see tests/MIGRATION.md for the mapping.
 */
import { spawnSync } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const PKG = path.resolve(HERE, '..')
const mode = (process.argv[2] || 'all').toLowerCase()
const VALID = new Set([
  'all',
  'js',
  'python',
  'unit',
  'integration',
  'contract',
  'extension-unit',
  'python-static',
  'e2e',
  'e2e:gui',
])
if (!VALID.has(mode)) {
  console.error('test: unknown mode "' + mode + '"; expected one of ' + [...VALID].join(', '))
  process.exit(2)
}

function fail(msg) {
  console.error('test: ' + msg)
  process.exit(2)
}

function run(file, args, label) {
  console.log('\n== ' + label + ' ==')
  const r = spawnSync(file, args, { cwd: PKG, stdio: 'inherit', env: process.env })
  if (r.error) fail(label + ' failed to start: ' + String(r.error.message || r.error))
  if (r.status !== 0) process.exit(typeof r.status === 'number' ? r.status : 1)
}

function runBudget() {
  run(process.execPath, [path.join(PKG, 'scripts', 'check-function-budget.mjs')], 'Function budget')
}

function runJs() {
  const vitest = path.join(PKG, 'node_modules', 'vitest', 'vitest.mjs')
  if (!fs.existsSync(vitest)) fail('vitest not installed; run npm install first')
  const args = [vitest, 'run']
  if (mode === 'unit') args.push('tests/unit')
  if (mode === 'integration') args.push('tests/integration')
  if (mode === 'contract') args.push('tests/unit/js/contract')
  run(process.execPath, args, 'Vitest (' + (mode === 'all' ? 'unit + integration' : mode) + ')')
}

function python() {
  if (process.env.PYTHON) return process.env.PYTHON
  const venv = path.join(
    PKG,
    '.venv',
    process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python',
  )
  if (fs.existsSync(venv)) return venv
  return process.platform === 'win32' ? 'python' : 'python3'
}

function runPythonStatic() {
  run(
    process.execPath,
    [path.join(PKG, 'scripts', 'check-python-static.mjs')],
    'Python static (ruff + pure-logic pyright)',
  )
}

function runPython() {
  const py = python()
  const probe = spawnSync(py, ['-m', 'pytest', '--version'], { cwd: PKG, encoding: 'utf8' })
  if (probe.status !== 0) {
    fail('pytest is not available via "' + py + ' -m pytest"; install -r requirements-dev.txt')
  }
  if (mode === 'extension-unit') {
    run(
      py,
      ['-m', 'pytest', 'tests/unit/python', '-k', 'addon'],
      'pytest (Blender addon unit tests, bpy stubs, no Blender)',
    )
    return
  }
  if (mode === 'e2e') {
    // pyproject.toml excludes blender/gui by default; clear addopts for the E2E layer.
    run(
      py,
      ['-m', 'pytest', '-o', 'addopts=', '-m', 'blender and not gui', 'tests/e2e'],
      'pytest (Blender headless E2E; set DSH_BLENDER_EXE)',
    )
    return
  }
  if (mode === 'e2e:gui') {
    run(
      py,
      ['-m', 'pytest', '-o', 'addopts=', '-m', 'gui', 'tests/e2e/gui'],
      'pytest (Blender GUI E2E; set DSH_BLENDER_EXE)',
    )
    return
  }
  run(py, ['-m', 'pytest'], 'pytest (unit + integration, blender/gui excluded)')
}

if (mode === 'all') {
  runBudget()
  runJs()
  runPython()
  runPythonStatic()
} else if (mode === 'js' || mode === 'unit' || mode === 'integration' || mode === 'contract') {
  runBudget()
  runJs()
} else if (mode === 'python-static') {
  runPythonStatic()
} else runPython()
