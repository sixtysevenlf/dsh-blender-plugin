import type { ChildProcess } from 'node:child_process'
import type { StageRecord } from '../../core/progress.js'
import type { PathWarning } from '../../core/evidence.js'
import type { LedgerApi } from '../ledger/index.js'

export interface JobsDeps {
  HERE: string
  SESSION_NAME: string
  dumpResult: (
    obj: unknown,
    runId?: string,
  ) => { path: string | null; bytes: number; error?: string }
  preloadChunk: (name: string) => string
  readScriptPath: (p: unknown) => { win: string; wsl: string }
  ledger?: LedgerApi
}

export interface JobRecord extends StageRecord {
  [k: string]: unknown
  status: string
  runId: string
  child: ChildProcess | null
  pid: number | null | undefined
  outdir: string
  outdirWsl: string
  dir: string
  finishedAt?: number | null
  timeoutMs?: number | null
  logBytes?: number
  exitCode?: number | null
  lastOutputAt?: number | null
  progressAt?: number | null
  parsed: unknown
  parseError: { message: string; line: string } | null
  pathWarnings: PathWarning[]
  scriptStaging: import('../../staging.js').StagingInfo | null
  timer?: NodeJS.Timeout | null
}
