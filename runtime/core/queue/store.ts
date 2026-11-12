// Queue persistence: one JSON file, atomic replacement, corrupt-file quarantine.
import fs from 'node:fs'
import path from 'node:path'
import { legacyWorkDirs } from '../../config.js'
import { homePath } from '../../home.js'
import type { LegacyMigration } from '../../home.js'
import { migrateLegacyFile } from '../../home.js'
import type { JobRecord, JobStore } from './types.js'

/**
 * v3.2: queue state lives in the plug-in home, not in the legacy workDir.
 * DSH_QUEUE_STATE still overrides the file (tests / embedded hosts).
 */
export function defaultQueueFile(): string {
  const override = process.env.DSH_QUEUE_STATE
  if (override) return override
  const target = homePath('queue', 'queue.json')
  migrateLegacyQueueState(target)
  return target
}

/**
 * Copy the legacy workDir state file once when the home queue is still
 * missing. The legacy file is read-only input: it is never modified or
 * deleted, so an old Gateway version can still start from it.
 */
export function migrateLegacyQueueState(target: string): LegacyMigration[] {
  const out: LegacyMigration[] = []
  if (fs.existsSync(target)) return out
  for (const dir of legacyWorkDirs()) {
    const from = path.join(dir, 'state', 'queue.json')
    if (path.resolve(from) === path.resolve(target)) continue
    const migration = migrateLegacyFile(from, target)
    if (migration.result === 'missing') continue
    out.push(migration)
    if (migration.result === 'copied') break
  }
  return out
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

const STATES: readonly string[] = [
  'queued',
  'assigned',
  'running',
  'completed',
  'failed',
  'canceled',
]

function text(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

function num(value: unknown): number {
  const n = Number(value)
  return Number.isFinite(n) ? n : 0
}

function numOrNull(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function ownerRow(value: unknown): JobRecord['owner'] | null {
  if (!isRecord(value)) return null
  const hostId = text(value.hostId)
  const dshSessionId = text(value.dshSessionId)
  const agentId = text(value.agentId)
  if (!hostId || !dshSessionId || !agentId) return null
  const parentAgentId = text(value.parentAgentId)
  return { hostId, dshSessionId, agentId, ...(parentAgentId ? { parentAgentId } : {}) }
}

/** Keep persisted rows that still carry the fields the queue reads. */
function sanitizeRow(value: unknown): JobRecord | null {
  if (!isRecord(value)) return null
  const id = text(value.id)
  const kind = value.kind === 'render' || value.kind === 'headless' ? value.kind : null
  const state = typeof value.state === 'string' && STATES.includes(value.state) ? value.state : null
  const params = isRecord(value.params) ? value.params : null
  const owner = ownerRow(value.owner)
  if (!id || !kind || !state || !params || !owner) return null
  const logs = isRecord(value.logs) ? value.logs : null
  const tempDir = text(value.tempDir)
  return {
    id,
    kind,
    params,
    state: state as JobRecord['state'],
    priority: Math.max(-100, Math.min(100, Math.trunc(num(value.priority)))),
    slot: value.slot === 'gpu' || value.slot === 'cpu' ? value.slot : null,
    owner,
    createdAt: num(value.createdAt),
    updatedAt: num(value.updatedAt),
    assignedAt: numOrNull(value.assignedAt),
    startedAt: numOrNull(value.startedAt),
    finishedAt: numOrNull(value.finishedAt),
    pid: numOrNull(value.pid),
    decision: isRecord(value.decision)
      ? (value.decision as unknown as JobRecord['decision'])
      : null,
    result: isRecord(value.result) ? (value.result as unknown as JobRecord['result']) : null,
    artifacts: Array.isArray(value.artifacts)
      ? value.artifacts.filter((x) => typeof x === 'string')
      : [],
    logs: {
      stdout: logs ? text(logs.stdout) : '',
      stderr: logs ? text(logs.stderr) : '',
    },
    error: isRecord(value.error) ? (value.error as unknown as JobRecord['error']) : null,
    events: Array.isArray(value.events)
      ? (value.events.filter((x) => isRecord(x)) as unknown as JobRecord['events'])
      : [],
    ...(tempDir ? { tempDir } : {}),
  }
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
