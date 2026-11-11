/**
 * virtual-gui 的人类许可门。
 *
 * 许可状态只存在于 DSH 宿主进程：模型工具 blender_session(start) 在这里读，
 * 浏览器侧栏 Blender Presence 面板经 DSH 认证 RPC 读写。Gateway 不保存许可，
 * virtual-gui 的 Blender 子进程即使能访问 loopback，也拿不到这个状态。
 *
 * 这是工具级/UX 门，不是对抗“已经拿到 blender_py 任意 Python”的隔离边界：
 * 那种调用方本来就能通过 /lease 绕过宿主工具面。
 */
import type { HostContext } from './context.js'
import { asRecord, isRecord, text } from '../contracts/dynamic.js'
import { handleVirtualGuiPanel, resetVirtualGuiInstall } from './virtual-gui-panel.js'

export type VirtualGuiPermissionSource = 'config' | 'runtime'

export interface VirtualGuiPermissionView {
  allowed: boolean
  source: VirtualGuiPermissionSource
  updatedAt: number | null
}

interface RpcFailure {
  ok: false
  error: { code: string; message: string; details?: Record<string, unknown> }
}

interface RpcSuccess<T = unknown> {
  ok: true
  value: T
}

interface ConnectionService {
  rpc: {
    handle: (
      channel: string,
      handler: (
        endpoint: string,
        payload: unknown,
        signal?: unknown,
        peer?: unknown,
      ) => Promise<unknown>,
    ) => unknown
  }
}

let allowed = false
let source: VirtualGuiPermissionSource = 'config'
let updatedAt: number | null = null

/** 插件配置/重载时重置默认值；运行时开关不持久化，重启回到配置默认。 */
export function configureVirtualGuiPermission(defaultAllowed: boolean): void {
  allowed = defaultAllowed === true
  source = 'config'
  updatedAt = null
  resetVirtualGuiInstall()
}

export function virtualGuiAllowed(): boolean {
  return allowed
}

export function virtualGuiPermissionView(): VirtualGuiPermissionView {
  return { allowed, source, updatedAt }
}

export function setVirtualGuiAllowed(next: boolean): VirtualGuiPermissionView {
  allowed = next === true
  source = 'runtime'
  updatedAt = Date.now()
  return virtualGuiPermissionView()
}

function failure(code: string, message: string): RpcFailure {
  return { ok: false, error: { code, message } }
}

function ownerPeer(peer: unknown): boolean {
  const principal = isRecord(peer) ? peer.principal : undefined
  return isRecord(principal) && principal.kind === 'owner'
}

/** RPC handler（导出用于单测；生产经 registerVirtualGuiPermissionRpc 挂载）。 */
export async function handleVirtualGuiPermission(
  endpoint: string,
  payload: unknown,
  _signal?: unknown,
  peer?: unknown,
): Promise<RpcFailure | RpcSuccess<VirtualGuiPermissionView>> {
  if (endpoint !== 'permission') return failure('not_found', 'unknown endpoint: ' + endpoint)
  if (!ownerPeer(peer))
    return failure('forbidden', 'virtual-gui permission is controlled by the human owner')
  const body = asRecord(payload)
  const op = text(body.op) || 'status'
  if (op === 'status') return { ok: true, value: virtualGuiPermissionView() }
  if (op !== 'set') return failure('invalid_params', 'op must be status or set')
  if (typeof body.allow !== 'boolean') return failure('invalid_params', 'allow must be a boolean')
  return { ok: true, value: setVirtualGuiAllowed(body.allow) }
}

function connectionOf(ctx: unknown): ConnectionService | null {
  const candidate = ctx as {
    connection?: unknown
    get?: (name: string) => unknown
  }
  const service =
    candidate.connection ??
    (typeof candidate.get === 'function' ? candidate.get('connection') : undefined)
  if (!isRecord(service)) return null
  const rpc = service.rpc
  if (!isRecord(rpc) || typeof rpc.handle !== 'function') return null
  return service as unknown as ConnectionService
}

function dispatchVirtualGuiRpc(port: number | undefined) {
  return async (
    endpoint: string,
    payload: unknown,
    signal?: unknown,
    peer?: unknown,
  ): Promise<unknown> => {
    if (endpoint === 'virtual-gui')
      return handleVirtualGuiPanel(payload, peer, port || 0, virtualGuiPermissionView())
    return handleVirtualGuiPermission(endpoint, payload, signal, peer)
  }
}

/** 把权限/安装端点挂到 DSH 的认证 RPC 通道；没有 connection 服务时静默跳过。 */
export function registerVirtualGuiPermissionRpc(ctx: HostContext, port?: number): void {
  if (typeof ctx.inject !== 'function') return
  ctx.inject(['connection'], (scope: HostContext) => {
    const connection = connectionOf(scope)
    if (!connection) return
    try {
      connection.rpc.handle('/dsh-blender', dispatchVirtualGuiRpc(port))
    } catch (error) {
      ctx.logger?.warn?.(
        '[dsh-blender] virtual-gui permission RPC unavailable: ' +
          text(error instanceof Error ? error.message : error),
      )
    }
  })
}
