/** blender_viewport 的执行层：op 分发 + 每个 op 一个阶段函数（行为与 v2.6.2 一致）。 */
import { HOLDER, SERVER_PATH } from '../../constants.js'
import {
  base,
  killBackendByCmd,
  killChildHandle,
  setPaused,
  sleep,
  startBackend,
} from '../../host/backend/index.js'
import { backendJson, backendPost } from '../../host/http.js'
import { probeHttp, waitHttp } from '../../host/runtime-bridge.js'
import { errorMessage, text } from '../../contracts/dynamic.js'
import {
  formatDoctor,
  formatLaunch,
  formatLeaseError,
  formatLeaseResult,
  formatStatus,
  formatStatusError,
  formatWho,
} from './presenter.js'

export async function executeViewport(
  args: Record<string, unknown>,
  port: number,
): Promise<string> {
  const op = text(args.op) || 'status'
  if (op === 'start') return opStart(port)
  if (op === 'restart') return opRestart(port)
  if (op === 'stop') return opStop(port)
  if (op === 'who' || op === 'lease' || op === 'release') return opLease(op, args, port)
  if (op === 'launch') return opLaunch(args, port)
  if (op === 'doctor') return opDoctor(port)
  return opStatus(port)
}

async function opStart(port: number): Promise<string> {
  setPaused(false)
  const url = base(port) + '/health'
  if (await probeHttp(url, 800)) return '后端已在运行：' + base(port) + '/（看护已启用）'
  const how = await startBackend(port)
  // S7：等待改成「轮询到真能服务」（15 s），不再「睡固定窗口后探测一次」。
  if (await waitHttp(url, 15000, 300))
    return '后端已启动（' + how + '）：' + base(port) + '/（看护已启用）'
  return (
    '启动失败：' +
    how +
    '（15 s 内 /health 仍不通；可先 op=restart 清残留进程，或检查 ' +
    SERVER_PATH +
    ' 与 node）'
  )
}

async function opRestart(port: number): Promise<string> {
  setPaused(false)
  killChildHandle()
  killBackendByCmd(port)
  await sleep(500)
  const how = await startBackend(port)
  if (await waitHttp(base(port) + '/health', 15000, 300))
    return '后端已重启（' + how + '）：' + base(port) + '/'
  return '重启失败：' + how + '（15 s 内 /health 仍不通）'
}

async function opStop(port: number): Promise<string> {
  setPaused(true)
  const notes: string[] = []
  if (killChildHandle()) notes.push('child-句柄已杀')
  const killed = killBackendByCmd(port)
  if (killed.length) notes.push('残留后端已杀 pid=' + killed.join(','))
  if (!notes.length) return '没有在跑的后端（127.0.0.1:' + String(port) + '）；看护已暂停'
  await sleep(400)
  return '已停止后端：' + notes.join(' · ') + '（看护已暂停，op=start 恢复）'
}

async function opLease(
  op: 'who' | 'lease' | 'release',
  args: Record<string, unknown>,
  port: number,
): Promise<string> {
  try {
    if (op === 'who') return formatWho(await backendJson(port, '/who', 15000))
    const body: Record<string, unknown> = { holder: text(args.holder) || HOLDER }
    if (args.ttl_ms) body.ttlMs = Number(args.ttl_ms)
    if (op === 'lease' && args.force) body.force = true
    if (op === 'lease' && args.scope) body.scope = text(args.scope)
    const r = await backendPost(port, op === 'lease' ? '/lease' : '/release', body, 20000)
    if (op === 'lease' && r.error === 'leased') return formatLeaseResult(op, r)
    return (op === 'lease' ? 'LEASE ' : 'RELEASE ') + JSON.stringify(r)
  } catch (e: unknown) {
    return formatLeaseError(op, e)
  }
}

async function opLaunch(args: Record<string, unknown>, port: number): Promise<string> {
  try {
    const body: Record<string, unknown> = {}
    if (args.wait_ms) body.waitMs = Number(args.wait_ms)
    if (args.file) body.file = text(args.file)
    if (args.exe) body.exe = text(args.exe)
    if (args.addon_module) body.addonModule = text(args.addon_module)
    if (args.addon_file) body.addonFile = text(args.addon_file)
    if (args.dry_run) body.dryRun = true
    const r = await backendPost(
      port,
      '/launch',
      body,
      Math.max(30000, Number(args.wait_ms || 90000) + 30000),
    )
    return formatLaunch(r)
  } catch (e: unknown) {
    return (
      'launch 失败：' +
      errorMessage(e) +
      '（后端没起？先 blender_viewport op=start —— 那是管**后端**进程的，本 op 才是管 Blender 的）'
    )
  }
}

async function opDoctor(port: number): Promise<string> {
  try {
    return formatDoctor(await backendJson(port, '/doctor'))
  } catch (e: unknown) {
    return '体检失败：' + errorMessage(e) + '（后端可能没起：blender_viewport op=start）'
  }
}

async function opStatus(port: number): Promise<string> {
  try {
    return formatStatus(port, await backendJson(port, '/status'))
  } catch (e: unknown) {
    return formatStatusError(port, e)
  }
}
