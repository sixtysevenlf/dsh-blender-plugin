import type { IncomingMessage, ServerResponse } from "node:http";
import { objArg } from "./deps.js";
import type { HttpRouteDeps } from "./deps.js";
import { errorMessage, errorField } from "../../core/json.js";
// HTTP 路由 /health：健康/状态/医生 + 已移除面板 410（T7）
export function createHealthRoutes(E: HttpRouteDeps) {
  const { PORT, describeConfig, engine, engineProvenance, json, leaseView, reqCounts, rttView, stats } = E;
  return {
    async health(req: IncomingMessage, res: ServerResponse, _url: URL, _p: string): Promise<void> {
      json(res, 200, { ok: true, stats: stats, lease: leaseView(), requests: Object.fromEntries(reqCounts),
                       // v0.9.4（P2-1）：分布 + 后端自身内存。⚠ 不含 blender.exe 的 RSS ——
                       // 它在 Windows 侧，取一次要起 tasklist（~百 ms 级），不该塞进健康探针的热路径。
                       rtt: rttView(),
                       // v0.9.6（D3）：加载版本自证（内容哈希，不是 mtime）——"要不要重启后端"在这里一眼可见。
                       // 只做本地读+哈希（~0.5 ms），不 spawn 干净进程；强对拍走 /plan op=catalog args={verify:true}。
                       provenance: (() => { try { return engineProvenance({}); } catch (e) { return { verdict: 'unknown', error: errorMessage(e) }; } })(),
                       backend: { pid: process.pid, rssMB: Math.round(process.memoryUsage().rss / 1048576),
                                  uptimeMs: Date.now() - stats.startedAt } });
      return;
    },
    async gone(req: IncomingMessage, res: ServerResponse, _url: URL, _p: string): Promise<void> {
      json(res, 410, {
        ok: false,
        error: 'gone',
        reason: '人肉视口面板（网页 UI + MJPEG 直播 + 输入注入）已移除',
        hint: '请关闭此标签页；Blender 交互改用 AI 通道：blender_rt_see / blender_rt_do / blender_rt_watch',
        removedAt: '2026-09-12',
      });
      return;
    },
    async status(req: IncomingMessage, res: ServerResponse, _url: URL, _p: string): Promise<void> {
      try {
        const s = objArg(await engine.status());
        const m = objArg(engine.metrics());
        json(res, 200, { ok: true, region: s.region, plugin: s.plugin || null, runs: s.runs || [],
                         jobs: s.jobs || [], ledger: s.ledger || null,
                         stats: stats, metrics: m, diagnosis: m.lastError && m.lastDiagnosis ? m.lastDiagnosis : null });
      } catch (e) {
        const m2 = objArg(engine.metrics());
        json(res, 200, { ok: false, error: errorMessage(e), stats: stats, metrics: m2, diagnosis: errorField(e, 'diagnosis') || m2.lastDiagnosis || null });
      }
      return;
    },
    async doctor(req: IncomingMessage, res: ServerResponse, _url: URL, _p: string): Promise<void> {
      try {
        const d = objArg(await engine.doctor());
        json(res, 200, { ok: true, ...d, config: describeConfig({ httpPort: PORT }), metrics: engine.metrics(), stats: stats });
      } catch (e) {
        json(res, 200, { ok: false, error: errorMessage(e), metrics: engine.metrics() });
      }
      return;
    },
  };
}
