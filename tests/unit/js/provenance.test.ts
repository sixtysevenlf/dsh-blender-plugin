import { afterAll, beforeAll, describe, expect, it } from 'vitest'
import { createHash } from 'node:crypto'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { pathToFileURL } from 'node:url'
import { pkgPath } from '../../helpers/pkg'

// v0.9.6 (D3): the loaded-version self check must use a content fingerprint,
// never mtime — a touch used to fake "stale", a same-second rewrite used to
// fake "fresh". A clean process is the judge for the on-disk catalog.

const TMP = fs.mkdtempSync(path.join(os.tmpdir(), 'dsh-prov-stale-'))
const COPY_RT = path.join(TMP, 'runtime')
const ENGINE = path.join(COPY_RT, 'engine.mjs')
const CATALOG = path.join(COPY_RT, 'catalog.mjs')
let mod: any
let origEngine = ''
let origMd5 = ''

function copyRuntime(src: string, dest: string) {
  fs.mkdirSync(dest, { recursive: true })
  for (const entry of fs.readdirSync(src, { withFileTypes: true })) {
    if (entry.name === '__pycache__' || entry.name === 'ext') continue
    const from = path.join(src, entry.name)
    const to = path.join(dest, entry.name)
    if (entry.isDirectory()) copyRuntime(from, to)
    else if (entry.isFile()) fs.copyFileSync(from, to)
  }
}

const md5 = (p: string) => createHash('md5').update(fs.readFileSync(p)).digest('hex')

beforeAll(async () => {
  copyRuntime(pkgPath('runtime'), COPY_RT)
  fs.copyFileSync(pkgPath('package.json'), path.join(TMP, 'package.json'))
  mod = await import(pathToFileURL(ENGINE).href + '?probe=' + String(Date.now()))
  origEngine = fs.readFileSync(ENGINE, 'utf8')
  origMd5 = md5(ENGINE)
}, 60000)

afterAll(() => { try { fs.rmSync(TMP, { recursive: true, force: true }) } catch { /* ignore */ } })

describe('loaded version provenance', () => {
  it('reports current right after loading with a sha256:12 hash', () => {
    const pr = mod.engineProvenance({})
    expect(pr).toMatchObject({ verdict: 'current', stale: false, hashAlgo: 'sha256:12' })
    expect(pr.loadedHash).toBeTruthy()
    expect(pr.loadedHash).toBe(pr.currentHash)
    expect(/^[0-9a-f]{12}$/.test(pr.loadedHash)).toBe(true)
    expect(String(pr.note)).toContain('mtime')
    const text = mod.catalogPayload({}).text
    expect(text).toContain('目录自证')
    expect(text).not.toContain('目录自证 FAIL')
  })

  it('ignores an mtime-only change but reports the mtime as reference', () => {
    const pr0 = mod.engineProvenance({})
    const future = new Date(Date.now() + 3600 * 1000)
    fs.utimesSync(ENGINE, future, future)
    expect(md5(ENGINE)).toBe(origMd5)
    expect(fs.statSync(ENGINE).mtimeMs).toBeGreaterThan(Date.now())
    const pr1 = mod.engineProvenance({})
    expect(pr1).toMatchObject({ verdict: 'current', stale: false })
    expect(pr1.currentHash).toBe(pr0.currentHash)
    expect(pr1.currentMtime).toBeTruthy()
    expect(pr1.currentMtime).not.toBe(pr0.currentMtime)
  })

  it('flags a real content change as stale and names both hashes', () => {
    fs.writeFileSync(ENGINE, origEngine + String.fromCharCode(10) + '// provenance-stale-probe' + String.fromCharCode(10), 'utf8')
    const pr = mod.engineProvenance({ verify: true })
    expect(pr).toMatchObject({ verdict: 'stale', stale: true })
    expect(String(pr.staleReason)).toContain(pr.loadedHash)
    expect(String(pr.staleReason)).toContain(pr.currentHash)
    expect(pr.diskVerdict && pr.diskVerdict.ok).toBe(true)
    expect(pr.diskVerdict.matchesLoadedCatalog).toBe(true)
  })

  it('names a stale on-disk catalog FAIL when the loaded catalog is old', () => {
    const mutated = fs.readFileSync(CATALOG, 'utf8').replace("{ f: 'sculpt',", "{ f: 'sculptx',")
    expect(mutated).not.toBe(fs.readFileSync(CATALOG, 'utf8'))
    fs.writeFileSync(CATALOG, mutated, 'utf8')
    const cat = mod.catalogPayload({ verify: true })
    expect(cat.provenance.diskVerdict.families).toBe(mod.PLAN_CATALOG.length)
    expect(cat.provenance.diskVerdict.matchesLoadedCatalog).toBe(false)
    expect(cat.provenance.catalogHash).not.toBe(cat.provenance.diskVerdict.catalogHash)
    expect(cat.text).toContain('目录自证 FAIL')
    const note = String(cat.provenance.diskVerdict.note)
    expect(note.includes('重启后端') || note.includes('以干净进程那份为准')).toBe(true)
  })
})
