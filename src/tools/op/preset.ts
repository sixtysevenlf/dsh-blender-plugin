/** preset 执行层：参数收敛（含 data JSON 解析）→ POST /preset → 文本渲染。 */
import { ensureBackend } from '../../host/backend/index.js'
import { backendPost, leasedText } from '../../host/http.js'
import { asRecord, text } from '../../contracts/dynamic.js'
import { redactSecrets, toolError } from '../errors.js'
import { formatPreset } from './preset-response.js'

export async function executePreset(port: number, args: Record<string, unknown>): Promise<unknown> {
  if (!(await ensureBackend(port)))
    return toolError(
      'backend_unreachable',
      '后端不可用（127.0.0.1:' + String(port) + '）',
      '确认 DSH Gateway 在运行（宿主自动启动），再重试；Gateway 只有一份，不要另起后端。',
    )
  const op = text(args.op) || 'list'
  const built = buildPresetArgs(args)
  if ('error' in built) return built.error
  const r = await backendPost(port, '/preset', { op: op, args: built.args }, 300000)
  const lt = leasedText(r)
  if (lt) return { text: lt }
  if (!r || r.ok !== true)
    return toolError(
      'backend_error',
      'PRESET ' + op + ' 失败 · ' + redactSecrets(text(r.error || r.raw || 'unknown')),
      '核对 preset 数据与 op',
    )
  return { text: formatPreset(op, asRecord(r.result)) }
}

function splitList(v: unknown): string[] {
  return String(v)
    .split(',')
    .map((x) => x.trim())
    .filter(Boolean)
}

function buildPresetArgs(
  args: Record<string, unknown>,
): { args: Record<string, unknown> } | { error: unknown } {
  const a: Record<string, unknown> = {}
  if (text(args.name)) a.name = text(args.name)
  if (text(args.kind)) a.kind = text(args.kind)
  if (text(args.note)) a.note = text(args.note)
  if (text(args.path)) a.path = text(args.path)
  if (text(args.tags)) a.tags = splitList(args.tags)
  if (text(args.names)) a.names = splitList(args.names)
  if (text(args.targets)) a.targets = splitList(args.targets)
  if (args.data) {
    try {
      a.data = JSON.parse(String(args.data))
    } catch (e: unknown) {
      return {
        error: toolError(
          'invalid_params',
          'data 不是合法 JSON: ' + (e instanceof Error ? e.message.slice(0, 120) : 'unknown'),
          '传 JSON 对象字符串，例如 data={"k":1}',
        ),
      }
    }
  }
  if (args.overwrite === false) a.overwrite = false
  return { args: a }
}
