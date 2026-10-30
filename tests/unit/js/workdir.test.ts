import { describe, expect, it } from 'vitest'
import { spawnSync } from 'node:child_process'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { describeConfig, wslPathShared } from '../../../runtime/config.mjs'
import { pkgPath } from '../../helpers/pkg'

// v0.9.6 (D3): the isolated workDir used to be os.tmpdir() (WSL /tmp is its own
// tmpfs). Node can write there but Windows blender.exe cannot read it, so the
// receipt came back empty and looked like a broken lease. The workDir must be
// judged (and substituted loudly) by mount-table visibility.

type Staged = { staging?: any, win?: string, wsl?: string, exists?: boolean, error?: string }

function stageWith(workdirPath: string): Staged {
  const code = [
    "import { writeHeadlessScript } from './runtime/engine.mjs';",
    "import fs from 'node:fs';",
    "const r = writeHeadlessScript('print(\"probe\")');",
    'console.log(JSON.stringify({ staging: r.staging, win: r.win, wsl: r.wsl, exists: fs.existsSync(r.wsl) }));',
  ].join(String.fromCharCode(10))
  const r = spawnSync(process.execPath, ['--input-type=module', '-e', code], {
    cwd: pkgPath(), encoding: 'utf8',
    env: Object.assign({}, process.env, { DSH_BLENDER_WORKDIR: workdirPath }),
  })
  const line = String(r.stdout || '').trim().split(String.fromCharCode(10)).filter(Boolean).pop() || ''
  try { return JSON.parse(line) } catch (error: any) {
    return { error: String(error && error.message), wsl: String(r.stdout).slice(-300) }
  }
}

function tmpDir(name: string): string {
  const dir = path.join(os.tmpdir(), 'dsh-workdir-probe-' + name + '-' + String(process.pid))
  fs.mkdirSync(dir, { recursive: true })
  return dir
}

describe('workDir Windows visibility', () => {
  it('judges absolute paths on the current platform and refuses to guess relative ones', () => {
    if (process.platform === 'win32') {
      expect(wslPathShared('/tmp/dsh-x').shared).toBe(true)
      expect(wslPathShared(path.join(os.homedir(), 'dsh-x')).shared).toBe(true)
      expect(wslPathShared('/dev/shm/dsh-x').shared).toBe(true)
    }
    const rel = wslPathShared('relative/x')
    // win32 reports every path as Windows-visible; elsewhere a relative path is unknowable.
    if (process.platform === 'win32') expect(rel.shared).toBe(true)
    else expect(rel.shared).toBeNull()
    expect(typeof rel.why).toBe('string')
    expect(rel.why.length).toBeGreaterThan(0)
  })

  it('exposes shared on describeConfig().workDir', () => {
    const cfg = describeConfig({})
    expect(Object.prototype.hasOwnProperty.call(cfg.workDir || {}, 'shared')).toBe(true)
  })

  it('substitutes a non-shared workDir loudly (or proves the shared one untouched)', () => {
    const probe = process.platform !== 'win32' && wslPathShared('/dev/shm/dsh-x').shared === false
      ? '/dev/shm/dsh-workdir-probe'
      : path.join(os.tmpdir(), 'dsh-workdir-probe-' + String(process.pid))
    const run = stageWith(probe)
    expect(run.error).toBeUndefined()
    expect(run.exists).toBe(true)
    const st = run.staging || {}
    if (st.requestedShared === true) {
      expect(st).toMatchObject({ substituted: false, usedShared: true })
      expect(st.used).toBe(st.requested)
    } else {
      expect(st).toMatchObject({ substituted: true, usedShared: true })
      expect(st.used).not.toBe(st.requested)
      expect(String(st.note)).toContain(st.used)
      expect(String(st.note)).toContain('不保证可见')
    }
    expect(typeof st.requestedShared).toBe('boolean')
    expect(typeof st.usedShared).toBe('boolean')
    expect(st.usedWin).toBe(run.win)
  })

  it('never substitutes an already-shared workDir', () => {
    const shared = tmpDir('shared')
    try {
      const run = stageWith(shared)
      expect(run.error).toBeUndefined()
      const st = run.staging || {}
      expect(st.substituted).toBe(false)
      expect(st.used).toBe(shared)
      expect(String(st.note)).toContain('通过')
    } finally {
      try { fs.rmSync(shared, { recursive: true, force: true }) } catch { /* ignore */ }
    }
  })
})
