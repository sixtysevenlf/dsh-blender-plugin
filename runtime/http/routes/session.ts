import type { IncomingMessage, ServerResponse } from "node:http";
import { objArg } from "./deps.js";
import type { HttpRouteDeps } from "./deps.js";
import { errorMessage } from "../../core/json.js";
// HTTP 路由 /session：launch / who / lease / release（T7）
export function createSessionRoutes(E: HttpRouteDeps) {
  const { LEASE, PORT, describeConfig, engine, json, leaseAcquire, leaseView, readBody, releaseStaleLease, reqCounts, stats } = E;
  return {
    async launch(req: IncomingMessage, res: ServerResponse, _url: URL, _p: string): Promise<void> {
      const raw = await readBody(req);
      let payload: Record<string, unknown> = {};
      try { payload = JSON.parse(raw); } catch (e) { payload = {}; }
      const t0 = Date.now();
      try {
        const out = objArg(await engine.launchBlender(payload));
        stats.launches = (stats.launches || 0) + 1;
        stats.lastLaunchMs = Date.now() - t0;
        const doc = out.ok && (out.launched || out.already) ? await engine.doctor().catch(() => null) : null;
        json(res, out.ok ? 200 : 200, Object.assign({ ok: !!out.ok, ms: Date.now() - t0, launches: stats.launches }, out, { doctor: doc }));
      } catch (e) {
        json(res, 500, { ok: false, ms: Date.now() - t0, error: errorMessage(e), hint: '看后端日志；也可用 exe/path 显式指定 blender.exe' });
      }
      return;
    },
    async who(req: IncomingMessage, res: ServerResponse, _url: URL, _p: string): Promise<void> {
      json(res, 200, { ok: true, lease: leaseView(), metrics: engine.metrics(), stats: stats, config: describeConfig({ httpPort: PORT }), requests: Object.fromEntries(reqCounts) });
      return;
    },
    async lease(req: IncomingMessage, res: ServerResponse, _url: URL, _p: string): Promise<void> {
      const raw = await readBody(req);
      let payload: Record<string, unknown> = {};
      try { payload = JSON.parse(raw); } catch (e) { payload = {}; }
      const holder = String(payload.holder || 'anonymous');
      releaseStaleLease('op=lease 到达时探活发现持有者进程不存在');   // v0.9.4
      if (payload.renewOnly) {
        // 心跳：只有本来就持有租约时才续期 —— 不在空闲时抢占，避免多会话互相锁死
        const v = leaseView();
        if (v.active && v.holder === holder) {
          const rr = leaseAcquire(holder, payload.ttlMs, false, payload.scope);
          json(res, 200, { ok: true, lease: 'lease' in rr ? rr.lease : null, renewed: true });
        } else {
          json(res, 200, { ok: true, lease: v, renewed: false });
        }
        return;
      }
      const r = leaseAcquire(holder, payload.ttlMs, !!payload.force, payload.scope);
      if ('denied' in r) {
        const otherScope = leaseView().scope || null;
        json(res, 409, { ok: false, error: 'leased', holder: r.holder, expiresInMs: r.expiresInMs, holderScope: otherScope,
          hint: '另一个会话持有写权限租约；force:true 可抢占，或等 ' + Math.round(r.expiresInMs / 1000) + 's 后重试' });
        return;
      }
      json(res, 200, { ok: true, lease: 'lease' in r ? r.lease : null, renewed: !!payload.renewOnly });
      return;
    },
    async release(req: IncomingMessage, res: ServerResponse, _url: URL, _p: string): Promise<void> {
      const raw = await readBody(req);
      let payload: Record<string, unknown> = {};
      try { payload = JSON.parse(raw); } catch (e) { payload = {}; }
      const want = String(payload.holder || '');
      let released: string | null = null;
      if (LEASE.holder && (!want || LEASE.holder === want)) {
        released = LEASE.holder;
        LEASE.holder = null; LEASE.token = null; LEASE.expiresAt = null; LEASE.acquiredAt = null;
      }
      json(res, 200, { ok: true, released: released, lease: leaseView() });
      return;
    },
  };
}
