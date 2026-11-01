#!/usr/bin/env node
/**
 * Python static gate.
 *
 * Ruff is the authoritative "real error" check for runtime/py, tests and the
 * addon. There is no checked-in baseline: every violation fails the gate.
 * Pyright is intentionally narrow: it checks only the pure-logic modules listed
 * in pyrightconfig.python.json. It is not a claim that runtime/py is fully typed.
 */
import { spawnSync } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const PKG = path.resolve(HERE, '..')
const VENV_PYTHON = path.join(
  PKG,
  '.venv',
  process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python',
)
const PYTHON =
  process.env.PYTHON ||
  (fs.existsSync(VENV_PYTHON) ? VENV_PYTHON : process.platform === 'win32' ? 'python' : 'python3')
const PYRIGHT_CONFIG = 'pyrightconfig.python.json'

function fail(message) {
  console.error('check-python-static: ' + message)
  process.exit(1)
}

function run(command, args, label) {
  const result = spawnSync(command, args, { cwd: PKG, encoding: 'utf8', env: process.env })
  if (result.error) {
    fail(
      label +
        ' failed to start: ' +
        String(result.error.message || result.error) +
        '; install the documented Python/Node tooling first',
    )
  }
  return result
}

const ruff = run(
  PYTHON,
  [
    '-m',
    'ruff',
    'check',
    'runtime/py',
    'tests',
    'addon/dsh_blender_bridge',
    '--no-cache',
  ],
  'ruff',
)
if (ruff.status !== 0) {
  console.error('check-python-static: ruff failed')
  if (ruff.stdout) console.error(ruff.stdout)
  if (ruff.stderr) console.error(ruff.stderr)
  process.exit(1)
}
console.log('check-python-static: ruff clean')

const pyrightCli = path.join(PKG, 'node_modules', 'pyright', 'index.js')
if (!fs.existsSync(pyrightCli)) fail('pyright is not installed; run npm ci')
if (!fs.existsSync(path.join(PKG, PYRIGHT_CONFIG))) fail('missing ' + PYRIGHT_CONFIG)
const pyright = run(process.execPath, [pyrightCli, '-p', PYRIGHT_CONFIG], 'pyright')
if (pyright.status !== 0) {
  console.error('check-python-static: pyright failed')
  if (pyright.stdout) console.error(pyright.stdout)
  if (pyright.stderr) console.error(pyright.stderr)
  process.exit(1)
}
console.log('check-python-static: pyright clean (' + PYRIGHT_CONFIG + ')')
