// /host 状态视图 + @ 提及数据源
import { stats } from '../config.js'
import { leaseView } from '../lease.js'
import { clampItemsLimit } from '../response.js'
import type { HostRoutesDeps } from './index.js'

/**
 * v2.6.1：GET /host/state 的响应体（纯内存，零 Blender 往返；任何一块取不到都给保守默认值，不抛）。
 * 「AI 已暂停」只能在 addon 拒掉一条命令之后才知道（后端不为它轮询 Blender），成功一次就清空。
 */
export function hostStateView(E: HostRoutesDeps): Record<string, unknown> {
  const { engine } = E
  const now = Date.now()
  const age = (t: unknown): number | null => (typeof t === 'number' && t ? now - t : null)
  let m: Record<string, unknown>
  try {
    m = (engine.metrics() || {}) as Record<string, unknown>
  } catch (e) {
    m = {}
  }
  let render: { busy: boolean; ageMs: number | null; scene: unknown } = {
    busy: false,
    ageMs: null,
    scene: null,
  }
  try {
    const g = engine.renderFlag() as {
      present?: unknown
      stale?: unknown
      ageMs?: unknown
      info?: unknown
    } | null
    if (g && g.present)
      render = {
        busy: !g.stale,
        ageMs: g.ageMs as number | null,
        scene: (g.info as Record<string, unknown> | null)?.['scene'] || null,
      }
  } catch (e) {
    /* 读不到标记 = 不在渲染 */
  }
  let running: number
  try {
    running = (engine.job.list() || []).filter((j) => j && j.status === 'running').length
  } catch (e) {
    running = 0
  }
  return {
    ok: true,
    channel: {
      lastOkAgeMs: age(m.lastOkAt),
      lastCmd: m.lastCmd || null,
      lastCmdAgeMs: age(m.lastCmdAt),
      inflight: Number(m.inflight) || 0,
      lastError: m.lastError ? String(m.lastError).slice(0, 200) : null,
      lastErrorAgeMs: age(m.lastErrorAt),
      diagnosis:
        m.lastDiagnosis && (m.lastDiagnosis as { kind?: unknown }).kind
          ? String((m.lastDiagnosis as { kind?: unknown }).kind)
          : null,
      aiPaused: !!m.aiPausedAt,
    },
    lease: leaseView(),
    render: render,
    jobs: { running: running },
    backend: { pid: process.pid, uptimeMs: now - stats.startedAt },
  }
}

/** 契约 §1.6 @ 提及数据源（GET /host/scene-items） */
export async function handleSceneItems(
  E: HostRoutesDeps,
  url: URL,
  res: import('node:http').ServerResponse,
  origin: unknown,
): Promise<void> {
  const { engine, hostJson, hostErrText } = E
  let out: Record<string, unknown>
  try {
    out = (await engine.hostSceneItems({
      q: url.searchParams.get('q') || '',
      limit: clampItemsLimit(url.searchParams.get('limit')),
    })) as Record<string, unknown>
  } catch (e) {
    stats.lastError = hostErrText(e)
    hostJson(res, 502, { ok: false, error: hostErrText(e) }, origin)
    return
  }
  hostJson(
    res,
    200,
    {
      ok: true,
      objects: out.objects || [],
      materials: out.materials || [],
      collections: out.collections || [],
    },
    origin,
  )
}
