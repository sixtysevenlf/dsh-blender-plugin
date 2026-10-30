import type { IncomingMessage, ServerResponse } from "node:http";
import type { HttpRouteDeps } from "./deps.js";
import { errorMessage, errorField } from "../../core/json.js";
// HTTP 路由 /loop：内环（T7）
export function createLoopRoutes(E: HttpRouteDeps) {
  const { engine, isReadOnly, json, leaseGate, readBody, stats } = E;
  return {
    async loop(req: IncomingMessage, res: ServerResponse, _url: URL, _p: string): Promise<void> {
      const raw = await readBody(req);
      let payload: Record<string, unknown> = {};
      try { payload = JSON.parse(raw); } catch (e) { payload = {}; }
      const op = String(payload.op || 'status');
      const gate = isReadOnly('/loop', op) ? null : leaseGate(payload);
      if (gate) { json(res, 409, gate); return; }
      stats.loops++;
      try {
        let out: unknown;
        if (op === 'start') out = await engine.loopStart(payload.spec || {});
        else if (op === 'stop') out = await engine.loopStop();
        else if (op === 'bench') out = await engine.loopBench(Number(payload.iterations) || 2000);
        else if (op === 'board') out = await engine.loopBoard(Number(payload.limit) || 10, payload.groups !== false);
        else if (op === 'export') out = await engine.loopExport({ path: payload.path, top: payload.top, includeVariants: !!payload.include_variants, note: payload.note });
        else if (op === 'help') out = await engine.loopHelp();
        else out = await engine.loopStatus(Number(payload.history) || 8);
        json(res, 200, { ok: true, op: op, result: out });
      } catch (e) {
        json(res, 200, { ok: false, op: op, error: errorMessage(e), diagnosis: errorField(e, 'diagnosis') || null });
      }
      return;
    },
  };
}
