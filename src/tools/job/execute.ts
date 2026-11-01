/** blender_rt_job 执行层：参数校验 → 后端调用 → 回执装配（行为与 v2.6.2 一致）。 */
import { ensureBackend } from '../../host/backend/index.js'
import { backendPost, leasedText, splitArgs } from '../../host/http.js'
import { detectFileAlias } from '../../host/runtime-bridge.js'
import { toolError } from '../errors.js'
import { asRecord, optionalText, text } from '../../contracts/dynamic.js'
import {
  buildCollectRunning,
  buildFinalJob,
  buildJobKill,
  buildJobStart,
  buildLedgerJob,
  buildRecoveredJob,
  buildWaitTimeout,
  renderJobList,
} from './presenter.js'

interface JobCtx {
  mode: 'job'
  jobId: string | null
  runId: string | null
  notes: string[]
  hint: string | null
}

export async function executeJob(args: Record<string, unknown>, port: number): Promise<unknown> {
  if (!(await ensureBackend(port)))
    return toolError(
      'backend_unreachable',
      '后端不可用（127.0.0.1:' + String(port) + '）',
      '先 blender_viewport op=start 拉起后端，再重试',
    )
  const op = text(args.op) || 'list'
  // F-09 一致性：op=start/run 的 file= 与 rt_headless 同一套拦截（引擎 job 路径已有 validateFileOpt 兜底）
  const bad = validateFileArgs(op, args)
  if (bad) return bad
  const r = await backendPost(port, '/job', buildJobBody(op, args), budgetFor(op, args))
  const lt = leasedText(r)
  if (lt) return { text: lt }
  if (!r || r.ok !== true)
    return toolError(
      'backend_error',
      'JOB ' + op + ' 失败 · ' + text(r.error || r.raw || 'unknown'),
      '用 blender_rt_job op=list 查看作业',
    )
  if (op === 'list') return renderJobList(r)
  return reportJob(op, asRecord(r.job))
}

function validateFileArgs(op: string, args: Record<string, unknown>): unknown {
  if (op !== 'start' && op !== 'run') return null
  const aliasKeys = detectFileAlias(args)
  if (aliasKeys.length)
    return toolError(
      'invalid_params',
      'blender_rt_job 没有参数 ' +
        aliasKeys.join(', ') +
        '；打开 .blend 请用 file=。（收到的键：' +
        Object.keys(args).join(', ') +
        '）',
      '用 file=<.blend 路径>；参数名是 file，不是 blend/path',
    )
  if (typeof args.file === 'string' && !args.file.trim())
    return toolError(
      'invalid_params',
      'file= 传了空字符串 —— 本次不会打开任何 .blend（脚本会拿到出厂空场景）',
      '给真实 .blend 路径，或不要传 file=',
    )
  return null
}

function buildJobBody(op: string, args: Record<string, unknown>): Record<string, unknown> {
  const body: Record<string, unknown> = { op: op }
  if (args.id) body.id = text(args.id)
  if (args.script !== undefined) body.script = text(args.script)
  if (args.script_file) body.scriptFile = text(args.script_file)
  if (args.args) body.args = splitArgs(args.args)
  if (args.env && typeof args.env === 'object') body.env = args.env
  if (args.file) body.file = text(args.file)
  if (args.outdir) body.outdir = text(args.outdir)
  if (args.engine) body.engine = text(args.engine)
  if (args.preload) body.preload = text(args.preload)
  if (args.workdir) body.workdir = text(args.workdir)
  if (args.out_json) body.outJson = text(args.out_json)
  if (args.include_noise !== undefined) body.includeNoise = !!args.include_noise
  if (args.factory_startup !== undefined) body.factoryStartup = !!args.factory_startup
  if (args.bootstrap !== undefined) body.bootstrap = !!args.bootstrap
  if (args.tail) body.tail = Number(args.tail)
  if (args.timeout_ms) body.timeoutMs = Number(args.timeout_ms)
  return body
}

// v0.9.3（D6.2）：op=wait 会阻塞 → 给足 HTTP 预算（服务端默认 120 s/次，上限 600 s）
function budgetFor(op: string, args: Record<string, unknown>): number {
  return op === 'wait' ? Math.min(600000, Number(args.timeout_ms) || 120000) + 30000 : 60000
}

function reportJob(op: string, j: Record<string, unknown>): unknown {
  // ---- 统一回执：run/job 同一套字段（D2）
  const ctx: JobCtx = {
    mode: 'job',
    jobId: text(j.id).indexOf('job-') === 0 ? text(j.id) : null,
    runId: optionalText(j.runId) || null,
    notes: [],
    hint: null,
  }
  if (op === 'start') return buildJobStart(j, ctx)
  if (op === 'kill') return buildJobKill(j)
  if (j.recovered) return buildRecoveredJob(j, op)
  if (j.status === 'stale' || j.fromLedger) return buildLedgerJob(j, op)
  if (op === 'collect' && j.status === 'running') return buildCollectRunning(j, ctx)
  if (op === 'wait' && j.waitTimedOut) return buildWaitTimeout(j)
  const notes: string[] = []
  if (op === 'wait')
    notes.push(
      'op=wait 等到终态（等了 ' + String(Math.round(Number(j.waitedMs || 0) / 1000)) + 's）',
    )
  if (j.kind === 'headless' || text(j.id).indexOf('run-') === 0)
    notes.push('这是 blender_rt_headless 的运行台账（run 与 job 同一 id 空间）')
  return buildFinalJob(j, ctx, notes)
}
