/** txn 执行层：参数收敛 → POST /txn → 文本渲染。 */
import { ensureBackend } from '../../host/backend/index.js'
import { backendPost, leasedText } from '../../host/http.js'
import { asRecord, text } from '../../contracts/dynamic.js'
import { toolError } from '../errors.js'
import { formatTxn } from './response.js'

export async function executeTxn(port: number, args: Record<string, unknown>): Promise<unknown> {
  if (!(await ensureBackend(port)))
    return toolError(
      'backend_unreachable',
      '后端不可用（127.0.0.1:' + String(port) + '）',
      '先 blender_viewport op=start 拉起后端，再重试',
    )
  const op = text(args.op) || 'list'
  const a = buildTxnArgs(args)
  const r = await backendPost(port, '/txn', { op: op, args: a }, 300000)
  const lt = leasedText(r)
  if (lt) return { text: lt }
  if (!r || r.ok !== true)
    return toolError(
      'backend_error',
      'TXN ' + op + ' 失败 · ' + text(r.error || r.raw || 'unknown'),
      '用 blender_rt_txn op=list 查看快照',
    )
  return { text: formatTxn(op, asRecord(r.result)) }
}

function buildTxnArgs(args: Record<string, unknown>): Record<string, unknown> {
  const a: Record<string, unknown> = {}
  if (args.label) a.label = text(args.label)
  if (args.note) a.note = text(args.note)
  if (args.keep) a.keep = Number(args.keep)
  if (args.into) a.into = text(args.into)
  if (typeof args.backup === 'boolean') a.backup = args.backup
  if (args.objects)
    a.objects = text(args.objects)
      .split(',')
      .map((s) => s.trim())
      .filter(Boolean)
  return a
}
