// HTTP 路由 /opt：优化（T7）
export function createOptRoutes(E) {
  const { engine, isReadOnly, json, leaseGate, readBody, stats } = E;
  return {
    async opt(req, res, url, p) {
      const raw = await readBody(req);
      let payload = {};
      try { payload = JSON.parse(raw); } catch (e) { payload = {}; }
      const op = String(payload.op || 'opt_analyze');
      const gate = isReadOnly('/opt', op) ? null : leaseGate(payload);
      if (gate) { json(res, 409, gate); return; }
      stats.opt = (stats.opt || 0) + 1;
      try {
        const out = await engine.opt(op, payload.args);
        json(res, 200, { ok: true, op: op, result: out });
      } catch (e) {
        json(res, 200, { ok: false, op: op, error: String((e && e.message) || e), diagnosis: (e && e.diagnosis) || null });
      }
      return;
    },
  };
}
