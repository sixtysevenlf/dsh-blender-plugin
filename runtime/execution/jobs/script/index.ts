// 作业脚本构建入口（拆分）：参数 / 前导 / 写盘
import { prepareJobArgs } from './args.js'
import type { JobBuildArgs } from './args.js'
import type { writeHeadlessScript } from '../../../staging.js'
import { buildJobParts, finalizeJobArgs } from './preload.js'
import type { JobsDeps } from '../types.js'

export type { JobBuildArgs } from './args.js'
export interface PreparedJob extends JobBuildArgs {
  sp: ReturnType<typeof writeHeadlessScript>
  timeoutMs: number
}

export function buildJobScript(E: JobsDeps, opts: Record<string, unknown> = {}): PreparedJob {
  const a = prepareJobArgs(E, opts)
  const built = buildJobParts(E, opts, a)
  finalizeJobArgs(a, built, opts)
  return {
    ...a,
    sp: built.sp,
    timeoutMs: built.timeoutMs,
  }
}
