import type { IncomingMessage, ServerResponse } from "node:http";
import type { HttpRouteDeps } from "./deps.js";
import { errorMessage, errorField } from "../../core/json.js";
// HTTP 路由 /plan：契约层/规划器/catalog（T7）
export function createPlanRoutes(E: HttpRouteDeps) {
  const { engine, isReadOnly, json, leaseGate, pushRtt, readBody, stats } = E;
  return {
    async plan(req: IncomingMessage, res: ServerResponse, _url: URL, _p: string): Promise<void> {
      // 契约层（S1+S2）与规划器（S3）统一入口：op + args
      const raw = await readBody(req);
      let payload: Record<string, unknown> = {};
      try { payload = JSON.parse(raw); } catch (e) { payload = {}; }
      const op = String(payload.op || 'status');
      const gate = isReadOnly('/plan', op) ? null : leaseGate(payload);
      if (gate) { json(res, 409, gate); return; }
      stats.plan = (stats.plan || 0) + 1;
      const t0p = Date.now();
      try {
        const out2 = await engine.plan(op, payload.args || {});
        stats.lastPlanMs = Date.now() - t0p; pushRtt('plan', stats.lastPlanMs);
        json(res, 200, { ok: true, op: op, result: out2 });
      } catch (e) {
        stats.lastError = errorMessage(e);
        json(res, 200, { ok: false, op: op, error: stats.lastError, diagnosis: errorField(e, 'diagnosis') || null });
      }
      return;
    },
  };
}
