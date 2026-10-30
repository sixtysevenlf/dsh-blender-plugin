import type { IncomingMessage, ServerResponse } from "node:http";
import type { HttpRouteDeps } from "./deps.js";
import { errorMessage } from "../../core/json.js";
// HTTP 路由 /job：作业层（T7）
export function createJobRoutes(E: HttpRouteDeps) {
  const { engine, isReadOnly, json, leaseGate, pushRtt, readBody, stats, streamJson } = E;
  return {
    async job(req: IncomingMessage, res: ServerResponse, _url: URL, _p: string): Promise<void> {
      // 作业层：start / status / collect / kill / list（长任务后台化，渲染一整晚）
      const raw = await readBody(req);
      let payload: Record<string, unknown> = {};
      try { payload = JSON.parse(raw); } catch (e) { payload = {}; }
      const op = String(payload.op || 'list');
      const gate = isReadOnly('/job', op) ? null : leaseGate(payload);
      if (gate) { json(res, 409, gate); return; }
      stats.job = (stats.job || 0) + 1;
      // v0.9.3（D6.2）：op=wait 会阻塞到终态/超时 → 走流式回执（每 15 s 一行心跳，客户端不会被判超时）
      if (op === 'wait') {
        await streamJson(res, async () => {
          try {
            const j = await engine.job.wait(payload.id, Number(payload.timeoutMs) || Number(payload.timeout_ms) || 120000);
            return { ok: true, op: op, job: j };
          } catch (e) {
            stats.lastError = errorMessage(e);
            return { ok: false, op: op, error: stats.lastError };
          }
        });
        return;
      }
      const t0j = Date.now();      // v0.9.4（P2-1）：作业路由耗时进 rtt 分布（P2-2 那种"偶发卡住"要看出趋势）
      try {
        if (op === 'start') { json(res, 200, { ok: true, op: op, job: engine.job.start(payload) }); return; }
        if (op === 'kill') { json(res, 200, { ok: true, op: op, job: engine.job.kill(payload.id) }); return; }
        if (op === 'collect') { json(res, 200, { ok: true, op: op, job: engine.job.collect(payload.id, Number(payload.tail) || 4000) }); return; }
        if (op === 'status') { json(res, 200, { ok: true, op: op, job: engine.job.status(payload.id) }); return; }
        json(res, 200, { ok: true, op: 'list', jobs: engine.job.list() });
      } catch (e) {
        stats.lastError = errorMessage(e);
        json(res, 200, { ok: false, op: op, error: stats.lastError });
      } finally {
        pushRtt('job', Date.now() - t0j);
      }
      return;
    },
  };
}
