#!/usr/bin/env node
/**
 * Python static gate.
 *
 * Ruff is the authoritative "real error" check for every tracked Python file.
 * There is no checked-in baseline: every violation fails the gate. Pyright is
 * intentionally narrow: it checks only the pure-logic modules listed in
 * pyrightconfig.python.json. It is not a claim that runtime/py is fully typed.
 *
 * The scope itself is part of the gate: every tracked .py file must live under
 * one of PYTHON_ROOTS, and every pyright include must exist and be unique.
 * CHECK_PYTHON_TRACKED_FILES is a test-only seam (newline separated); normal
 * runs use `git ls-files '*.py'`.
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

export const PYTHON_ROOTS = [
  'runtime/py',
  'tests',
  'extension/dsh_blender_bridge',
  'scripts',
  'docs/examples',
]
export const PYRIGHT_CONFIGS = ['pyrightconfig.python.json', 'pyrightconfig.extension.json']
const PYRIGHT_CONFIG = PYRIGHT_CONFIGS[0]
const SCOPE_ONLY = process.argv.includes('--scope-only')

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

function trackedPythonFiles() {
  const injected = process.env.CHECK_PYTHON_TRACKED_FILES
  if (injected !== undefined) {
    const files = injected
      .split(/\r?\n/)
      .map((file) => file.trim())
      .filter(Boolean)
    if (!files.length) {
      fail('CHECK_PYTHON_TRACKED_FILES is empty; refusing to treat an empty list as a pass')
    }
    return files
  }

  const result = spawnSync('git', ['ls-files', '*.py'], { cwd: PKG, encoding: 'utf8' })
  if (result.error) {
    fail('git ls-files failed to start: ' + String(result.error.message || result.error))
  }
  if (result.status !== 0) {
    fail(
      'git ls-files failed: ' +
        String(result.stderr || '').trim() +
        '; the Python scope gate needs a git checkout',
    )
  }
  const files = String(result.stdout || '')
    .split(/\r?\n/)
    .map((file) => file.trim())
    .filter(Boolean)
  if (!files.length) fail('git ls-files returned no Python files; refusing to treat it as a pass')
  return files
}

function assertPythonScope() {
  const files = trackedPythonFiles()
  const roots = PYTHON_ROOTS.map((root) => root.replace(/\/+$/, ''))
  const outside = files.filter(
    (file) => !roots.some((root) => file === root || file.startsWith(root + '/')),
  )
  if (outside.length) {
    fail(
      'tracked Python files outside the Python static roots (' +
        roots.join(', ') +
        '). Move each file under an existing root or add a root to PYTHON_ROOTS with a reason:\n  x ' +
        outside.join('\n  x '),
    )
  }

  for (const config of PYRIGHT_CONFIGS) {
    const configPath = path.join(PKG, config)
    if (!fs.existsSync(configPath)) fail('missing ' + config)
    let parsed
    try {
      parsed = JSON.parse(fs.readFileSync(configPath, 'utf8'))
    } catch (error) {
      fail('cannot parse ' + config + ': ' + String(error.message || error))
    }
    const include = parsed && parsed.include
    if (!Array.isArray(include) || !include.length) {
      fail(config + ' must contain a non-empty include array')
    }
    const seen = new Set()
    for (const entry of include) {
      if (typeof entry !== 'string' || !entry) fail(config + ' contains an invalid include entry')
      if (seen.has(entry)) fail(config + ' contains a duplicate include: ' + entry)
      seen.add(entry)
      const absolute = path.join(PKG, entry)
      if (!fs.existsSync(absolute) || !fs.statSync(absolute).isFile()) {
        fail(config + ' include does not exist as a file: ' + entry)
      }
    }
  }

  console.log(
    'check-python-static: scope clean (' +
      files.length +
      ' tracked Python files; pyright includes exist)',
  )
  return files
}

function runRuff() {
  const ruff = run(PYTHON, ['-m', 'ruff', 'check', ...PYTHON_ROOTS, '--no-cache'], 'ruff')
  if (ruff.status !== 0) {
    console.error('check-python-static: ruff failed')
    if (ruff.stdout) console.error(ruff.stdout)
    if (ruff.stderr) console.error(ruff.stderr)
    process.exit(1)
  }
  console.log('check-python-static: ruff clean')
}

function runPyright() {
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
}

function main() {
  assertPythonScope()
  if (SCOPE_ONLY) return
  runRuff()
  runPyright()
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) main()
