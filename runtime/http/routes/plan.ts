import type { IncomingMessage, ServerResponse } from 'node:http'
import type { HttpRouteDeps } from './deps.js'
import { errorMessage, errorField } from '../../core/json.js'
import { PLAN_SESSION_READ_OPS } from '../../catalog/index.js'
// HTTP 路由 /plan：契约层/规划器/catalog
export function createPlanRoutes(E: HttpRouteDeps) {
  const { engine, isReadOnly, json, leaseGate, pushRtt, readBody, stats } = E
  return {
    async plan(req: IncomingMessage, res: ServerResponse, _url: URL, _p: string): Promise<void> {
      // 契约层与规划器统一入口：op + args
      const raw = await readBody(req)
      let payload: Record<string, unknown>
      try {
        payload = JSON.parse(raw)
      } catch (e) {
        payload = {}
      }
      const op = String(payload.op || 'status')
      // Catalog reads skip the gate. Session-read ops (animation_report/help)
      // need a visible/owned session but never a write grant, so they take the
      // read gate; everything else keeps the write gate.
      const readOnly = isReadOnly('/plan', op)
      const sessionRead = !readOnly && PLAN_SESSION_READ_OPS.includes(op)
      const gate = readOnly
        ? null
        : leaseGate(payload, sessionRead ? { readOnly: true } : undefined)
      if (gate) {
        json(res, 409, gate)
        return
      }
      stats.plan = (stats.plan || 0) + 1
      const t0p = Date.now()
      try {
        const out2 = await engine.plan(op, payload.args || {})
        stats.lastPlanMs = Date.now() - t0p
        pushRtt('plan', stats.lastPlanMs)
        json(res, 200, { ok: true, op: op, result: out2 })
      } catch (e) {
        stats.lastError = errorMessage(e)
        json(res, 200, {
          ok: false,
          op: op,
          error: stats.lastError,
          diagnosis: errorField(e, 'diagnosis') || null,
        })
      }
      return
    },
  }
}
