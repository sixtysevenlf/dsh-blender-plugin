// Gateway process lifecycle: fail closed on port conflict, single owner state
// file, bounded shutdown. This replaces the 3.0.x reclaim-and-steal listener.
import fs from 'node:fs'
import type { GatewayApp } from './app.js'
import { gatewayHostId, readGatewayOwner, removeGatewayOwner, writeGatewayOwner } from './owner.js'
import type { GatewayOwner } from './owner.js'
import { portListenerPids } from '../ports.js'
import { tasklistImageName } from '../win_proc.js'

export interface GatewayLifecycleOptions {
  /** Bind process signal handlers; tests/assets can run without owning process exit. */
  handleSignals?: boolean
}

export interface GatewayStartResult {
  ok: boolean
  port: number
  owner?: GatewayOwner
  error?: Record<string, unknown>
}

interface PortProbe {
  port: number
  pid: number | null
  process: string | null
}

function processName(pid: number | null): string | null {
  if (!pid) return null
  if (process.platform === 'win32') {
    const name = tasklistImageName(pid)
    return name || null
  }
  try {
    const comm = fs.readFileSync('/proc/' + String(pid) + '/comm', 'utf8').trim()
    if (comm) return comm
  } catch (e) {
    /* fall through */
  }
  return null
}

function ownerProbe(port: number): PortProbe {
  const pids = portListenerPids(port)
  const pid = pids.length ? pids[0] : null
  return { port, pid, process: processName(pid) }
}

function conflictError(
  app: GatewayApp,
  existing: GatewayOwner | null,
  probe: PortProbe,
): Record<string, unknown> {
  const currentHost = gatewayHostId()
  if (existing && existing.hostId !== currentHost) {
    return {
      stage: 'config',
      code: 'GATEWAY_IN_USE',
      port: app.port,
      root_cause:
        'Gateway 端口 ' +
        String(app.port) +
        ' 属于另一个 DSH host（hostId=' +
        String(existing.hostId) +
        '，pid=' +
        String(existing.pid) +
        '）；3.1 每个 host 一个 Gateway，不共享、不 attach、不复用。',
      fix: '停掉另一个 DSH host 的 Gateway，或为当前 host 显式设置另一个 DSH_BLENDER_GATEWAY_PORT。',
      fallback: 'Gateway 未启动；不会把请求路由到既有 Gateway。',
      evidence: { port: app.port, owner: existing, probe },
    }
  }
  return {
    stage: 'config',
    code: 'port_conflict',
    port: app.port,
    root_cause: 'Gateway 端口 ' + String(app.port) + ' 已被占用；3.1 不共享端口、不夺回。',
    fix:
      '停掉占用进程，或显式设置 DSH_BLENDER_GATEWAY_PORT 为另一个 loopback 端口。' +
      '占用者：' +
      JSON.stringify(probe) +
      (existing ? '；同 host 的旧 Gateway 仍在运行，插件重载必须先停止旧 Gateway。' : ''),
    fallback: 'Gateway 未启动；Blender 保持未连接，不会自动降级到旧拓扑。',
    evidence: { port: app.port, owner: existing, ownerFile: existing !== null, probe },
  }
}

function ownerOf(app: GatewayApp): GatewayOwner {
  return (
    app.owner ?? {
      gatewayId: app.addonGateway.gatewayId,
      hostId: gatewayHostId(),
      pid: process.pid,
      endpoint: 'ws://' + app.host + ':' + String(app.port) + '/addon',
      startedAt: Date.now(),
    }
  )
}

/** Remove this Gateway's diagnostic owner file and close its server. */
export function stopGateway(app: GatewayApp): void {
  removeGatewayOwner(app.port)
  app.close()
}

/** Listen on the configured loopback port. Never steals or shares an occupied port. */
export async function startGateway(
  app: GatewayApp,
  opts: GatewayLifecycleOptions = {},
): Promise<GatewayStartResult> {
  // Explicit ownership preflight: do not wait for the bind error to discover an
  // unrelated listener, and never reclaim another host's Gateway.
  if (app.port > 0) {
    const existing = readGatewayOwner(app.port)
    const probe = ownerProbe(app.port)
    if (probe.pid !== null) {
      return { ok: false, port: app.port, error: conflictError(app, existing, probe) }
    }
  }
  const owner = ownerOf(app)
  const ownerWithPort: GatewayOwner = { ...owner, port: app.port }
  return new Promise<GatewayStartResult>((resolve) => {
    let settled = false
    const fail = (error: Record<string, unknown>): void => {
      if (settled) return
      settled = true
      resolve({ ok: false, port: app.port, error })
    }
    app.server.once('error', (e: NodeJS.ErrnoException) => {
      const code = String(e && e.code ? e.code : '')
      if (code === 'EADDRINUSE') {
        fail(conflictError(app, readGatewayOwner(app.port), ownerProbe(app.port)))
        return
      }
      fail({
        stage: 'spawn',
        code: 'gateway_listen_failed',
        port: app.port,
        root_cause: 'Gateway 监听失败：' + String(e && e.message ? e.message : e),
        fix: '检查端口权限与网络配置后重启插件。',
        fallback: 'Gateway 未启动。',
      })
    })
    app.server.listen(app.port, app.host, () => {
      if (settled) return
      settled = true
      const address = app.server.address()
      if (address && typeof address === 'object' && address.port) app.port = address.port
      const actualOwner: GatewayOwner = {
        ...ownerWithPort,
        port: app.port,
        endpoint: 'ws://' + app.host + ':' + String(app.port) + '/addon',
      }
      app.owner = actualOwner
      writeGatewayOwner(actualOwner)
      if (opts.handleSignals !== false) {
        const shutdown = (): void => {
          stopGateway(app)
          setTimeout(() => process.exit(0), 200)
        }
        process.once('SIGINT', shutdown)
        process.once('SIGTERM', shutdown)
      }
      resolve({ ok: true, port: app.port, owner: actualOwner })
    })
  })
}
