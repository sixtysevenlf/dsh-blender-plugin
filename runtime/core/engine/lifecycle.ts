// engine 生命周期：指标 / addon.send 包装
import { diagnose } from '../../transport/probes.js'
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
  'export_scene',
  'describe_node_type',
  'bpy_api_lookup',
  'drain_human_activity',
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
    '用 blender_rt_plan(op="render_wait") 等它，或等渲染完再发（长渲染建议一开始就走 blender_rt_job）'
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

export function installAddonSend(
  addon: AddonClientType,
  metrics: EngineMetrics,
  readRenderFlag: () => RenderFlagView,
): void {
  const rawSend = addon.send.bind(addon)
  addon.send = async (type: string, params: Record<string, unknown> = {}, timeoutMs?: number) => {
    if (MAIN_THREAD_CMDS.has(String(type))) {
      const g = readRenderFlag()
      if (g.present && !g.stale) throw renderBusyError(g, metrics)
    }
    metrics.calls++
    metrics.inflight++
    metrics.lastCmd = type
    metrics.lastCmdAt = Date.now()
    try {
      const r = await rawSend(type, params, timeoutMs)
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
      const transport = /timeout|closed|ECONN|ECONNRESET|socket/i.test(msg)
      if (transport) metrics.timeouts++
      const err = e as Error & {
        renderBusy?: boolean
        renderGuard?: unknown
        diagnosis?: unknown
        kind?: string
      }
      if (transport) {
        try {
          err.diagnosis = await diagnose()
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
