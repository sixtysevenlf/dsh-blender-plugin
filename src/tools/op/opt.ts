/** blender_op action=opt：对象精简（analyze / join），会话门由 dispatcher 先做。 */
import { BACKEND_HINT, T } from '../../constants.js'
import { ensureBackend } from '../../host/backend/index.js'
import { backendPost, jsonPayload, leasedText } from '../../host/http.js'
import { text } from '../../contracts/dynamic.js'
import { toolError } from '../errors.js'

export async function executeOpt(
  port: number,
  args: Record<string, unknown>,
  routed: { sessionId?: string; instanceId?: string } = {},
): Promise<unknown> {
  if (!(await ensureBackend(port)))
    return toolError(
      'gateway_unreachable',
      'Gateway 不可用（127.0.0.1:' + String(port) + '）',
      BACKEND_HINT,
    )
  const op = text(args.op) || 'analyze'
  const oargs = jsonPayload(args.args)
  const body: Record<string, unknown> = {
    op: op === 'join' ? 'opt_join' : 'opt_analyze',
    args: Object.keys(oargs).length ? oargs : undefined,
    write_scope: text(args.write_scope).trim() || 'exclusive',
  }
  if (routed.sessionId) body.sessionId = routed.sessionId
  if (routed.instanceId) body.instanceId = routed.instanceId
  const r = await backendPost(port, '/opt', body, T.opt)
  const lt = leasedText(r)
  if (lt) return { text: lt }
  if (!r || r.ok !== true)
    return toolError(
      'backend_error',
      'OPT ' + op + ' 失败 · ' + text(r.error || r.raw || 'unknown'),
      '核对 op 名称；blender_op action=opt op=help',
    )
  return { text: 'OPT ' + op + ' ' + JSON.stringify(r.result) }
}
