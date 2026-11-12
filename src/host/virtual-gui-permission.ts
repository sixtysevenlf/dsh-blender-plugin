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
import { handleDnnRpc } from './dnn-config.js'

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

/** 0/undefined/NaN 都不是可用端口：0 在这里会被误报成 gateway_unreachable。 */
function usablePort(port: number | undefined): number | null {
  return typeof port === 'number' && Number.isFinite(port) && port > 0 ? port : null
}

/** Exported for unit tests; the returned handler is the owner-only RPC surface. */
export function dispatchVirtualGuiRpc(port: number | undefined) {
  const gatewayPort = usablePort(port)
  return async (
    endpoint: string,
    payload: unknown,
    signal?: unknown,
    peer?: unknown,
  ): Promise<unknown> => {
    if (endpoint === 'virtual-gui')
      return handleVirtualGuiPanel(payload, peer, gatewayPort ?? 0, virtualGuiPermissionView())
    // DNN 配置端点（apply/status/cancel-pending/rollback/download/probe）：
    // 与 virtual-gui 复用同一个 owner-only 通道，不新增 HTTP 路由。
    if (endpoint === 'dnn') {
      if (gatewayPort === null) {
        // 插件没拿到端口时不能伪装成“网关不可达”：那是两种不同的修复路径。
        return {
          ok: false,
          error: {
            code: 'gateway_not_configured',
            message: '插件未配置 Gateway 端口（可能尚未完成 apply 或插件重载中）；重启插件后重试',
          },
        }
      }
      return handleDnnRpc(payload, peer, gatewayPort)
    }
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
