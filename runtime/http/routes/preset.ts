import type { IncomingMessage, ServerResponse } from "node:http";
import { objArg } from "./deps.js";
import type { HttpRouteDeps } from "./deps.js";
import { errorMessage, errorField } from "../../core/json.js";
// HTTP 路由 /preset：配方库（T7）
export function createPresetRoutes(E: HttpRouteDeps) {
  const { engine, isReadOnly, json, leaseGate, readBody, stats, streamJson } = E;
  return {
    async preset(req: IncomingMessage, res: ServerResponse, _url: URL, _p: string): Promise<void> {
      // 配方库：save / list / get / delete / apply / export / import / help
      const raw = await readBody(req);
      let payload: Record<string, unknown> = {};
      try { payload = JSON.parse(raw); } catch (e) { payload = {}; }
      const op = String(payload.op || 'list');
      const gate = isReadOnly('/preset', op) ? null : leaseGate(payload);
      if (gate) { json(res, 409, gate); return; }
      stats.preset = (stats.preset || 0) + 1;
      await streamJson(res, async () => {
        try {
          const r = objArg(await engine.preset(op, payload.args || {}));
          return { ok: !!(r && r.ok !== false), op: op, result: r };
        } catch (e) {
          stats.lastError = errorMessage(e);
          return { ok: false, op: op, error: stats.lastError, diagnosis: errorField(e, 'diagnosis') || null };
        }
      });
      return;
    },
  };
}
