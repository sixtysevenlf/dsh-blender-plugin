// /host 路由处理器
import type { IncomingMessage, ServerResponse } from 'node:http'
import { stats } from '../config.js'
import { hostCorsHeaders, readBody } from '../response.js'
import { trimJob, clampIntParam } from '../jobs.js'
import { handleSceneItems, hostStateView } from './scene-items.js'
import type { HostRoutesDeps } from './index.js'

async function handleHostJobs(
  E: HostRoutesDeps,
  url: URL,
  res: ServerResponse,
  origin: unknown,
): Promise<void> {
  const { engine, hostJson } = E
  const limit = clampIntParam(url.searchParams.get('limit'), 20, 1, 100)
  let all: Array<Record<string, unknown>>
  try {
    all = engine.job.list() || []
  } catch (e) {
    all = []
  }
  const trimmed = all.map(trimJob)
  // running 在前；其余按耗时倒序（契约 §8.3）
  trimmed.sort(
    (a, b) =>
      (b.status === 'running' ? 1 : 0) - (a.status === 'running' ? 1 : 0) ||
      (Number(b.ms) || 0) - (Number(a.ms) || 0),
  )
  const running = trimmed.filter((j) => j.status === 'running').length
  hostJson(
    res,
    200,
    { ok: true, jobs: trimmed.slice(0, limit), running: running, total: trimmed.length },
    origin,
  )
}

async function handleHostJobsLog(
  E: HostRoutesDeps,
  url: URL,
  res: ServerResponse,
  origin: unknown,
): Promise<void> {
  const { engine, hostJson } = E
  const id = String(url.searchParams.get('id') || '')
  const tail = clampIntParam(url.searchParams.get('tail'), 8000, 1, 200000)
  let snap: Record<string, unknown> | null
  try {
    snap = engine.job.collect(id, tail)
  } catch (e) {
    snap = null
  }
  // engine.job.collect 对不存在的 id 回 {status:'unknown', ok:false}（不是 null）⇒ 这里归一成明确的失败态
  if (!snap || snap.status === 'unknown' || snap.ok === false) {
    hostJson(
      res,
      200,
      { ok: false, id: id, error: 'unknown_job', stdout: null, stderr: null, job: null },
      origin,
    )
    return
  }
  hostJson(
    res,
    200,
    {
      ok: true,
      id: id,
      stdout: snap.stdoutTail === undefined ? null : snap.stdoutTail,
      stderr: snap.stderrTail === undefined ? null : snap.stderrTail,
      job: trimJob(snap),
    },
    origin,
  )
}

async function readJsonPayload(req: IncomingMessage): Promise<unknown> {
  const raw = await readBody(req)
  try {
    return JSON.parse(raw)
  } catch (e) {
    return null
  }
}

async function handleSelectionPost(
  E: HostRoutesDeps,
  req: IncomingMessage,
  res: ServerResponse,
  origin: unknown,
): Promise<void> {
  const { selectionStore, hostJson } = E
  const payload = await readJsonPayload(req)
  const t0 = Date.now()
  const stored = selectionStore.put(payload)
  if (!stored.ok) {
    hostJson(res, 400, { ok: false, error: stored.error || 'invalid_payload' }, origin)
    return
  }
  stats.selections = (stats.selections || 0) + 1
  stats.lastSelectionMs = Date.now() - t0
  hostJson(res, 200, stored, origin)
}

export async function handleHost(
  E: HostRoutesDeps,
  req: IncomingMessage,
  res: ServerResponse,
  url: URL,
  p: string,
): Promise<void> {
  const { selectionStore, hostJson } = E
  const origin: unknown = req.headers.origin || null
  // §1.1 CORS 预检：204 空体
  if (req.method === 'OPTIONS') {
    res.writeHead(204, hostCorsHeaders(origin))
    res.end()
    return
  }
  // §1.6 @ 提及数据源
  if (req.method === 'GET' && p === '/host/scene-items') {
    await handleSceneItems(E, url, res, origin)
    return
  }
  // §8.3 headless 作业区（只读：只读后端已有的 engine.job 台账，零 Blender 往返）
  if (req.method === 'GET' && p === '/host/jobs') {
    await handleHostJobs(E, url, res, origin)
    return
  }
  if (req.method === 'GET' && p === '/host/jobs/log') {
    await handleHostJobsLog(E, url, res, origin)
    return
  }
  // v2.6.1：通道 / 租约 / 渲染 / 作业 / AI 暂停一次给全（零 Blender 往返）
  if (req.method === 'GET' && p === '/host/state') {
    hostJson(res, 200, hostStateView(E), origin)
    return
  }
  if (req.method === 'POST' && p === '/host/selection') {
    await handleSelectionPost(E, req, res, origin)
    return
  }
  if (req.method === 'GET' && p === '/host/selection/latest') {
    hostJson(res, 200, selectionStore.snapshot(), origin)
    return
  }
  if (req.method === 'POST' && p === '/host/selection/consume') {
    const payload = await readJsonPayload(req)
    const payloadRec: Record<string, unknown> =
      payload && typeof payload === 'object' ? (payload as Record<string, unknown>) : {}
    const consumed = selectionStore.consume(payloadRec.seq, payloadRec)
    hostJson(res, consumed.ok ? 200 : 400, consumed, origin)
    return
  }
  hostJson(res, 404, { ok: false, error: 'not found', path: p }, origin)
}
