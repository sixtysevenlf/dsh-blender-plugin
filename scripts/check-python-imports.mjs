#!/usr/bin/env node
/**
 * Python import boundaries.
 *
 * Runs import-linter against the contracts declared in pyproject.toml. The
 * contracts are the single authority for Python import boundaries: runtime/py
 * domains stay independent (with admission via the exhaustive layers check),
 * and the two addon edges remain forbidden. There are no per-file waivers.
 *
 * The interpreter is resolved the same way as check-python-static.mjs:
 * PYTHON -> .venv -> python/python3. lint-imports is taken from the
 * interpreter's own directory first (venv Scripts/bin, or the CI setup-python
 * bin), then from PATH. PYTHONPATH includes both the repository root (for the
 * runtime.py namespace package) and extension/ (for dsh_blender_bridge).
 */
import { spawnSync } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const PKG = path.resolve(HERE, '..')
const IS_WIN = process.platform === 'win32'
const VENV_PYTHON = path.join(PKG, '.venv', IS_WIN ? 'Scripts/python.exe' : 'bin/python')
const PYTHON =
  process.env.PYTHON || (fs.existsSync(VENV_PYTHON) ? VENV_PYTHON : IS_WIN ? 'python' : 'python3')

function fail(message) {
  console.error('check-python-imports: ' + message)
  process.exit(1)
}

function findLintImports() {
  const names = IS_WIN ? ['lint-imports.exe', 'lint-imports.cmd', 'lint-imports'] : ['lint-imports']
  const interpreterDir = path.dirname(PYTHON)
  for (const name of names) {
    const candidate = path.resolve(PKG, interpreterDir, name)
    if (fs.existsSync(candidate) && fs.statSync(candidate).isFile()) return candidate
  }
  return 'lint-imports'
}

function pythonPath() {
  const entries = [PKG, path.join(PKG, 'extension')]
  const existing = process.env.PYTHONPATH
  if (existing) entries.push(existing)
  return entries.join(path.delimiter)
}

function main() {
  const lintImports = findLintImports()
  const env = { ...process.env, PYTHONPATH: pythonPath() }
  const result = spawnSync(lintImports, ['--no-cache'], {
    cwd: PKG,
    env,
    stdio: 'inherit',
    shell: IS_WIN && lintImports === 'lint-imports',
  })
  if (result.error) {
    fail(
      'could not run lint-imports (' +
        String(result.error.message || result.error) +
        '); install the Python development dependencies with: npm run setup:dev',
    )
  }
  if (result.status !== 0) {
    console.error('check-python-imports: import-linter reported broken contracts')
    process.exit(result.status === null ? 1 : result.status)
  }
}

main()
