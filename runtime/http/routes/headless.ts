import type { IncomingMessage, ServerResponse } from "node:http";
import type { HttpRouteDeps } from "./deps.js";
import { errorMessage, errorCode, errorField } from "../../core/json.js";
// HTTP 路由 /headless：同步无头（T7）
export function createHeadlessRoutes(E: HttpRouteDeps) {
  const { engine, json, leaseGate, pushRtt, readBody, stats, streamJson } = E;
  return {
    async headless(req: IncomingMessage, res: ServerResponse, _url: URL, _p: string): Promise<void> {
      const raw = await readBody(req);
      let payload: Record<string, unknown> = {};
      try { payload = JSON.parse(raw); } catch (e) { payload = {}; }
      const gate = leaseGate(payload);
      if (gate) { json(res, 409, gate); return; }
      const t0 = Date.now();
      stats.headless++;
      await streamJson(res, async () => {
        try {
          const out = await engine.headless(payload);
          stats.lastHeadlessMs = Date.now() - t0; pushRtt('headless', stats.lastHeadlessMs); stats.lastError = null;
          return { ok: !!out.ok, ms: out.ms, result: out };
        } catch (e) {
          stats.lastError = errorMessage(e);
          return { ok: false, ms: Date.now() - t0, error: stats.lastError, hint: errorField(e, 'hint') || null, code: errorCode(e) || null };
        }
      });
      return;
    },
  };
}
