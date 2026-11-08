// Queue persistence: one JSON file, atomic replacement, corrupt-file quarantine.
import fs from 'node:fs'
import path from 'node:path'
import { CFG, IS_WIN } from '../../config.js'
import type { JobRecord, JobStore } from './types.js'

export function defaultQueueFile(): string {
  const override = process.env.DSH_QUEUE_STATE
  if (override) return override
  const workdir = IS_WIN ? CFG.workDirWin : CFG.workDirWsl
  return path.join(workdir, 'state', 'queue.json')
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

/** Keep persisted rows that still carry the fields the queue reads. */
function sanitizeRow(value: unknown): JobRecord | null {
  if (!isRecord(value)) return null
  const id = typeof value.id === 'string' ? value.id : ''
  const kind = value.kind === 'render' || value.kind === 'headless' ? value.kind : null
  const state = typeof value.state === 'string' ? value.state : ''
  if (!id || !kind || !state) return null
  return value as unknown as JobRecord
}

export class JsonJobStore implements JobStore {
  constructor(private readonly file: string = defaultQueueFile()) {}

  load(): JobRecord[] {
    let raw: string
    try {
      raw = fs.readFileSync(this.file, 'utf8')
    } catch (e) {
      if (isRecord(e) && (e as { code?: string }).code === 'ENOENT') return []
      throw e
    }
    try {
      const parsed = JSON.parse(raw)
      const rows = isRecord(parsed) && Array.isArray(parsed.jobs) ? parsed.jobs : null
      if (!rows) throw new Error('missing jobs array')
      return rows.map(sanitizeRow).filter((row): row is JobRecord => row !== null)
    } catch {
      // A truncated write must not stop the Gateway. Keep the damaged file for
      // inspection and start from an empty queue instead of guessing.
      try {
        fs.renameSync(this.file, this.file + '.corrupt-' + Date.now())
      } catch {
        /* quarantine is best effort */
      }
      return []
    }
  }

  save(jobs: JobRecord[]): void {
    const dir = path.dirname(this.file)
    fs.mkdirSync(dir, { recursive: true })
    const tmp = this.file + '.tmp-' + process.pid
    const body = JSON.stringify({ version: 1, jobs }, null, 2) + '\n'
    fs.writeFileSync(tmp, body, 'utf8')
    fs.renameSync(tmp, this.file)
  }
}

/** In-memory store used by unit tests and by a queue that should not persist. */
export class MemoryJobStore implements JobStore {
  jobs: JobRecord[] = []
  saves = 0
  load(): JobRecord[] {
    return JSON.parse(JSON.stringify(this.jobs)) as JobRecord[]
  }
  save(jobs: JobRecord[]): void {
    this.saves += 1
    this.jobs = JSON.parse(JSON.stringify(jobs)) as JobRecord[]
  }
}
