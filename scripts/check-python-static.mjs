#!/usr/bin/env node
/**
 * T5d.4: Python static gate.
 *
 * Ruff is the authoritative "real error" check for runtime/py, tests and the
 * addon. The checked-in allowlist records the historical baseline by
 * (path, rule, count); new violations fail, expired or unowned exemptions fail,
 * and a shrinking baseline produces a warning so the debt is actually retired.
 *
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
const ALLOWLIST = path.join(PKG, 'config', 'python-static-allowlist.json')
const PYRIGHT_CONFIG = 'pyrightconfig.python.json'

function fail(message) {
  console.error('check-python-static: ' + message)
  process.exit(1)
}

function readJson(file) {
  try {
    return JSON.parse(fs.readFileSync(file, 'utf8'))
  } catch (error) {
    fail('cannot read ' + file + ': ' + String(error && error.message ? error.message : error))
  }
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

function normalize(file) {
  return path.relative(PKG, path.resolve(PKG, file)).split(path.sep).join('/')
}

function key(file, code) {
  return file + '\u0000' + code
}

function parseDate(value, label) {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value))
    fail(label + ': expires must be YYYY-MM-DD')
  const time = Date.parse(value + 'T00:00:00Z')
  if (Number.isNaN(time)) fail(label + ': invalid expires ' + value)
  return time
}

const allowlist = readJson(ALLOWLIST)
if (
  !allowlist ||
  typeof allowlist !== 'object' ||
  allowlist.version !== 1 ||
  !Array.isArray(allowlist.entries)
) {
  fail('allowlist must be { version: 1, entries: [] }')
}

const today = Date.parse(new Date().toISOString().slice(0, 10) + 'T00:00:00Z')
const allowed = new Map()
const seen = new Set()
for (const entry of allowlist.entries) {
  if (!entry || typeof entry !== 'object') fail('allowlist entry must be an object')
  const file = normalize(String(entry.path || ''))
  const code = String(entry.code || '')
  const count = Number(entry.count)
  const reason = String(entry.reason || '').trim()
  const owner = String(entry.owner || '').trim()
  const expires = parseDate(entry.expires, file + ' ' + code)
  if (!file || file.startsWith('..') || path.isAbsolute(file))
    fail('allowlist path must be repo-relative: ' + String(entry.path))
  if (!/^[A-Z][0-9]{3}$/.test(code)) fail(file + ': invalid ruff code ' + code)
  if (!Number.isInteger(count) || count < 1)
    fail(file + ' ' + code + ': count must be a positive integer')
  if (!reason) fail(file + ' ' + code + ': reason is required')
  if (!owner) fail(file + ' ' + code + ': owner is required')
  if (expires < today) fail(file + ' ' + code + ': allowlist entry expired on ' + entry.expires)
  const itemKey = key(file, code)
  if (seen.has(itemKey)) fail(file + ' ' + code + ': duplicate allowlist entry')
  seen.add(itemKey)
  allowed.set(itemKey, { file, code, count, reason, owner, expires: entry.expires })
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
    '--output-format=json',
    '--no-cache',
  ],
  'ruff',
)
if (!ruff.stdout.trim())
  fail('ruff produced no JSON; is ruff installed for ' + PYTHON + '? ' + String(ruff.stderr || ''))
let violations
try {
  violations = JSON.parse(ruff.stdout || '[]')
} catch (error) {
  fail(
    'ruff did not return JSON: ' +
      String(error && error.message ? error.message : error) +
      '\n' +
      (ruff.stderr || ''),
  )
}
if (!Array.isArray(violations)) fail('ruff JSON must be an array')

const current = new Map()
for (const violation of violations) {
  const file = normalize(String(violation.filename || ''))
  const code = String(violation.code || '')
  if (!file || !code) continue
  const itemKey = key(file, code)
  const item = current.get(itemKey) || { file, code, count: 0, samples: [] }
  item.count += 1
  if (item.samples.length < 3) {
    const loc = violation.location || {}
    item.samples.push(
      file +
        ':' +
        String(loc.row || '?') +
        ':' +
        String(loc.column || '?') +
        ' ' +
        String(violation.message || '').trim(),
    )
  }
  current.set(itemKey, item)
}

const problems = []
for (const [itemKey, item] of current.entries()) {
  const allowedItem = allowed.get(itemKey)
  if (!allowedItem) {
    problems.push(item.file + ' ' + item.code + ': new violation x' + item.count)
    for (const sample of item.samples) problems.push('  ' + sample)
    continue
  }
  if (item.count > allowedItem.count) {
    problems.push(
      item.file + ' ' + item.code + ': ' + item.count + ' > allowlist ' + allowedItem.count,
    )
    for (const sample of item.samples) problems.push('  ' + sample)
  }
}
for (const [itemKey, allowedItem] of allowed.entries()) {
  if (!current.has(itemKey)) {
    console.warn(
      'check-python-static: baseline shrank; update allowlist for ' +
        allowedItem.file +
        ' ' +
        allowedItem.code,
    )
  } else if (current.get(itemKey).count < allowedItem.count) {
    console.warn(
      'check-python-static: baseline shrank; lower allowlist for ' +
        allowedItem.file +
        ' ' +
        allowedItem.code +
        ' to ' +
        current.get(itemKey).count,
    )
  }
}

if (problems.length > 0) {
  console.error('check-python-static: ruff baseline check failed')
  for (const problem of problems) console.error(problem)
  process.exit(1)
}
console.log(
  'check-python-static: ruff clean (' +
    current.size +
    ' baseline groups, ' +
    violations.length +
    ' allowlisted findings)',
)

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
