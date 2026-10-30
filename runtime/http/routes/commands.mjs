// HTTP 路由 /commands：命令清单 / 透传（T7）
export function createCommandsRoutes(E) {
  const { engine, json, leaseGate, readBody, stats } = E;
  return {
    async commands(req, res, url, p) {
      try {
        const c = await engine.commands();
        json(res, 200, { ok: true, ...c });
      } catch (e) {
        json(res, 200, { ok: false, error: String((e && e.message) || e), diagnosis: (e && e.diagnosis) || null });
      }
      return;
    },
    async cmd(req, res, url, p) {
      const raw = await readBody(req);
      let payload = {};
      try { payload = JSON.parse(raw); } catch (e) { payload = {}; }
      const gate = leaseGate(payload);
      if (gate) { json(res, 409, gate); return; }
      const t0 = Date.now();
      stats.cmds++;
      try {
        const out = await engine.cmd(String(payload.name || ''), payload.params || {}, payload.timeoutMs || 120000);
        json(res, 200, { ok: true, ms: Date.now() - t0, result: out });
      } catch (e) {
        stats.lastError = String((e && e.message) || e);
        json(res, 200, { ok: false, ms: Date.now() - t0, error: stats.lastError, diagnosis: (e && e.diagnosis) || null });
      }
      return;
    },
  };
}
