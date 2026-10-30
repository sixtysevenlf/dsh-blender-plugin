
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { afterAll, beforeAll, describe, expect, it } from 'vitest'
import { startBackend, type Backend } from '../../helpers/backend'

// S8 recovery: a backend restart must recover a running job from the on-disk
// ledger instead of declaring success. Seeded ledger fixtures (no Blender):
// summary.json, stdout.log HEADLESS receipt, an empty dir, and an incomplete
// stdout that stopped at DSH_STAGE preload-done. The real-headless progress,
// warning and heartbeat cases need Blender and moved to the E2E layer.

let backend: Backend
const workDir = fs.mkdtempSync(path.join(os.tmpdir(), 'dsh-ledger-'))
const startedAt = Date.now() - 60000

function seed(id: string, files: Record<string, string>) {
  const dir = path.join(workDir, 'jobs', id)
  fs.mkdirSync(dir, { recursive: true })
  for (const [name, content] of Object.entries(files)) fs.writeFileSync(path.join(dir, name), content)
  return { id, kind: 'job', status: 'running', pid: 999999, startedAt, dir }
}

const records = [
  seed('job-e2e-summary', { 'summary.json': JSON.stringify({ status: 'done', exit_code: 0, ok: true }) }),
  seed('job-e2e-log', { 'stdout.log': 'DSH_HEADLESS begin\nAll done\nHEADLESS {"ok":true,"status":"done"}\n' }),
  seed('job-e2e-empty', {}),
  seed('job-e2e-incomplete', { 'stdout.log': 'DSH_STAGE {"name":"preload-done"}\n' }),
]

beforeAll(async () => {
  fs.writeFileSync(path.join(workDir, 'jobs', 'index.jsonl'),
    records.map((record) => JSON.stringify(record)).join('\n') + '\n')
  backend = await startBackend({ workDir })
})
afterAll(async () => { await backend.stop() })

const status = async (id: string) => {
  const response = await backend.post('/job', { op: 'status', id })
  expect(response.status).toBe(200)
  expect(response.json.ok).toBe(true)
  return response.json.job
}

describe('ledger recovery after restart', () => {
  it('recovers a done job from summary.json', async () => {
    const job = await status('job-e2e-summary')
    expect(job.status).toBe('done')
    expect(job.recovered).toBe(true)
    expect(job.recoveredFrom).toBe('summary.json')
  })

  it('recovers a done job from a HEADLESS receipt on stdout', async () => {
    const job = await status('job-e2e-log')
    expect(job.status).toBe('done')
    expect(job.recovered).toBe(true)
    expect(job.recoveredFrom).toBe('stdout.log')
  })

  it('reports an empty job dir as stale with evidence checked', async () => {
    const job = await status('job-e2e-empty')
    expect(job.status).toBe('stale')
    expect(Array.isArray(job.evidenceChecked)).toBe(true)
  })

  it('keeps an incomplete run stale and reports the last stage', async () => {
    const job = await status('job-e2e-incomplete')
    expect(job.status).toBe('stale')
    expect(job.evidence.lastStage).toBe('preload-done')
  })

  it('does not lie for an id the ledger never saw', async () => {
    const job = await status('job-never-existed')
    expect(job.status === 'unknown' || job.ok === false).toBe(true)
  })
})
