// HTTP 路由 /perf：性能诊断（T7）
export function createPerfRoutes(E) {
  const { engine, isReadOnly, json, leaseGate, readBody, stats } = E;
  return {
    async perf(req, res, url, p) {
      const raw = await readBody(req);
      let payload = {};
      try { payload = JSON.parse(raw); } catch (e) { payload = {}; }
      const op = String(payload.op || 'status');
      const gate = isReadOnly('/perf', op) ? null : leaseGate(payload);
      if (gate) { json(res, 409, gate); return; }
      stats.perf = (stats.perf || 0) + 1;
      try {
        const out = await engine.perf(op, payload.args);
        json(res, 200, { ok: true, op: op, result: out });
      } catch (e) {
        json(res, 200, { ok: false, op: op, error: String((e && e.message) || e), diagnosis: (e && e.diagnosis) || null });
      }
      return;
    },
  };
}
