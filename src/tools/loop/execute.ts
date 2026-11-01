/** loop 执行层：GUI timers 通道与无头 worker session 通道。 */
import { BACKEND_HINT } from '../../constants.js'
import { ensureBackend } from '../../host/backend/index.js'
import { backendPost, leasedText } from '../../host/http.js'
import { asRecord, text } from '../../contracts/dynamic.js'
import { redactSecrets, toolError } from '../errors.js'
import { loopResultText, loopSessionError, loopSessionText } from './response.js'

export async function executeLoop(port: number, args: Record<string, unknown>): Promise<unknown> {
  if (!(await ensureBackend(port)))
    return toolError(
      'backend_unreachable',
      '后端不可用（127.0.0.1:' + String(port) + '）',
      BACKEND_HINT,
    )
  const op = text(args.op) || 'status'
  const target = text(args.target) || 'gui'
  if (target === 'session') return loopViaSession(port, args, op)
  return loopGui(port, args, op)
}

async function loopViaSession(
  port: number,
  args: Record<string, unknown>,
  op: string,
): Promise<unknown> {
  const nm = text(args.session) || 'dsh-loop'
  const st = await backendPost(
    port,
    '/worker',
    { op: 'start', name: nm, preload: 'runner', engine: 'workbench', warmup: true, isolated: true },
    120000,
  )
  if (!st || st.ok !== true)
    return toolError(
      'backend_error',
      'LOOP session 启动失败 · ' + text(st.error || st.raw || 'unknown'),
      '先 blender_rt_worker op=start 启动 worker，或改用 GUI 通道',
    )
  const code = buildSessionCode(op, args)
  const r = await backendPost(
    port,
    '/worker',
    { op: 'exec', name: nm, code: code, isolated: true },
    300000,
  )
  const rr = asRecord(r.result)
  if (!r || r.ok !== true)
    return toolError(
      'backend_error',
      loopSessionError(op, nm, redactSecrets(text(r.error || rr.error || 'unknown'))),
      '用 blender_rt_loop op=status 检查会话；必要时重新 op=start',
    )
  return { text: loopSessionText(op, nm, rr.result || rr || r) }
}

/** 按 op 生成 session 通道要执行的那段 Python（内层 JSON 双重编码，与旧实现逐字一致）。 */
function buildSessionCode(op: string, args: Record<string, unknown>): string {
  const specJ = JSON.stringify(JSON.stringify(args.spec || {}))
  const nl = String.fromCharCode(10)
  return op === 'start'
    ? 'import json as _json' +
        nl +
        '_s = _json.loads(' +
        specJ +
        ')' +
        nl +
        '_r = K.dsh_loop_api["sync"](_s)' +
        nl +
        'print("HEADLESS " + (_r if isinstance(_r, str) else _json.dumps(_r, ensure_ascii=False)))'
    : op === 'status'
      ? 'print("HEADLESS " + K.dsh_loop_api["status"](' +
        String(Number(args.history || 8)) +
        ', ' +
        String(Number(args.board || 0)) +
        '))'
      : op === 'board'
        ? 'print("HEADLESS " + K.dsh_loop_api["board"](' +
          String(Number(args.limit || 10)) +
          ', ' +
          (args.groups === false ? 'False' : 'True') +
          '))'
        : op === 'export'
          ? 'print("HEADLESS " + K.dsh_loop_api["export"](' +
            JSON.stringify(args.path ? text(args.path) : null) +
            ', ' +
            String(Number(args.top || 1)) +
            ', ' +
            (args.include_variants ? 'True' : 'False') +
            ', ' +
            JSON.stringify(text(args.note)) +
            '))'
          : op === 'stop'
            ? 'print("HEADLESS " + K.dsh_loop_api["stop"]())'
            : op === 'bench'
              ? 'print("HEADLESS " + K.dsh_loop_api["bench"](' +
                String(Number(args.iterations || 5000)) +
                '))'
              : 'print("HEADLESS " + K.dsh_loop_api["help"]())'
}

async function loopGui(port: number, args: Record<string, unknown>, op: string): Promise<unknown> {
  const payload: Record<string, unknown> = {
    op: op,
    spec: args.spec || {},
    history: args.history || 8,
    board: args.board || 0,
    limit: args.limit || 10,
    groups: !(args.groups === false),
    path: args.path,
    top: args.top || 1,
    include_variants: !!args.include_variants,
    note: args.note || '',
    iterations: args.iterations || 5000,
  }
  const r = await backendPost(port, '/loop', payload, 300000)
  const lt = leasedText(r)
  if (lt) return { text: lt }
  if (!r || r.ok !== true)
    return toolError(
      'backend_error',
      'LOOP ' + op + ' 失败 · ' + text(r.error || r.raw || 'unknown'),
      '用 blender_rt_loop op=status 检查会话',
    )
  return { text: loopResultText(op, r.result) }
}
