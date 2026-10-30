import type { IncomingMessage, ServerResponse } from "node:http";
import { objArg } from "./deps.js";
import type { HttpRouteDeps } from "./deps.js";
import { errorMessage, errorField } from "../../core/json.js";
// HTTP 路由 /commands：命令清单 / 透传（T7）
export function createCommandsRoutes(E: HttpRouteDeps) {
  const { engine, json, leaseGate, readBody, stats } = E;
  return {
    async commands(req: IncomingMessage, res: ServerResponse, _url: URL, _p: string): Promise<void> {
      try {
        const c = objArg(await engine.commands());
        json(res, 200, { ok: true, ...c });
      } catch (e) {
        json(res, 200, { ok: false, error: errorMessage(e), diagnosis: errorField(e, 'diagnosis') || null });
      }
      return;
    },
    async cmd(req: IncomingMessage, res: ServerResponse, _url: URL, _p: string): Promise<void> {
      const raw = await readBody(req);
      let payload: Record<string, unknown> = {};
      try { payload = JSON.parse(raw); } catch (e) { payload = {}; }
      const gate = leaseGate(payload);
      if (gate) { json(res, 409, gate); return; }
      const t0 = Date.now();
      stats.cmds++;
      try {
        const out = await engine.cmd(String(payload.name || ''), objArg(payload.params), Number(payload.timeoutMs) || 120000);
        json(res, 200, { ok: true, ms: Date.now() - t0, result: out });
      } catch (e) {
        stats.lastError = errorMessage(e);
        json(res, 200, { ok: false, ms: Date.now() - t0, error: stats.lastError, diagnosis: errorField(e, 'diagnosis') || null });
      }
      return;
    },
  };
}
