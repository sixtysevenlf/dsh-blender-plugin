import { afterAll, describe, expect, it } from 'vitest'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { logEvidence, reconcileLedger, recoverFromDisk } from '../../../runtime/recover.js'

// Feedback #1 ("台账以文件为准"): after a backend restart a ledger row can say
// running while the child already finished. Completion is decided by on-disk
// evidence, never by the pid alone; missing evidence is "stale", never "done".

const TMP = fs.mkdtempSync(path.join(os.tmpdir(), 'dsh-recover-'))
const NL = String.fromCharCode(10)
const map = { winToWsl: (p: string) => p, wslToWin: (p: string) => p }
const mk = (name: string) => { const d = path.join(TMP, name); fs.mkdirSync(d, { recursive: true }); return d }
const write = (p: string, s: string) => fs.writeFileSync(p, s, 'utf8')
const rec = (extra: Record<string, unknown> = {}) => Object.assign(
  { id: 'job-x', kind: 'job', status: 'running', pid: 999999, startedAt: Date.now() - 60000 }, extra)

interface Recovered {
  status?: string
  recovered?: unknown
  recoveredFrom?: string
  recoveredEvidence?: { path?: string } | null
  artifacts?: unknown[]
  evidence?: { lastStage?: string } | null
  evidenceChecked?: string[]
  [key: string]: unknown
}
const R = (value: unknown): Recovered => value as Recovered

afterAll(() => { try { fs.rmSync(TMP, { recursive: true, force: true }) } catch { /* ignore */ } })

describe('ledger recovery', () => {
  it('recovers status from summary.json and keeps artifacts', () => {
    const d = mk('A')
    write(path.join(d, 'summary.json'), JSON.stringify({ id: 'job-x', status: 'done', exitCode: 0, finishedAt: 123, artifacts: ['a.blend'] }))
    const r = R(reconcileLedger(rec({ dir: d }), map, () => false))
    expect(r).toMatchObject({ status: 'done', recovered: true, recoveredFrom: 'summary.json' })
    expect(r.recoveredEvidence && r.recoveredEvidence.path).toBeTruthy()
    expect(r.artifacts).toEqual(['a.blend'])
  })

  it('keeps a failed summary failed instead of calling it done', () => {
    const d = mk('A2')
    write(path.join(d, 'summary.json'), JSON.stringify({ id: 'job-x', status: 'failed', exitCode: 1, finishedAt: 124 }))
    expect(reconcileLedger(rec({ dir: d }), map, () => false)).toMatchObject({ status: 'failed', recovered: true })
  })

  it('recovers from resultPath', () => {
    const d = mk('B')
    const rp = path.join(d, 'result.json')
    write(rp, '{"ok":true}')
    expect(reconcileLedger(rec({ resultPath: rp }), map, () => false)).toMatchObject({ status: 'done', recoveredFrom: 'resultPath' })
  })

  it('recovers from a DSH_STAGE done line in stdout.log', () => {
    const d = mk('C')
    const so = path.join(d, 'stdout.log')
    write(so, ['DSH_STAGE {"name": "boot", "run": "run-x"}', 'DSH_STAGE {"name": "done", "run": "run-x"}', ''].join(NL))
    const r = R(reconcileLedger(rec({ stdoutLog: so }), map, () => false))
    expect(r).toMatchObject({ status: 'done', recoveredFrom: 'stdout.log', stage: 'done' })
  })

  it('treats a HEADLESS receipt as terminal', () => {
    const d = mk('D')
    const so = path.join(d, 'stdout.log')
    write(so, ['noise', 'HEADLESS {"ok": true, "status": "done"}', ''].join(NL))
    expect(R(reconcileLedger(rec({ stdoutLog: so }), map, () => false))).toMatchObject({ status: 'done', recovered: true })
  })

  it('treats a Traceback as failed, not done', () => {
    const d = mk('E')
    const so = path.join(d, 'stdout.log')
    write(so, ['Traceback (most recent call last):', '  File "x.py", line 1', 'TypeError: boom', ''].join(NL))
    expect(R(reconcileLedger(rec({ stdoutLog: so }), map, () => false))).toMatchObject({ status: 'failed', recovered: true })
  })

  it('reports stale (with lastStage) when logs exist without a terminal line', () => {
    const d = mk('F')
    const so = path.join(d, 'stdout.log')
    write(so, ['DSH_STAGE {"name": "boot", "run": "run-x"}', 'DSH_STAGE {"name": "preload-done"}', 'building geometry...', ''].join(NL))
    const r = R(reconcileLedger(rec({ stdoutLog: so }), map, () => false))
    expect(r.status).toBe('stale')
    expect(r.recovered).not.toBe(true)
    expect(r.evidence && r.evidence.lastStage).toBe('preload-done')
  })

  it('reports stale and names the checked evidence when nothing exists', () => {
    const r = R(reconcileLedger(rec({ dir: mk('G') }), map, () => false))
    expect(r.status).toBe('stale')
    expect(r.evidenceChecked).toEqual(['summary.json', 'resultPath', 'stdout.log'])
  })

  it('never steals a task whose pid is still alive', () => {
    const r = R(reconcileLedger(rec({ dir: mk('H') }), map, () => true))
    expect(r.status).toBe('running')
    expect(r.recovered).not.toBe(true)
  })

  it('reads the tail of a large log (>256 KiB)', () => {
    const d = mk('I')
    const so = path.join(d, 'stdout.log')
    write(so, 'x'.repeat(300 * 1024) + NL + 'DSH_STAGE {"name": "done"}' + NL)
    const ev = logEvidence(so)
    expect(ev.exists).toBe(true)
    expect(ev.sawDone).toBe(true)
    expect(ev.bytes).toBeGreaterThan(300 * 1024)
    expect(R(reconcileLedger(rec({ stdoutLog: so }), map, () => false)).status).toBe('done')
  })

  it('recovers a job-directory record through recoverFromDisk', () => {
    const d = mk('J')
    write(path.join(d, 'stdout.log'), 'HEADLESS {"ok":true}' + NL)
    expect(recoverFromDisk(rec({ dir: d }), map)).toMatchObject({ found: true, status: 'done', source: 'stdout.log' })
  })

  it('returns a terminal record untouched', () => {
    const r = R(reconcileLedger({ id: 'x', status: 'done' }, map, () => false))
    expect(r.status).toBe('done')
    expect(r.recovered).not.toBe(true)
  })
})
