import type { IncomingMessage, ServerResponse } from "node:http";
import { objArg } from "./deps.js";
import type { HttpRouteDeps } from "./deps.js";
import { errorMessage, errorField } from "../../core/json.js";
// HTTP 路由 /act：Python 执行（T7）
export function createActRoutes(E: HttpRouteDeps) {
  const { engine, json, leaseGate, pushRtt, readBody, stats } = E;
  return {
    async act(req: IncomingMessage, res: ServerResponse, _url: URL, _p: string): Promise<void> {
      const raw = await readBody(req);
      let payload: Record<string, unknown> = {};
      try { payload = JSON.parse(raw); } catch (e) { payload = { code: raw }; }
      const gate = leaseGate(payload);
      if (gate) { json(res, 409, gate); return; }
      const t0 = Date.now();
      try {
        const out = objArg(await engine.act(payload.code || '', Number(payload.timeoutMs) || 120000, payload.file || null));
        stats.acts++; stats.lastActMs = Date.now() - t0; pushRtt('act', stats.lastActMs); stats.lastError = null;
        const o = out || {};
        json(res, 200, { ok: o.ok !== false, ms: o.ms != null ? o.ms : (Date.now() - t0),
                         mainThreadMs: o.mainThreadMs != null ? o.mainThreadMs : null,
                         executed: !!o.executed, stdout: String(o.stdout || ""), stderr: String(o.stderr || ""),
                         error: o.error || null, traceback: o.traceback || null,
                         file: o.file || null, marker_missing: !!o.marker_missing,
                         sceneEpoch: o.sceneEpoch || null,
                         errorKind: o.error ? 'execution' : null });
      } catch (e) {
        stats.acts++; stats.lastError = errorMessage(e);
        json(res, 200, { ok: false, ms: Date.now() - t0, error: stats.lastError,
                         errorKind: errorField(e, 'kind') || null, diagnosis: errorField(e, 'diagnosis') || null });
      }
      return;
    },
  };
}
