#!/usr/bin/env node
/**
 * scripts/setup-dev.mjs — one command for the development environment.
 *
 * Usage:
 *   node scripts/setup-dev.mjs            create/reuse .venv and install Python + npm deps
 *   node scripts/setup-dev.mjs --check    verify only: write nothing, non-zero exit on problems
 *   node scripts/setup-dev.mjs --force    recreate .venv and reinstall npm dependencies
 *   node scripts/setup-dev.mjs --help
 *
 * The same implementation runs locally and in CI: .github/workflows/pr.yml and
 * .github/workflows/release.yml call this script instead of inline virtualenv
 * steps, so the two environments cannot drift.
 *
 * Python discovery: PYTHON env -> python -> python3 -> (Windows) py -3, then
 * versioned fallbacks (py -3.13, python3.13, ... the Windows Store aliases) and
 * finally the base interpreter recorded in an existing .venv/pyvenv.cfg.
 * Python 3.11+ is required (pyproject target version, Blender 4.2 runtime).
 *
 * Idempotent: an existing .venv is reused; pip is skipped when the requirements
 * hash marker matches and the test tools import cleanly. node_modules is only
 * installed with `npm ci --legacy-peer-deps` when it is missing or incomplete
 * (or --force is given); an existing tree is never silently replaced.
 *
 * Exit codes: 0 = ready (or --check passed); 1 = environment problem; 2 = bad usage.
 */
import { spawnSync } from 'node:child_process'
import crypto from 'node:crypto'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { planEditorConfig } from './editor-config.mjs'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const ROOT = path.resolve(HERE, '..')
const IS_WIN = process.platform === 'win32'
const LOG = 'setup-dev'
const NODE_MIN = [24, 9, 0]
const PYTHON_MIN = [3, 11, 0]
const VENV_DIR = path.join(ROOT, '.venv')
const VENV_PYTHON = path.join(VENV_DIR, IS_WIN ? 'Scripts/python.exe' : 'bin/python')
const VENV_PYTHON_ALIASES = [
  VENV_PYTHON,
  path.join(VENV_DIR, 'bin/python'),
  path.join(VENV_DIR, 'bin/python3'),
  path.join(VENV_DIR, 'Scripts/python.exe'),
  path.join(VENV_DIR, 'Scripts/python3.exe'),
]
const NODE_MODULES = path.join(ROOT, 'node_modules')
const EDITOR_DIR = path.join(ROOT, '.vscode')
const EDITOR_SETTINGS = path.join(EDITOR_DIR, 'settings.json')
const VITEST_ENTRY = path.join(NODE_MODULES, 'vitest', 'vitest.mjs')
const PYRIGHT_ENTRY = path.join(NODE_MODULES, 'pyright', 'index.js')
const REQUIREMENT_FILES = ['requirements-dev.txt', 'requirements-typecheck.txt'].map(
  function (name) {
    return path.join(ROOT, name)
  },
)
const STATE_FILE = path.join(VENV_DIR, '.setup-dev-state.json')
const STATE_SCHEMA = 1
const KNOWN_ARGS = new Set(['--check', '--force', '--help', '-h'])
const argv = process.argv.slice(2)
const CHECK_ONLY = argv.includes('--check')
const FORCE = argv.includes('--force')
const HELP = argv.includes('--help') || argv.includes('-h')
const UNKNOWN_ARGS = argv.filter(function (arg) {
  return !KNOWN_ARGS.has(arg)
})

// ── Output and process helpers ─────────────────────────────────────────────

function info(message) {
  console.log(LOG + ': ' + message)
}

function detail(message) {
  console.log('  ' + message)
}

function die(message, hints) {
  console.error(LOG + ': ' + message)
  for (const hint of hints || []) console.error('  ' + hint)
  process.exit(1)
}

function runVisible(command, args, options) {
  return spawnSync(command, args, {
    cwd: ROOT,
    env: process.env,
    stdio: 'inherit',
    shell: Boolean(options && options.shell),
  })
}

function runCapture(command, args) {
  return spawnSync(command, args, {
    cwd: ROOT,
    encoding: 'utf8',
    env: process.env,
    timeout: 30000,
  })
}

function describeSpawnFailure(result) {
  if (result.error) return String(result.error.message || result.error)
  if (result.status === null) return 'terminated by signal ' + result.signal
  return 'exit code ' + result.status
}

function lastOutputLine(text) {
  const lines = String(text || '')
    .trim()
    .split(/\r?\n/)
  return lines.length ? lines[lines.length - 1].trim() : ''
}

// ── Versions and paths ─────────────────────────────────────────────────────

function parseVersion(text) {
  const match = /(\d+)\.(\d+)\.(\d+)/.exec(String(text || ''))
  if (!match) return null
  return [Number(match[1]), Number(match[2]), Number(match[3])]
}

function versionAtLeast(found, minimum) {
  for (let index = 0; index < minimum.length; index += 1) {
    if (found[index] > minimum[index]) return true
    if (found[index] < minimum[index]) return false
  }
  return true
}

function formatVersion(parts) {
  return parts.join('.')
}

function relPath(target) {
  return path.relative(ROOT, target) || '.'
}

function venvPythonLabel() {
  return relPath(VENV_PYTHON)
}

function isManagedVenvPython(value) {
  if (!value) return false
  const resolved = path.resolve(ROOT, value)
  return VENV_PYTHON_ALIASES.some(function (candidate) {
    return path.resolve(candidate) === resolved
  })
}

// ── Node.js and npm dependencies ───────────────────────────────────────────

function checkNodeVersion() {
  const found = parseVersion(process.versions.node)
  if (!found || !versionAtLeast(found, NODE_MIN)) {
    die(
      'Node.js ' +
        process.versions.node +
        ' is too old: >= ' +
        formatVersion(NODE_MIN) +
        ' is required.',
      [
        'Install Node.js 24.9 or newer (https://nodejs.org/en/download) and re-run: npm run setup:dev',
      ],
    )
  }
  info('Node.js ' + process.versions.node + ' (>= ' + formatVersion(NODE_MIN) + ') OK')
}

function nodeModulesState() {
  const present = fs.existsSync(NODE_MODULES) && fs.statSync(NODE_MODULES).isDirectory()
  return {
    present: present,
    recorded: present && fs.existsSync(path.join(NODE_MODULES, '.package-lock.json')),
    complete: present && fs.existsSync(VITEST_ENTRY) && fs.existsSync(PYRIGHT_ENTRY),
  }
}

function installRecordIsOlderThanLockfile() {
  try {
    const installed = fs.statSync(path.join(NODE_MODULES, '.package-lock.json')).mtimeMs
    const lockfile = fs.statSync(path.join(ROOT, 'package-lock.json')).mtimeMs
    return lockfile > installed
  } catch {
    return false
  }
}

function installNodeModules() {
  const cli = process.env.npm_execpath
  const useNodeCli = Boolean(cli) && fs.existsSync(cli)
  const command = useNodeCli ? process.execPath : IS_WIN ? 'npm.cmd' : 'npm'
  const args = (useNodeCli ? [cli] : []).concat(['ci', '--legacy-peer-deps'])
  info('running npm ci --legacy-peer-deps')
  const result = runVisible(command, args, { shell: !useNodeCli && IS_WIN })
  if (result.error || result.status !== 0) {
    die('npm ci --legacy-peer-deps failed (' + describeSpawnFailure(result) + ').', [
      'Check network/proxy access to the npm registry, then re-run: npm run setup:dev',
      'If node_modules is half-written, delete it and re-run npm ci --legacy-peer-deps.',
    ])
  }
}

function ensureNodeModules() {
  const state = nodeModulesState()
  const staleNote =
    'note: package-lock.json is newer than the recorded node_modules install; re-run with --force if dependencies changed'
  if (CHECK_ONLY) {
    if (!state.present) {
      die('node_modules is missing.', ['Run npm run setup:dev to install the npm dependencies.'])
    }
    if (!state.complete) {
      die('node_modules is incomplete: vitest and/or pyright is missing.', [
        'Run npm run setup:dev -- --force (or npm ci --legacy-peer-deps).',
      ])
    }
    info('npm dependencies OK (vitest and pyright present)')
    if (installRecordIsOlderThanLockfile()) info(staleNote)
    return
  }
  if (FORCE || !state.present || !state.recorded || !state.complete) {
    const reason = FORCE
      ? '--force'
      : !state.present
        ? 'node_modules is missing'
        : !state.recorded
          ? 'node_modules has no npm install record'
          : 'node_modules is incomplete'
    info(reason + '; installing npm dependencies')
    installNodeModules()
    if (!nodeModulesState().complete) {
      die('npm ci finished but vitest/pyright is still missing.', [
        'Delete node_modules and re-run npm ci --legacy-peer-deps.',
      ])
    }
    info('npm dependencies installed')
    return
  }
  info('npm dependencies present; skipping npm ci (use --force to reinstall)')
  if (installRecordIsOlderThanLockfile()) info(staleNote)
}

function ensureEditorConfig() {
  let exists
  let settingsText = ''
  try {
    exists = fs.existsSync(EDITOR_SETTINGS)
    if (exists) settingsText = fs.readFileSync(EDITOR_SETTINGS, 'utf8')
  } catch (error) {
    detail(
      'editor settings: cannot read ' +
        relPath(EDITOR_SETTINGS) +
        ' (' +
        String(error.message || error) +
        '); skipping',
    )
    return
  }
  const plan = planEditorConfig({
    settingsPath: relPath(EDITOR_SETTINGS),
    settingsText: settingsText,
    exists: exists,
    checkOnly: CHECK_ONLY,
    ci: Boolean(process.env.CI),
  })
  if (plan.action === 'create' || plan.action === 'merge') {
    try {
      fs.mkdirSync(EDITOR_DIR, { recursive: true })
      fs.writeFileSync(EDITOR_SETTINGS, plan.content)
      info(plan.message)
    } catch (error) {
      detail(
        'editor settings: could not write ' +
          relPath(EDITOR_SETTINGS) +
          ' (' +
          String(error.message || error) +
          '); skipping (warning only)',
      )
    }
    return
  }
  info(plan.message)
}

// ── Python discovery ───────────────────────────────────────────────────────

function venvBaseInterpreter() {
  try {
    const config = fs.readFileSync(path.join(VENV_DIR, 'pyvenv.cfg'), 'utf8')
    const values = new Map()
    for (const line of config.split(/\r?\n/)) {
      const match = /^([A-Za-z][A-Za-z0-9_]*)\s*=\s*(.*)$/.exec(line.trim())
      if (match) values.set(match[1].toLowerCase(), match[2].trim())
    }
    const executable = values.get('executable')
    if (executable && fs.existsSync(executable)) return executable
    const home = values.get('home')
    if (home) {
      const names = IS_WIN ? ['python.exe', 'python3.exe'] : ['python3', 'python']
      for (const name of names) {
        const candidate = path.join(home, name)
        if (fs.existsSync(candidate)) return candidate
      }
    }
  } catch {
    return null
  }
  return null
}

function pythonCandidates() {
  const candidates = []
  const explicit = process.env.PYTHON
  if (explicit && !isManagedVenvPython(explicit)) {
    candidates.push({ label: 'PYTHON env', command: explicit, args: [], explicit: true })
  }
  candidates.push({ label: 'python', command: 'python', args: [], explicit: false })
  candidates.push({ label: 'python3', command: 'python3', args: [], explicit: false })
  if (IS_WIN) {
    candidates.push({ label: 'py -3', command: 'py', args: ['-3'], explicit: false })
    for (const minor of [13, 12, 11]) {
      candidates.push({
        label: 'py -3.' + minor,
        command: 'py',
        args: ['-3.' + minor],
        explicit: false,
      })
      candidates.push({
        label: 'python3.' + minor,
        command: 'python3.' + minor,
        args: [],
        explicit: false,
      })
    }
  }
  const base = venvBaseInterpreter()
  if (base)
    candidates.push({ label: 'existing .venv base', command: base, args: [], explicit: false })
  return candidates
}

function probePython(candidate) {
  const args = candidate.args.concat(['-B', '-c', 'import sys; print(sys.version.split()[0])'])
  const result = runCapture(candidate.command, args)
  if (result.error || result.status !== 0)
    return { ok: false, reason: describeSpawnFailure(result) }
  const versionText = lastOutputLine(result.stdout)
  const version = parseVersion(versionText)
  if (!version) return { ok: false, reason: 'unexpected output: ' + JSON.stringify(versionText) }
  return { ok: true, version: version, versionText: versionText }
}

function noPythonHints() {
  return [
    'Install Python ' +
      formatVersion(PYTHON_MIN) +
      ' or newer (https://www.python.org/downloads/) and make python or python3 available on PATH,',
    'or point PYTHON at an existing interpreter: PYTHON=/path/to/python npm run setup:dev',
  ]
}

function discoverPython() {
  const attempts = []
  for (const candidate of pythonCandidates()) {
    const probe = probePython(candidate)
    if (!probe.ok) {
      attempts.push(candidate.label + ' (' + probe.reason + ')')
      if (candidate.explicit) {
        die(
          'PYTHON is set to "' +
            candidate.command +
            '" but it is not usable (' +
            probe.reason +
            ').',
          ['Fix PYTHON, or unset it to let the script discover an interpreter.'],
        )
      }
      continue
    }
    if (!versionAtLeast(probe.version, PYTHON_MIN)) {
      const tooOld =
        candidate.label +
        ' is Python ' +
        probe.versionText +
        ', but >= ' +
        formatVersion(PYTHON_MIN) +
        ' is required'
      attempts.push(tooOld)
      if (candidate.explicit) {
        die(tooOld + '.', [
          'Point PYTHON at a Python ' +
            formatVersion(PYTHON_MIN) +
            '+ interpreter, or unset PYTHON to let the script discover one.',
        ])
      }
      continue
    }
    return {
      interpreter: {
        label: candidate.label,
        command: candidate.command,
        args: candidate.args,
        version: probe.version,
        versionText: probe.versionText,
      },
      attempts: attempts,
    }
  }
  return { interpreter: null, attempts: attempts }
}

// ── Virtualenv ─────────────────────────────────────────────────────────────

function probeVenv() {
  if (!fs.existsSync(VENV_PYTHON)) return null
  const result = runCapture(VENV_PYTHON, ['-B', '-c', 'import sys; print(sys.version.split()[0])'])
  if (result.error || result.status !== 0) return null
  const versionText = lastOutputLine(result.stdout)
  const version = parseVersion(versionText)
  return version ? { version: version, versionText: versionText } : null
}

function ensureVenv(basePython, discovery) {
  if (FORCE && fs.existsSync(VENV_DIR)) {
    info('--force: removing ' + relPath(VENV_DIR))
    fs.rmSync(VENV_DIR, { recursive: true, force: true })
  }
  const existing = probeVenv()
  if (existing) {
    if (!versionAtLeast(existing.version, PYTHON_MIN)) {
      die(
        'the existing ' +
          relPath(VENV_DIR) +
          ' runs Python ' +
          existing.versionText +
          ', but >= ' +
          formatVersion(PYTHON_MIN) +
          ' is required.',
        [
          'Recreate it: npm run setup:dev -- --force (or delete ' +
            relPath(VENV_DIR) +
            ' and re-run npm run setup:dev).',
        ],
      )
    }
    info(
      'reusing ' +
        relPath(VENV_DIR) +
        ' (' +
        venvPythonLabel() +
        ', Python ' +
        existing.versionText +
        ')',
    )
    return existing
  }
  if (CHECK_ONLY) {
    if (!fs.existsSync(VENV_DIR)) {
      die('missing ' + relPath(VENV_DIR) + '.', ['Run npm run setup:dev once to create it.'])
    }
    die(relPath(VENV_DIR) + ' exists but its interpreter is missing or not runnable.', [
      'Recreate it: npm run setup:dev -- --force',
    ])
  }
  if (!basePython) {
    const hints = noPythonHints()
    for (const attempt of discovery.attempts) hints.push('tried: ' + attempt)
    die(
      'cannot create ' +
        relPath(VENV_DIR) +
        ': no usable Python ' +
        formatVersion(PYTHON_MIN) +
        '+ interpreter was found.',
      hints,
    )
  }
  if (fs.existsSync(VENV_DIR)) {
    info(relPath(VENV_DIR) + ' is incomplete; recreating it')
    fs.rmSync(VENV_DIR, { recursive: true, force: true })
  }
  info(
    'creating ' +
      relPath(VENV_DIR) +
      ' with Python ' +
      basePython.versionText +
      ' (' +
      basePython.label +
      ')',
  )
  const created = runVisible(basePython.command, basePython.args.concat(['-m', 'venv', VENV_DIR]))
  if (created.error || created.status !== 0) {
    die(
      'python -m venv ' + relPath(VENV_DIR) + ' failed (' + describeSpawnFailure(created) + ').',
      [
        'Check that the interpreter ships the venv/ensurepip module (Debian/Ubuntu: apt install python3-venv).',
        'Then re-run: npm run setup:dev',
      ],
    )
  }
  const venv = probeVenv()
  if (!venv) {
    die('created ' + relPath(VENV_DIR) + ' but ' + venvPythonLabel() + ' cannot run.', [
      'Delete ' +
        relPath(VENV_DIR) +
        ' and re-run npm run setup:dev; if it repeats, check the Python installation.',
    ])
  }
  info('created ' + relPath(VENV_DIR) + ' (Python ' + venv.versionText + ')')
  return venv
}

// ── Python requirements ────────────────────────────────────────────────────

function requirementsHash(pythonVersionText) {
  for (const file of REQUIREMENT_FILES) {
    if (!fs.existsSync(file)) {
      die('missing ' + relPath(file) + '.', ['Restore it from git and re-run: npm run setup:dev'])
    }
  }
  const hash = crypto.createHash('sha256')
  hash.update('setup-dev requirements schema ' + STATE_SCHEMA + '\n')
  for (const file of REQUIREMENT_FILES) {
    hash.update(path.basename(file) + '\n')
    hash.update(fs.readFileSync(file, 'utf8').replace(/\r\n/g, '\n'))
    hash.update('\n')
  }
  hash.update('python ' + pythonVersionText + '\n')
  return hash.digest('hex')
}

function readState() {
  try {
    const state = JSON.parse(fs.readFileSync(STATE_FILE, 'utf8'))
    return state && typeof state === 'object' ? state : null
  } catch {
    return null
  }
}

function writeState(hash, pythonVersionText) {
  const state = {
    schema: STATE_SCHEMA,
    requirementsSha256: hash,
    python: pythonVersionText,
    createdAt: new Date().toISOString(),
  }
  fs.writeFileSync(STATE_FILE, JSON.stringify(state, null, 2) + '\n')
}

const HEALTH_PROBES = [
  { label: 'pytest', args: ['-B', '-m', 'pytest', '-p', 'no:cacheprovider', '--version'] },
  { label: 'ruff', args: ['-B', '-m', 'ruff', '--version'] },
  { label: 'diff-cover', args: ['-B', '-c', 'import diff_cover'] },
  { label: 'numpy', args: ['-B', '-c', 'import numpy'] },
  {
    label: 'fake-bpy stubs',
    args: [
      '-B',
      '-c',
      'import os, sysconfig, sys; sys.exit(0 if os.path.isdir(os.path.join(sysconfig.get_paths()["purelib"], "bpy-stubs")) else 1)',
    ],
  },
]

function probePythonDependencies() {
  const failures = []
  for (const probe of HEALTH_PROBES) {
    const result = runCapture(VENV_PYTHON, probe.args)
    if (result.error || result.status !== 0) {
      failures.push(probe.label + ' (' + describeSpawnFailure(result) + ')')
    }
  }
  return failures
}

function ensurePythonDependencies(venv) {
  const hash = requirementsHash(venv.versionText)
  const recordedState = readState()
  const recorded =
    Boolean(recordedState) &&
    recordedState.schema === STATE_SCHEMA &&
    recordedState.requirementsSha256 === hash
  const failures = probePythonDependencies()
  if (CHECK_ONLY) {
    if (failures.length) {
      die('the Python environment is missing or broken packages: ' + failures.join(', ') + '.', [
        'Run npm run setup:dev to install requirements-dev.txt and requirements-typecheck.txt.',
      ])
    }
    info('Python dependencies OK (pytest, ruff, diff-cover, numpy, fake-bpy stubs)')
    if (!recorded) detail('no matching install marker; the next npm run setup:dev will record one')
    return
  }
  if (!FORCE && recorded && failures.length === 0) {
    info('Python dependencies already satisfied (marker ' + hash.slice(0, 12) + ')')
    return
  }
  if (recorded && failures.length) {
    info('Python dependencies recorded but unhealthy: ' + failures.join(', ') + '; reinstalling')
  }
  info(
    'installing Python requirements (' +
      REQUIREMENT_FILES.map(function (file) {
        return path.basename(file)
      }).join(' + ') +
      ')',
  )
  const pipArgs = ['-m', 'pip', 'install', '--disable-pip-version-check', '--no-input']
  for (const file of REQUIREMENT_FILES) pipArgs.push('-r', file)
  const result = runVisible(VENV_PYTHON, pipArgs)
  if (result.error || result.status !== 0) {
    die('pip install failed (' + describeSpawnFailure(result) + ').', [
      'Check network/proxy access to PyPI, then re-run: npm run setup:dev',
      'Manual fallback: ' +
        venvPythonLabel() +
        ' -m pip install -r requirements-dev.txt -r requirements-typecheck.txt',
    ])
  }
  const after = probePythonDependencies()
  if (after.length) {
    die('requirements installed but the environment still fails: ' + after.join(', ') + '.', [
      'Recreate it: npm run setup:dev -- --force',
    ])
  }
  writeState(hash, venv.versionText)
  info('Python dependencies installed and verified')
}

// ── Entry point ────────────────────────────────────────────────────────────

function printUsage() {
  console.log('usage: node scripts/setup-dev.mjs [--check | --force | --help]')
  console.log('')
  console.log('  (no flag)  create/reuse .venv, install Python requirements and npm dependencies')
  console.log('  --check    verify the environment without writing; exits non-zero on any problem')
  console.log('  --force    recreate .venv and reinstall npm dependencies')
  console.log('  --help     show this help')
}

function printReady(venv) {
  info(
    (CHECK_ONLY ? 'check passed' : 'environment ready') +
      ' — ' +
      relPath(VENV_DIR) +
      ' (Python ' +
      venv.versionText +
      ')',
  )
  detail('PYTHON=' + venvPythonLabel() + ' (optional; scripts/test.mjs auto-detects .venv)')
  if (IS_WIN) {
    detail('cmd:        set PYTHON=' + venvPythonLabel())
    detail('PowerShell: $env:PYTHON = "' + venvPythonLabel() + '"')
  } else {
    detail('export PYTHON="$PWD/' + relPath(VENV_PYTHON) + '"')
  }
  detail('next: npm run typecheck && npm run test:unit && npm run test:python')
}

function main() {
  if (HELP) {
    printUsage()
    return
  }
  if (UNKNOWN_ARGS.length) {
    console.error(LOG + ': unknown argument(s): ' + UNKNOWN_ARGS.join(' '))
    printUsage()
    process.exit(2)
  }
  if (CHECK_ONLY && FORCE) {
    console.error(LOG + ': --check verifies without writing and cannot be combined with --force')
    process.exit(2)
  }
  console.log(LOG + ': DSH x Blender development environment' + (CHECK_ONLY ? ' (check only)' : ''))
  checkNodeVersion()
  const existingVenv = probeVenv()
  if (process.env.PYTHON && isManagedVenvPython(process.env.PYTHON)) {
    detail(
      'note: PYTHON points at the managed ' +
        venvPythonLabel() +
        '; looking for a base interpreter instead',
    )
  }
  const discovery = discoverPython()
  const basePython = discovery.interpreter
  if (basePython) {
    info('Python ' + basePython.versionText + ' via ' + basePython.label)
  } else if (CHECK_ONLY || !existingVenv) {
    const hints = noPythonHints()
    for (const attempt of discovery.attempts) hints.push('tried: ' + attempt)
    die('no usable Python ' + formatVersion(PYTHON_MIN) + '+ interpreter found.', hints)
  } else {
    info('no base Python found; reusing the existing ' + relPath(VENV_DIR) + ' as-is')
  }
  const venv = ensureVenv(basePython, discovery)
  ensurePythonDependencies(venv)
  ensureNodeModules()
  ensureEditorConfig()
  printReady(venv)
}

main()
