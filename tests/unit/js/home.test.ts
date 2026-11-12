import { afterEach, describe, expect, it } from 'vitest'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import {
  HOME_DIR,
  HOME_LAYOUT_DIRS,
  ensureHomeLayout,
  getHome,
  getHomeResolution,
  migrateLegacyFile,
  resetHomeCache,
  resolveHome,
} from '../../../runtime/home.js'
import {
  HOST_HOME_LAYOUT_DIRS,
  ensureHostHomeLayout,
  resetDnnHomeCache,
} from '../../../src/host/dnn-home.js'

const tmpDirs: string[] = []

function tmp(name: string): string {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'dsh-home-' + name + '-'))
  tmpDirs.push(dir)
  return dir
}

afterEach(() => {
  resetHomeCache()
  resetDnnHomeCache()
  while (tmpDirs.length) {
    const dir = tmpDirs.pop()
    if (dir) fs.rmSync(dir, { recursive: true, force: true })
  }
})

describe('resolveHome', () => {
  it('lets an explicit DSH_BLENDER_HOME win', () => {
    const env = { DSH_BLENDER_HOME: path.join('C:', 'data', 'dsh-blender'), DSH_HOME: '/x' }
    const got = resolveHome(env, '/home/me')
    expect(got.source).toBe('env')
    expect(got.home).toBe(path.resolve(env.DSH_BLENDER_HOME))
    expect(got.requestedFrom).toContain('DSH_BLENDER_HOME=')
  })

  it('hangs the default off DSH_HOME when set', () => {
    const dshHome = path.join(os.tmpdir(), 'dsh-home-env')
    const got = resolveHome({ DSH_HOME: dshHome }, '/home/me')
    expect(got.source).toBe('dsh_home')
    expect(got.home).toBe(path.resolve(dshHome, HOME_DIR))
  })

  it('falls back to ~/.dsh/dsh-blender without env', () => {
    const got = resolveHome({}, '/home/me')
    expect(got.source).toBe('default')
    expect(got.home).toBe(path.join('/home/me', '.dsh', HOME_DIR))
  })

  it('ignores blank values instead of resolving the process cwd', () => {
    const got = resolveHome({ DSH_BLENDER_HOME: '   ', DSH_HOME: '' }, '/home/me')
    expect(got.source).toBe('default')
  })

  it('resolves once per process', () => {
    const first = tmp('once')
    process.env.DSH_BLENDER_HOME = first
    resetHomeCache()
    expect(getHome()).toBe(path.resolve(first))
    process.env.DSH_BLENDER_HOME = tmp('changed')
    expect(getHome()).toBe(path.resolve(first))
    expect(getHomeResolution().source).toBe('env')
    delete process.env.DSH_BLENDER_HOME
  })
})

describe('ensureHomeLayout', () => {
  it('creates every layout directory idempotently and keeps user files', () => {
    const home = path.join(tmp('layout'), 'nested', 'home')
    const first = ensureHomeLayout(home)
    expect(first.ok).toBe(true)
    if (!first.ok) return
    for (const name of HOME_LAYOUT_DIRS) {
      expect(fs.statSync(first.dirs[name]).isDirectory()).toBe(true)
    }
    const keep = path.join(home, 'state', 'keep.txt')
    fs.writeFileSync(keep, 'mine')

    const second = ensureHomeLayout(home)
    expect(second.ok).toBe(true)
    if (!second.ok) return
    expect(second.home).toBe(path.resolve(home))
    expect(fs.readFileSync(keep, 'utf8')).toBe('mine')
    // The write probe must not leave a file behind.
    expect(fs.readdirSync(home).filter((f) => f.includes('write-probe'))).toEqual([])
  })

  it('reports home_unwritable without falling back when the path is a file', () => {
    const base = tmp('unwritable')
    const blocked = path.join(base, 'blocked')
    fs.writeFileSync(blocked, 'not a directory')
    const got = ensureHomeLayout(blocked)
    expect(got.ok).toBe(false)
    if (got.ok) return
    expect(got.error).toMatchObject({ code: 'home_unwritable', path: path.resolve(blocked) })
    expect(String(got.error.reason).length).toBeGreaterThan(0)
    expect(String(got.error.fix)).toContain('DSH_BLENDER_HOME')
    // Nothing was written next to the failing path.
    expect(fs.readdirSync(base)).toEqual(['blocked'])
  })
})

describe('host home layout (plugin apply path)', () => {
  it('mirrors the runtime layout directory list', () => {
    expect([...HOST_HOME_LAYOUT_DIRS]).toEqual([...HOME_LAYOUT_DIRS])
  })

  it('creates the same directories and reports home_unwritable without throwing', () => {
    const home = path.join(tmp('host-layout'), 'home')
    const ok = ensureHostHomeLayout(home)
    expect(ok.ok).toBe(true)
    if (ok.ok) {
      for (const name of HOME_LAYOUT_DIRS) {
        expect(fs.statSync(ok.dirs[name]).isDirectory()).toBe(true)
      }
    }

    const base = tmp('host-blocked')
    const blocked = path.join(base, 'blocked')
    fs.writeFileSync(blocked, 'not a directory')
    const failed = ensureHostHomeLayout(blocked)
    expect(failed.ok).toBe(false)
    if (!failed.ok) {
      expect(failed.error).toMatchObject({ code: 'home_unwritable', path: path.resolve(blocked) })
      expect(String(failed.error.reason).length).toBeGreaterThan(0)
      expect(String(failed.error.fix)).toContain('DSH_BLENDER_HOME')
    }
  })
})

describe('migrateLegacyFile', () => {
  it('copies only when the target is missing and never deletes the source', () => {
    const base = tmp('migrate')
    const from = path.join(base, 'legacy', 'queue.json')
    const to = path.join(base, 'home', 'queue', 'queue.json')
    fs.mkdirSync(path.dirname(from), { recursive: true })
    fs.writeFileSync(from, '{"version":1,"jobs":[]}')

    expect(migrateLegacyFile(from, to)).toMatchObject({ result: 'copied' })
    expect(fs.existsSync(from)).toBe(true)
    expect(fs.readFileSync(to, 'utf8')).toBe('{"version":1,"jobs":[]}')

    fs.writeFileSync(to, 'newer')
    expect(migrateLegacyFile(from, to)).toMatchObject({ result: 'skipped' })
    expect(fs.readFileSync(to, 'utf8')).toBe('newer')

    expect(
      migrateLegacyFile(path.join(base, 'none.json'), path.join(base, 'x.json')),
    ).toMatchObject({
      result: 'missing',
    })
  })
})
