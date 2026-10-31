import type { ChildProcess } from 'node:child_process'
import type { StageRecord } from '../../core/progress.js'
import type { StagingInfo } from '../../staging.js'

export interface HeadlessOpts {
  [k: string]: unknown
}

export interface SpawnResult {
  so: string
  se: string
  exitCode: number | null
  signal: NodeJS.Signals | null
  spawnErr: Error | null
  timedOut: boolean
}

export interface HeadlessRunRec extends StageRecord {
  [k: string]: unknown
  pathWarnings: unknown[]
  artifacts: string[]
  logs: { stdout: string; stderr: string; stdoutWsl: string; stderrWsl: string } | null
  scriptStaging: StagingInfo | null
  child: ChildProcess | null
  pid: number | null | undefined
}

export interface HeadlessDeps {
  HERE: string
  LEDGER: string
  SESSION_NAME: string
  dumpResult: (
    obj: unknown,
    runId?: string,
  ) => { path: string | null; bytes: number; error?: string }
  jobStart: (opts?: Record<string, unknown>) => Record<string, unknown> & { id: string }
  ledgerAppend: (rec: Record<string, unknown>) => void
  metrics: { headlessRuns: number; lastHeadlessMs?: number; [k: string]: unknown }
  preloadChunk: (name: string) => string
  readScriptPath: (p: unknown) => { win: string; wsl: string }
  runs: Map<string, HeadlessRunRec>
}

export interface ScriptFileInfo {
  given: string
  resolved: string
  bytes: number
  auto_from_file: boolean
}

export interface PreparedHeadless {
  opts: HeadlessOpts
  outdirArg: string | null
  outdirWsl: string | null
  outdirWin: string
  scriptFileInfo: ScriptFileInfo | null
  fileOpt: string | undefined
  fileLooksPython: boolean
  inputFileInfo: unknown
  script: string
  mods: unknown[]
  shotsSpec: Record<string, unknown> | null
  shotsWanted: boolean
  engineMode: string
  gpuOff: boolean
  gpuManual: boolean
  runId: string
  envPairs: Record<string, string | null>
  envOpt: Record<string, unknown> | null
  exp: Record<string, unknown> | null
  pre: string
  gpuPre: string
  timeoutMs: number
}
