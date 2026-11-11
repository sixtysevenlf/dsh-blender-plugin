import { describe, it, expect } from 'vitest'
import { spawnSync } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'
import { PKG_ROOT } from '../../../helpers/pkg'

const SCRIPT = path.join(PKG_ROOT, 'scripts', 'check-python-static.mjs')
const PYRIGHT_CONFIGS = ['pyrightconfig.python.json', 'pyrightconfig.extension.json']

function runScope(extraEnv: Record<string, string> = {}) {
  return spawnSync(process.execPath, [SCRIPT, '--scope-only'], {
    cwd: PKG_ROOT,
    encoding: 'utf8',
    env: { ...process.env, ...extraEnv },
  })
}

describe('python static scope admission', () => {
  it('accepts every tracked Python file under the declared roots', () => {
    const result = runScope()
    expect(result.status).toBe(0)
    expect(String(result.stderr)).not.toContain('outside the Python static roots')
  })

  it('rejects a tracked Python file outside the declared roots', () => {
    const result = runScope({ CHECK_PYTHON_TRACKED_FILES: 'docs/uncovered.py\n' })
    expect(result.status).not.toBe(0)
    expect(String(result.stderr)).toContain('docs/uncovered.py')
    expect(String(result.stderr)).toContain('outside the Python static roots')
  })

  it('keeps every pyright include present and unique', () => {
    for (const config of PYRIGHT_CONFIGS) {
      const parsed = JSON.parse(fs.readFileSync(path.join(PKG_ROOT, config), 'utf8')) as {
        include?: string[]
      }
      expect(Array.isArray(parsed.include)).toBe(true)
      const include = parsed.include as string[]
      expect(new Set(include).size).toBe(include.length)
      for (const entry of include) {
        const absolute = path.join(PKG_ROOT, entry)
        expect(fs.existsSync(absolute), entry + ' from ' + config).toBe(true)
        expect(fs.statSync(absolute).isFile(), entry + ' from ' + config).toBe(true)
      }
    }
  })
})
