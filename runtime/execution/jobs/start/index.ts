// 作业启动入口（拆分）：记录 / spawn / 收尾
import fs from 'node:fs'
import { jobSnapshot } from '../snapshot.js'
import { errorMessage } from '../../../core/json.js'
import { buildJobScript } from '../script/index.js'
import type { JobRecord } from '../types.js'
import type { JobsDeps } from '../types.js'
import { buildJobRecord, collectJobOutputs, persistJobResult } from './args.js'
import { spawnJobChild } from './spawn.js'

export interface JobStartDeps {
  E: JobsDeps
  jobs: Map<string, JobRecord>
  ledgerAppend: (rec: Record<string, unknown>) => void
}

export function startJob(
  d: JobStartDeps,
  opts: Record<string, unknown> = {},
): Record<string, unknown> {
  const p = buildJobScript(d.E, opts)
  const so = fs.createWriteStream(p.outPath),
    se = fs.createWriteStream(p.errPath)
  // Log streams are best-effort: an outdir removed while a stream is still opening must not
  // surface as an unhandled ENOENT and fail an otherwise successful job.
  so.on('error', () => {})
  se.on('error', () => {})
  const j = buildJobRecord(p)
  const { child, stopHb } = spawnJobChild(d, j, p, so, se, opts)
  child.on('close', (code, sig) => {
    if (j.timer) clearTimeout(j.timer)
    stopHb()
    j.exitCode = code
    j.signal = sig
    j.finishedAt = Date.now()
    if (j.status !== 'killed') j.status = code === 0 ? 'done' : 'failed'
    collectJobOutputs(j, p)
    persistJobResult(d, j, p, opts)
  })
  child.on('error', (e) => {
    j.status = 'failed'
    j.lastException = errorMessage(e)
  })
  return jobSnapshot(j)
}
