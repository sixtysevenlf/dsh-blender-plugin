import type { IncomingMessage, ServerResponse } from 'node:http'
import type { HttpRouteDeps } from './deps.js'
import { errorCode, errorMessage, errorField } from '../../core/json.js'
import { JobSurface, callerFromHeaders } from '../../core/queue/handler.js'

/** The internal queue surface lives behind the same /job route until section 7 exposes blender_run. */
async function handleQueueSurface(
  E: HttpRouteDeps,
  req: IncomingMessage,
  res: ServerResponse,
  payload: Record<string, unknown>,
  op: string,
): Promise<boolean> {
  if (payload.surface !== 'queue') return false
  const surface = new JobSurface(E.jobQueue)
  const caller = callerFromHeaders(req.headers)
  E.stats.job = (E.stats.job || 0) + 1
  if (op === 'wait') await E.streamJson(res, () => surface.handle(caller, payload))
  else E.json(res, 200, await surface.handle(caller, payload))
  return true
}

// HTTP 路由 /job：作业层
export function createJobRoutes(E: HttpRouteDeps) {
  const { engine, isReadOnly, json, leaseGate, pushRtt, readBody, stats, streamJson } = E
  return {
    async job(req: IncomingMessage, res: ServerResponse, _url: URL, _p: string): Promise<void> {
      // 作业层：start / status / collect / kill / list（长任务后台化，渲染一整晚）
      const raw = await readBody(req)
      let payload: Record<string, unknown> = {}
      try {
        payload = JSON.parse(raw)
      } catch (e) {
        payload = {}
      }
      const op = String(payload.op || 'list')
      if (await handleQueueSurface(E, req, res, payload, op)) return
      const gate = isReadOnly('/job', op) ? null : leaseGate(payload)
      if (gate) {
        json(res, 409, gate)
        return
      }
      stats.job = (stats.job || 0) + 1
      // v0.9.3：op=wait 会阻塞到终态/超时 → 走流式回执（每 15 s 一行心跳，客户端不会被判超时）
      if (op === 'wait') {
        await streamJson(res, async () => {
          try {
            const j = await engine.job.wait(
              payload.id,
              Number(payload.timeoutMs) || Number(payload.timeout_ms) || 120000,
            )
            return { ok: true, op: op, job: j }
          } catch (e) {
            stats.lastError = errorMessage(e)
            return {
              ok: false,
              op: op,
              error: stats.lastError,
              code: errorField(e, 'code') || errorCode(e) || null,
            }
          }
        })
        return
      }
      const t0j = Date.now() // v0.9.4：作业路由耗时进 rtt 分布（那种"偶发卡住"要看出趋势）
      try {
        if (op === 'start') {
          json(res, 200, { ok: true, op: op, job: engine.job.start(payload) })
          return
        }
        if (op === 'kill') {
          json(res, 200, { ok: true, op: op, job: engine.job.kill(payload.id) })
          return
        }
        if (op === 'collect') {
          json(res, 200, {
            ok: true,
            op: op,
            job: engine.job.collect(payload.id, Number(payload.tail) || 4000),
          })
          return
        }
        if (op === 'status') {
          json(res, 200, { ok: true, op: op, job: engine.job.status(payload.id) })
          return
        }
        json(res, 200, { ok: true, op: 'list', jobs: engine.job.list() })
      } catch (e) {
        stats.lastError = errorMessage(e)
        // 把 blender_exe_missing 等结构化 code 透出，工具层才能给配置指引而不是泛化 backend_error。
        json(res, 200, {
          ok: false,
          op: op,
          error: stats.lastError,
          code: errorField(e, 'code') || errorCode(e) || null,
        })
      } finally {
        pushRtt('job', Date.now() - t0j)
      }
      return
    },
  }
}
