/**
 * blender_run：唯一作业入口（第 6 节有界队列的模型面）。
 *
 * submit 立即返回 jobId；之后用 status/wait/collect/cancel/list/perf 观察。
 * 没有同步模式、没有第二个作业工具。tune 保留旧 Cycles 分析（/perf）。
 */
import { BACKEND_HINT, T } from '../../constants.js'
import type { AvailabilityStage } from '../../contracts/availability.js'
import { text } from '../../contracts/dynamic.js'
import { ensureBackend } from '../../host/backend/index.js'
import { backendPost, jsonPayload } from '../../host/http.js'
import { toolError } from '../errors.js'
import {
  formatCancel,
  formatCollect,
  formatJob,
  formatList,
  formatPerf,
  formatSubmit,
  formatWait,
  runHelp,
} from './presenter.js'

const RENDER_KEYS = [
  'file',
  'output',
  'outdir',
  'frames',
  'resolution',
  'samples',
  'engine',
  'format',
  'camera',
]
const HEADLESS_KEYS = [
  'script',
  'script_file',
  'file',
  'outdir',
  'timeout_ms',
  'engine',
  'preload',
  'factory_startup',
  'bootstrap',
  'workdir',
  'include_noise',
  'out_json',
  'args',
  'env',
  'shots',
  'expect',
  'hard_exit',
  'gpu',
  'run_id',
]
const ACTIONS = ['submit', 'status', 'wait', 'collect', 'cancel', 'list', 'perf', 'tune', 'help']

function present(v: unknown): boolean {
  return v !== undefined && v !== null && String(v).trim() !== ''
}

function failureOf(body: Record<string, unknown>, action: string): unknown {
  const code = text(body.code) || 'backend_error'
  const stage = typeof body.stage === 'string' ? (body.stage as AvailabilityStage) : undefined
  if (code === 'queue_unknown_job')
    return toolError(
      'queue_unknown_job',
      'RUN ' + action + ' 失败 · 没有这个 jobId：' + text(body.root_cause || body.error || ''),
      '先 blender_run action=list 取现有 jobId',
      JSON.stringify(body.evidence || {}),
      'queue',
    )
  if (body.root_cause || body.fix)
    return toolError(
      code,
      'RUN ' + action + ' 失败 · ' + text(body.root_cause || body.error || 'unknown'),
      text(body.fix || '读日志修原因后重新 submit'),
      body.evidence ? JSON.stringify(body.evidence) : undefined,
      stage,
    )
  return toolError(
    'backend_error',
    'RUN ' + action + ' 失败 · ' + text(body.error || body.raw || 'unknown'),
    '先 blender_session action=status 看 Gateway/addon 状态',
    undefined,
    stage,
  )
}

async function postQueue(
  port: number,
  payload: Record<string, unknown>,
  timeoutMs: number,
): Promise<Record<string, unknown>> {
  try {
    return await backendPost(port, '/job', { surface: 'queue', ...payload }, timeoutMs)
  } catch (e: unknown) {
    return {
      ok: false,
      code: 'gateway_unreachable',
      stage: 'transport',
      root_cause: 'Gateway 不可达 · ' + text(e instanceof Error ? e.message : e),
      fix: BACKEND_HINT,
    }
  }
}

function submitPayload(args: Record<string, unknown>): Record<string, unknown> | string {
  const kind = text(args.kind).trim()
  if (kind !== 'render' && kind !== 'headless')
    return 'submit 需要 kind=render|headless（render=声明式最终渲染；headless=跑脚本）'
  const payload: Record<string, unknown> = { op: 'submit', kind }
  if (present(args.priority)) payload.priority = Number(args.priority)
  if (kind === 'render') {
    if (present(args.script) || present(args.script_file))
      return 'render 不接受 script/script_file：最终渲染是声明式入口；跑脚本请用 kind=headless 或 blender_py'
    for (const k of RENDER_KEYS) if (present(args[k])) payload[k] = args[k]
    return payload
  }
  if (!present(args.script) && !present(args.script_file) && !present(args.file))
    return 'headless 需要 script / script_file / file 之一'
  for (const k of HEADLESS_KEYS) if (present(args[k])) payload[k] = args[k]
  return payload
}

async function tune(port: number, args: Record<string, unknown>): Promise<unknown> {
  const op = text(args.op).trim() || 'status'
  const pargs = jsonPayload(args.args)
  let r: Record<string, unknown>
  try {
    r = await backendPost(
      port,
      '/perf',
      { op, args: Object.keys(pargs).length ? pargs : undefined },
      T.perf,
    )
  } catch (e: unknown) {
    return toolError('gateway_unreachable', 'RUN tune 失败 · ' + text(e), BACKEND_HINT)
  }
  if (!r || r.ok !== true)
    return toolError(
      'backend_error',
      'RUN tune ' + op + ' 失败 · ' + text(r.error || r.raw || 'unknown'),
      'op=status 看渲染设置；analyze 需要已打开的场景',
    )
  return { text: 'RUN tune ' + op + ' ' + JSON.stringify(r.result ?? r) }
}

export async function executeRun(port: number, args: Record<string, unknown>): Promise<unknown> {
  const action = text(args.action).trim() || (present(args.kind) ? 'submit' : 'list')
  if (action === 'help') return { text: runHelp() }
  if (!ACTIONS.includes(action))
    return toolError(
      'invalid_params',
      '未知 blender_run action：' + action,
      '可用：' + ACTIONS.join(' | '),
    )
  if (!(await ensureBackend(port)))
    return toolError(
      'gateway_unreachable',
      'Gateway 不可用（127.0.0.1:' + String(port) + '）',
      BACKEND_HINT,
    )
  if (action === 'tune') return tune(port, args)

  if (action === 'submit') {
    const built = submitPayload(args)
    if (typeof built === 'string')
      return toolError('invalid_params', built, 'action=help 查看 render/headless 参数')
    const body = await postQueue(port, built, 60000)
    if (body.ok !== true) return failureOf(body, action)
    return { text: formatSubmit(body.job as Record<string, unknown>) }
  }
  if (action === 'list') {
    const payload: Record<string, unknown> = { op: 'list' }
    for (const k of ['state', 'kind', 'owner']) if (present(args[k])) payload[k] = args[k]
    const body = await postQueue(port, payload, 60000)
    if (body.ok !== true) return failureOf(body, action)
    return {
      text: formatList(
        Array.isArray(body.jobs) ? (body.jobs as Array<Record<string, unknown>>) : [],
      ),
    }
  }
  if (action === 'perf') {
    const body = await postQueue(port, { op: 'perf' }, 60000)
    if (body.ok !== true) return failureOf(body, action)
    return { text: formatPerf((body.perf || {}) as Record<string, unknown>) }
  }
  if (!present(args.id))
    return toolError(
      'invalid_params',
      action + ' 需要 id（jobId）',
      '先 action=submit 或 action=list',
    )
  const id = text(args.id)
  if (action === 'status') {
    const body = await postQueue(port, { op: 'status', id }, 60000)
    if (body.ok !== true) return failureOf(body, action)
    return { text: formatJob(body.job as Record<string, unknown>).join(String.fromCharCode(10)) }
  }
  if (action === 'wait') {
    const timeoutMs = Number(args.timeout_ms) || 120000
    const budget = Math.min(600000, Math.max(1000, timeoutMs)) + 30000
    const body = await postQueue(port, { op: 'wait', id, timeout_ms: timeoutMs }, budget)
    if (body.ok !== true) return failureOf(body, action)
    return {
      text: formatWait(body.job as Record<string, unknown>, body.waitedMs, body.waitTimedOut),
    }
  }
  if (action === 'collect') {
    const body = await postQueue(
      port,
      { op: 'collect', id, tail: Number(args.tail) || 4000 },
      60000,
    )
    if (body.ok !== true) return failureOf(body, action)
    return { text: formatCollect(body.job as Record<string, unknown>) }
  }
  // cancel
  const body = await postQueue(port, { op: 'cancel', id }, 60000)
  if (body.ok !== true) return failureOf(body, action)
  return { text: formatCancel(body) }
}
