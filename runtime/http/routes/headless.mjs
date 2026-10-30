// HTTP 路由 /headless：同步无头（T7）
export function createHeadlessRoutes(E) {
  const { engine, json, leaseGate, pushRtt, readBody, stats, streamJson } = E;
  return {
    async headless(req, res, url, p) {
      const raw = await readBody(req);
      let payload = {};
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
          stats.lastError = String((e && e.message) || e);
          return { ok: false, ms: Date.now() - t0, error: stats.lastError, hint: (e && e.hint) || null, code: (e && e.code) || null };
        }
      });
      return;
    },
  };
}
