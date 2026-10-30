import { describe, expect, it } from 'vitest'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { spawnSync } from 'node:child_process'
import {
  CFG, IS_MAC, IS_WSL, PATHS, describeConfig, detectWsl, winToWsl, wslToWin,
} from '../../../runtime/config.js'
import { PATH_GUARD, PATH_HELPERS, WIN_TMP, WSL_TMP, blenderJoin } from '../../../runtime/engine.js'
import { pkgPath } from '../../helpers/pkg'

// issue #11 / #32 / #33: the plug-in was written for "WSL + Windows Blender".
// Native Linux and macOS run Blender on the same OS, so every cross-OS rewrite
// must be the identity; a leaked UNC path fails silently inside Blender. The
// platform decision table is a pure function, so one machine covers all
// branches, while source guards keep the WSL/Windows branches from being lost.

const MANJARO = 'Linux version 6.18.49-1-MANJARO (gcc (GCC) 15.2.1) #1 SMP PREEMPT'
const WSL2 = 'Linux version 5.15.90.1-microsoft-standard-WSL2 (root@x) #1 SMP'

const read = (rel: string) => fs.readFileSync(pkgPath(rel), 'utf8')
const NL = String.fromCharCode(10)
const pythonAvailable = (): string | null => {
  for (const candidate of [process.env.DSH_TEST_PYTHON || '', 'python3', 'python']) {
    if (!candidate) continue
    const r = spawnSync(candidate, ['-V'], { encoding: 'utf8' })
    if (r.status === 0) return candidate
  }
  return null
}

describe('detectWsl decision table (platform independent)', () => {
  it('is a pure exported function', () => {
    expect(typeof detectWsl).toBe('function')
  })
  it.each([
    ['win32 always false', 'win32', { WSL_DISTRO_NAME: 'Ubuntu' }, WSL2, false],
    ['darwin always false', 'darwin', { WSL_DISTRO_NAME: 'Ubuntu' }, WSL2, false],
    ['native linux without WSL vars', 'linux', {}, MANJARO, false],
    ['WSL_INTEROP means interop is running', 'linux', { WSL_INTEROP: '/run/WSL/1_interop' }, MANJARO, true],
    ['WSL_DISTRO_NAME is enough', 'linux', { WSL_DISTRO_NAME: 'Ubuntu' }, MANJARO, true],
    ['systemd service with empty env falls back to /proc/version', 'linux', {}, WSL2, true],
    ['unreadable /proc/version never guesses WSL', 'linux', {}, '', false],
    ['old Microsoft kernel string counts', 'linux', {}, 'Linux 4.4.0-19041-Microsoft', true],
  ])('%s', (_label, platform, env, version, want) => {
    expect((detectWsl as any)(platform, env, version)).toBe(want)
  })
  it('does not throw on undefined input', () => {
    expect((detectWsl as any)('linux', undefined, undefined)).toBe(false)
  })
})

describe('current platform branch', () => {
  if (process.platform === 'win32') {
    it('keeps the Windows backslash behaviour and IS_WSL=false', () => {
      expect(IS_WSL).toBe(false)
      expect(wslToWin('D:/a/b')).toBe('D:\\a\\b')
    })
  } else if (process.platform === 'darwin') {
    it('is identity on macOS', () => {
      expect(IS_MAC).toBe(true)
      expect(wslToWin('/Users/x/f.png')).toBe('/Users/x/f.png')
      expect(winToWsl('/Users/x/f.png')).toBe('/Users/x/f.png')
    })
  } else if (IS_WSL) {
    it('keeps UNC and /mnt drive mapping on WSL', () => {
      expect(wslToWin('/home/x/f.png')).toBe('\\\\wsl.localhost\\' + String((CFG as any).distro) + '\\home\\x\\f.png')
      expect(wslToWin('/mnt/d/a/b.py')).toBe('D:\\a\\b.py')
    })
  } else {
    it('is identity on native Linux (issue #11)', () => {
      const abs = '/home/someone/.dsh-blender-rt'
      expect(wslToWin(abs)).toBe(abs)
      expect(wslToWin(abs).includes('wsl.localhost')).toBe(false)
      expect(wslToWin('rel/x.py')).toBe('rel/x.py')
      for (const value of Object.values(PATHS)) expect(String(value).includes('\\')).toBe(false)
    })
  }
})

describe('source guards (WSL/Windows branches still exist)', () => {
  it('keeps the UNC and /mnt mappings and the WSL detection fallback', () => {
    const config = read('runtime/config.ts')
    expect(config).toContain('wsl.localhost')
    expect(config).toContain('^\\/mnt\\/([a-z])\\/')
    expect(/if\s*\(!IS_WSL\)\s*return\s*s;/.test(config)).toBe(true)
    expect(config).toContain('WSL_INTEROP')
    expect(config).toContain('/proc/version')
    expect(config).toContain('Blender Foundation')
    expect(config).toContain('where.exe')
  })
  it('keeps the WSL path-helper rewrite', () => {
    const source = read('runtime/engine.ts') + NL + read('runtime/paths.ts') + NL + read('runtime/proc_env.ts')
    expect(source).toContain('WSLENV')
    expect(source).toContain('startswith("/mnt/")')
  })
})

describe('macOS branch (runs only on macOS)', () => {
  it.skipIf(!IS_MAC)('keeps workDir persistent and disables PATH_GUARD', () => {
    const wd = String((CFG as any).workDirWsl)
    expect(wd.startsWith(os.tmpdir())).toBe(false)
    expect((CFG as any).workDirWin).toBe(wd)
    expect(describeConfig({}).distro).toBeNull()
    expect(PATH_GUARD).toBe('')
    expect(PATH_HELPERS).toContain('if True: return str(p)')
    expect(WIN_TMP.startsWith('/')).toBe(true)
    expect(WSL_TMP.startsWith('/')).toBe(true)
    expect(blenderJoin(wd, 'frame.png').includes('\\')).toBe(false)
    for (const value of Object.values(PATHS)) {
      expect(String(value).includes('\\')).toBe(false)
      expect(String(value).startsWith('/')).toBe(true)
    }
  })

  // macOS identity only: on Windows the same helper intentionally emits UNC paths
  // (\\wsl.localhost\...) and native backslashes, so the macOS expectations below
  // only hold on a Mac. Mirrors the sibling skipIf above.
  it.skipIf(!IS_MAC)('compiles the Blender-side path helpers with Python (macOS identity)', () => {
    const python = pythonAvailable()
    if (!python) return
    const probe = path.join(os.tmpdir(), 'dsh_mac_paths_probe.py')
    fs.writeFileSync(probe, PATH_HELPERS, 'utf8')
    const driver = [
      'import sys, types, json',
      "bpy = types.ModuleType('bpy')",
      "bpy.data = types.SimpleNamespace(filepath='/Users/someone/projects/scene.blend')",
      "sys.modules['bpy'] = bpy",
      'class KMod: pass',
      'K = KMod()',
      "ns = {'bpy': bpy, 'K': K}",
      'exec(compile(open(' + JSON.stringify(probe) + ').read(), "ph.py", "exec"), ns)',
      "wp, wl, bd = ns['_dsh_win_path'], ns['_dsh_wsl_path'], ns['_dsh_blend_path']",
      "cases = ['/Users/someone/projects/out.png', '/abs/x.py', 'rel/script.py']",
      "print(json.dumps({'win': {c: wp(c) for c in cases}, 'wsl': {c: wl(c) for c in cases}, 'blend': bd('rel/script.py')}))",
    ].join(NL)
    const r = spawnSync(python, ['-c', driver], { encoding: 'utf8', timeout: 30000 })
    if (r.status !== 0) return
    const out = JSON.parse(String(r.stdout).trim().split(NL).pop() || '{}')
    expect(JSON.stringify(out).includes('wsl.localhost')).toBe(false)
    expect(out.blend).toBe('/Users/someone/projects/rel/script.py')
    if (IS_MAC) {
      expect(out.win['/Users/someone/projects/out.png']).toBe('/Users/someone/projects/out.png')
      expect(out.wsl['/Users/someone/projects/out.png']).toBe('/Users/someone/projects/out.png')
    }
  })
})
