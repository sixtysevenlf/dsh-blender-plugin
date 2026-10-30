import type { IncomingMessage, ServerResponse } from "node:http";
import type { HttpRouteDeps } from "./deps.js";
import { errorMessage, errorField } from "../../core/json.js";
// HTTP 路由 /perf：性能诊断（T7）
export function createPerfRoutes(E: HttpRouteDeps) {
  const { engine, isReadOnly, json, leaseGate, readBody, stats } = E;
  return {
    async perf(req: IncomingMessage, res: ServerResponse, _url: URL, _p: string): Promise<void> {
      const raw = await readBody(req);
      let payload: Record<string, unknown> = {};
      try { payload = JSON.parse(raw); } catch (e) { payload = {}; }
      const op = String(payload.op || 'status');
      const gate = isReadOnly('/perf', op) ? null : leaseGate(payload);
      if (gate) { json(res, 409, gate); return; }
      stats.perf = (stats.perf || 0) + 1;
      try {
        const out = await engine.perf(op, payload.args);
        json(res, 200, { ok: true, op: op, result: out });
      } catch (e) {
        json(res, 200, { ok: false, op: op, error: errorMessage(e), diagnosis: errorField(e, 'diagnosis') || null });
      }
      return;
    },
  };
}
