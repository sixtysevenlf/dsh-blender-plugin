// engine 生命周期：指标 / addon.send 包装
import { gatewayInstances } from '../../transport/addon-ws.js'
import { errorMessage } from '../json.js'
import type { EngineMetrics } from './index.js'
import type { AddonClient as AddonClientType } from '../../transport/probes.js'

export interface RenderFlagView {
  present: boolean
  stale: boolean
  ageMs: number | null
  info: Record<string, unknown> | null
}

export const MAIN_THREAD_CMDS = new Set<string>([
  'execute_code',
  'get_scene_info',
  'get_object_info',
  'get_viewport_screenshot',
  'describe_node_type',
  'bpy_api_lookup',
  'get_activity_state',
  'get_selection',
  'send_selection_to_dsh',
])

export function createEngineMetrics(): EngineMetrics {
  const metrics: EngineMetrics = {
    calls: 0,
    errors: 0,
    timeouts: 0,
    inflight: 0,
    lastCmd: null,
    lastCmdAt: null,
    lastOkAt: null,
    lastError: null,
    lastDiagnosis: null,
    views: 0,
    headlessRuns: 0,
    lastHeadlessMs: null,
    workerStarts: 0,
    workerExecs: 0,
    lastErrorAt: null,
    aiPausedAt: null,
  }
  return metrics
}

function renderBusyError(
  g: RenderFlagView,
  metrics: EngineMetrics,
): Error & {
  renderBusy?: boolean
  renderGuard?: unknown
  diagnosis?: unknown
  kind?: string
} {
  metrics.errors++
  const msg =
    'BUSY_RENDER：Blender 正在渲染（' +
    String(Math.round((g.ageMs || 0) / 1000)) +
    ' s，scene=' +
    String((g.info && g.info.scene) || '?') +
    '）—— 主线程命令会排队到超时；' +
    '用 blender_op(op="render_wait") 等它，或等渲染完再发（长渲染建议一开始就走 blender_run）'
  metrics.lastError = msg
  const e = new Error(msg) as Error & {
    renderBusy?: boolean
    renderGuard?: unknown
    diagnosis?: unknown
    kind?: string
  }
  e.renderBusy = true
  e.renderGuard = g
  return e
}

/**
 * Gateway transport failures stay structured and cheap: never run the legacy
 * 9876 TCP diagnosis from a 3.1 Gateway path (it would add ~2.5s and report the
 * wrong layer). The doctor rebuilds the authoritative diagnosis from the same
 * instance registry.
 */
function gatewayTransportDiagnosis(
  addon: AddonClientType,
  e: unknown,
): Record<string, unknown> | null {
  const instances = gatewayInstances(addon)
  if (!instances) return null
  const ready = instances.filter((x) => x.ready)
  const err = errorMessage(e)
  if (ready.length === 0) {
    return {
      kind: 'addon-unreachable',
      stage: 'transport',
      connected: false,
      tcp: false,
      ready_instances: 0,
      instances: instances.map((x) => x.instanceId),
      error: err,
      summary:
        'Gateway 在，但没有 Blender addon 连接到 Gateway —— Blender 未启动、扩展未启用/未连接，或仍在握手。',
      fix: '启动 Blender 4.2.10 LTS，在 3D 视图 N 面板启用「DSH Blender Bridge」2.0.0（protocol 12）；确认扩展显示已连接 Gateway。纯渲染/批处理可改用 blender_run。',
      hint: 'Gateway /health 只证明 Gateway 进程存活，不代表 extension/bpy 可用。',
    }
  }
  return {
    kind: 'addon-transport-error',
    stage: 'transport',
    connected: true,
    tcp: true,
    ready_instances: ready.length,
    instances: ready.map((x) => x.instanceId),
    error: err,
    summary: 'Gateway 在、addon 控制连接在，但这次请求在传输层失败：' + err,
    fix: '用 blender_session(op="doctor") 复核实例状态；gateway 端口或实例重连问题先看 Gateway 日志与占用。',
    hint: '不要把它归成主线程忙；传输层错误来自 Gateway Addon 通道本身。',
  }
}

export function installAddonSend(
  addon: AddonClientType,
  metrics: EngineMetrics,
  readRenderFlag: () => RenderFlagView,
): void {
  const rawSend = addon.send.bind(addon)
  addon.send = async (
    type: string,
    params: Record<string, unknown> = {},
    timeoutMs?: number,
    opts?: { instanceId?: string; noMark?: boolean },
  ) => {
    if (MAIN_THREAD_CMDS.has(String(type))) {
      const g = readRenderFlag()
      if (g.present && !g.stale) throw renderBusyError(g, metrics)
    }
    metrics.calls++
    metrics.inflight++
    metrics.lastCmd = type
    metrics.lastCmdAt = Date.now()
    try {
      const r = await rawSend(type, params, timeoutMs, opts)
      metrics.lastOkAt = Date.now()
      metrics.lastDiagnosis = null
      metrics.aiPausedAt = null
      return r
    } catch (e) {
      const msg = errorMessage(e)
      metrics.errors++
      metrics.lastError = msg
      metrics.lastErrorAt = Date.now()
      if (/AI_PAUSED|AI 操作已暂停/.test(msg)) metrics.aiPausedAt = metrics.lastErrorAt
      const transport =
        /timeout|closed|ECONN|ECONNRESET|socket|no_addon|addon_write_failed|instance_not_found|instance_disconnected|没有 Blender addon|没有可路由/i.test(
          msg,
        )
      if (transport) metrics.timeouts++
      const err = e as Error & {
        renderBusy?: boolean
        renderGuard?: unknown
        diagnosis?: unknown
        kind?: string
      }
      if (transport) {
        try {
          err.diagnosis = gatewayTransportDiagnosis(addon, e)
          metrics.lastDiagnosis = err.diagnosis
        } catch (x) {
          /* 诊断自身失败不影响原错误 */
        }
      } else {
        err.kind = 'execution'
      }
      throw err
    } finally {
      metrics.inflight--
    }
  }
}
