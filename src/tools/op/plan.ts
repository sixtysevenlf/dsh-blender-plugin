/** blender_op action=plan：catalog 契约算子执行（会话门由 dispatcher 先做）。 */
import { BACKEND_HINT, T } from '../../constants.js'
import { ensureBackend } from '../../host/backend/index.js'
import { backendPost, jsonPayload, leasedText } from '../../host/http.js'
import { text } from '../../contracts/dynamic.js'
import type { HostContext } from '../../host/context.js'
import { toolError } from '../errors.js'
import { finishPlanResponse } from './plan-response.js'
import { isHostLocalOp } from './read-only.js'
import { isSessionRead } from './session-read.js'

/**
 * Build the /plan body. Session-read ops (animation_report/help) must not
 * declare a write_scope: the Gateway gates them through sessionReadGate, and a
 * declared scope would look like a write request. Host-local ops (vision_*) run
 * inside the Gateway process with no session or lease, so they must not declare
 * a scope either.
 */
export function planRequestBody(
  op: string,
  argsIn: Record<string, unknown>,
  payload: Record<string, unknown>,
  routed: { sessionId?: string; instanceId?: string } = {},
): Record<string, unknown> {
  const body: Record<string, unknown> = { op: op, args: payload }
  if (!isSessionRead(op) && !isHostLocalOp(op))
    body.write_scope = text(argsIn.write_scope).trim() || 'exclusive'
  if (routed.sessionId) body.sessionId = routed.sessionId
  if (routed.instanceId) body.instanceId = routed.instanceId
  return body
}

export async function executePlan(
  ctx: HostContext,
  port: number,
  argsIn: Record<string, unknown>,
  routed: { sessionId?: string; instanceId?: string } = {},
): Promise<unknown> {
  if (!(await ensureBackend(port)))
    return toolError(
      'gateway_unreachable',
      'Gateway 不可用（127.0.0.1:' + String(port) + '）',
      BACKEND_HINT,
    )
  const op = text(argsIn.op) || 'status'
  const payload = jsonPayload(argsIn.args)
  if (
    !Object.keys(payload).length &&
    typeof argsIn.args === 'string' &&
    argsIn.args.trim() &&
    argsIn.args.trim() !== '{}'
  )
    return toolError(
      'invalid_params',
      'OP ' + op + ' 失败 · args 不是合法 JSON 对象：' + argsIn.args.slice(0, 200),
      'args 传 JSON 对象，例如 {"family":"audit"}',
    )
  if (op === 'report' && argsIn.path) payload.path = text(argsIn.path)
  const body = planRequestBody(op, argsIn, payload, routed)
  const r = await backendPost(port, '/plan', body, T.plan)
  const lt = leasedText(r)
  if (lt) return { text: lt }
  if (!r || r.ok !== true)
    return toolError(
      'backend_error',
      'OP ' + op + ' 失败 · ' + text(r.error || r.raw || 'unknown'),
      '检查 /plan op 名与参数；不确定先 blender_op action=plan op=catalog',
    )
  return await finishPlanResponse(ctx, op, r.result, argsIn)
}
