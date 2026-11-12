import { afterEach, describe, expect, it } from 'vitest'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { resetHomeCache } from '../../../runtime/home.js'
import { createLedgerStore } from '../../../runtime/execution/ledger/state.js'

const tmpDirs: string[] = []

function tmpHome(): string {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'dsh-ledger-home-'))
  tmpDirs.push(dir)
  return dir
}

afterEach(() => {
  delete process.env.DSH_BLENDER_HOME
  resetHomeCache()
  while (tmpDirs.length) {
    const dir = tmpDirs.pop()
    if (dir) fs.rmSync(dir, { recursive: true, force: true })
  }
})

describe('jobs ledger home root', () => {
  it('stores index.jsonl under the resolved home', () => {
    const home = tmpHome()
    process.env.DSH_BLENDER_HOME = home
    resetHomeCache()
    const store = createLedgerStore()
    expect(store.LEDGER).toBe(path.join(home, 'jobs', 'index.jsonl'))
    store.ledgerAppend({ id: 'job-test', status: 'running' })
    expect(fs.existsSync(path.join(home, 'jobs', 'index.jsonl'))).toBe(true)
    const state = store.ledgerState() as any
    expect(state.path).toBe(path.join(home, 'jobs', 'index.jsonl'))
    expect(state.writable).toBe(true)
    expect(store.ledgerList().some((row: any) => row.id === 'job-test')).toBe(true)
  })

  it('copies a legacy workDir ledger only when the home ledger is missing', () => {
    const home = tmpHome()
    process.env.DSH_BLENDER_HOME = home
    resetHomeCache()
    // legacyWorkDirs() includes os.tmpdir()/dsh-blender-rt on this platform.
    const legacy = path.join(os.tmpdir(), 'dsh-blender-rt')
    const legacyLedger = path.join(legacy, 'jobs', 'index.jsonl')
    const hadLegacy = fs.existsSync(legacyLedger)
    const backup = hadLegacy ? fs.readFileSync(legacyLedger, 'utf8') : null
    try {
      fs.mkdirSync(path.dirname(legacyLedger), { recursive: true })
      fs.writeFileSync(legacyLedger, JSON.stringify({ id: 'job-legacy', status: 'done' }) + '\n')
      const store = createLedgerStore()
      expect(fs.existsSync(store.LEDGER)).toBe(true)
      expect(store.ledgerList().some((row: any) => row.id === 'job-legacy')).toBe(true)
      // A second store must not overwrite the already-migrated target.
      fs.appendFileSync(store.LEDGER, JSON.stringify({ id: 'job-new' }) + '\n')
      createLedgerStore()
      expect(fs.readFileSync(store.LEDGER, 'utf8')).toContain('job-new')
    } finally {
      if (hadLegacy && backup !== null) fs.writeFileSync(legacyLedger, backup)
      else fs.rmSync(legacyLedger, { force: true })
    }
  })
})
