import type { IncomingMessage, ServerResponse } from "node:http";
import { objArg } from "./deps.js";
import type { HttpRouteDeps } from "./deps.js";
import { errorMessage, errorField } from "../../core/json.js";
// HTTP 路由 /txn：事务（T7）
export function createTxnRoutes(E: HttpRouteDeps) {
  const { engine, isReadOnly, json, leaseGate, readBody, stats, streamJson } = E;
  return {
    async txn(req: IncomingMessage, res: ServerResponse, _url: URL, _p: string): Promise<void> {
      // 事务：snapshot / restore / list / prune / mark / revert / marks / drop / help
      const raw = await readBody(req);
      let payload: Record<string, unknown> = {};
      try { payload = JSON.parse(raw); } catch (e) { payload = {}; }
      const op = String(payload.op || 'list');
      const gate = isReadOnly('/txn', op) ? null : leaseGate(payload);
      if (gate) { json(res, 409, gate); return; }
      stats.txn = (stats.txn || 0) + 1;
      await streamJson(res, async () => {
        try {
          const r = objArg(await engine.txn(op, payload.args || {}));
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
