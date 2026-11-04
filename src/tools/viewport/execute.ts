/** blender_viewport 的执行层：op 分发 + 每个 op 一个阶段函数（行为与 v2.6.2 一致）。 */
import {
  HOLDER,
  SERVER_PATH,
  VIEWPORT_DOCTOR_HEALTH_TIMEOUT_MS,
  VIEWPORT_START_STATUS_TIMEOUT_MS,
} from '../../constants.js'
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
  if (await probeHttp(url, 800)) return formatBackendUp(port)
  const how = await startBackend(port)
  // S7：等待改成「轮询到真能服务」（15 s），不再「睡固定窗口后探测一次」。
  if (await waitHttp(url, 15000, 300)) return formatBackendUp(port, how)
  return (
    '启动失败：' +
    how +
    '（15 s 内 /health 仍不通；可先 op=restart 清残留进程，或检查 ' +
    SERVER_PATH +
    ' 与 node）'
  )
}

/**
 * P0-3：/health 通只证明 9877 后端活着，**不证明** Blender/addon/bpy 可用。
 * 再用有界 /status 看 bpy 往返，把「后端在 + addon/bpy 可用」与「后端在、addon 没响应」分开说。
 */
async function formatBackendUp(port: number, how?: string): Promise<string> {
  const endpoint = base(port) + '/'
  const addonDown = (detail: string): string =>
    '后端在（9877）' +
    endpoint +
    '：addon/bpy 未响应：' +
    detail +
    ' → 检查 Blender 是否运行且「DSH Blender Bridge」已 Connect（op=start 只管后端进程，不修这条通道；op=doctor 复核）'
  let status: Record<string, unknown>
  try {
    status = await backendJson(port, '/status', VIEWPORT_START_STATUS_TIMEOUT_MS)
  } catch (e: unknown) {
    return addonDown(errorMessage(e))
  }
  if (status.addonError) return addonDown(text(status.addonError))
  return (
    (how ? '后端已启动（' + how + '）' : '后端已在运行') +
    '：' +
    endpoint +
    '（看护已启用）· addon/bpy 可用（/status bpy 往返通）'
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
    const err = errorMessage(e)
    // P0-3：只有 /health 也不通才能说「后端没起」；后端在就是 addon/bpy 往返的问题。
    const backendUp = await probeHttp(base(port) + '/health', VIEWPORT_DOCTOR_HEALTH_TIMEOUT_MS)
    if (backendUp)
      return (
        '体检失败：' +
        err +
        '（后端在（9877），doctor 超时/失败：问题在 addon 或 bpy 往返 —— ' +
        '检查 Blender 是否运行且「DSH Blender Bridge」已 Connect；op=start 只管后端进程，不修这条通道；op=doctor 稍后重试）'
      )
    return '体检失败：' + err + '（后端没起：blender_viewport op=start）'
  }
}

async function opStatus(port: number): Promise<string> {
  try {
    return formatStatus(port, await backendJson(port, '/status'))
  } catch (e: unknown) {
    return formatStatusError(port, e)
  }
}
