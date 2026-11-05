// engine API：指标
import type { EngineApiDeps, EngineApiMetrics } from './index.js'

export function metricsView(E: EngineApiDeps): Record<string, unknown> {
  const metrics: EngineApiMetrics = E.metrics
  return { ...metrics, lastCmdAgeMs: metrics.lastCmdAt ? Date.now() - metrics.lastCmdAt : null }
}

export function createMetricsApi(E: EngineApiDeps) {
  const { metrics } = E
  return {
    metrics() {
      return { ...metrics, lastCmdAgeMs: metrics.lastCmdAt ? Date.now() - metrics.lastCmdAt : null }
    },
    /** 通道实际用的 addon 协议（显式配置 or auto 探测结果） */
  }
}
