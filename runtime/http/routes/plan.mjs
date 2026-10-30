// HTTP 路由 /plan：契约层/规划器/catalog（T7）
export function createPlanRoutes(E) {
  const { engine, isReadOnly, json, leaseGate, pushRtt, readBody, stats } = E;
  return {
    async plan(req, res, url, p) {
      // 契约层（S1+S2）与规划器（S3）统一入口：op + args
      const raw = await readBody(req);
      let payload = {};
      try { payload = JSON.parse(raw); } catch (e) { payload = {}; }
      const op = String(payload.op || 'status');
      const gate = isReadOnly('/plan', op) ? null : leaseGate(payload);
      if (gate) { json(res, 409, gate); return; }
      stats.plan = (stats.plan || 0) + 1;
      const t0p = Date.now();
      try {
        const out2 = await engine.plan(op, payload.args || {});
        stats.lastPlanMs = Date.now() - t0p; pushRtt('plan', stats.lastPlanMs);
        json(res, 200, { ok: true, op: op, result: out2 });
      } catch (e) {
        stats.lastError = String((e && e.message) || e);
        json(res, 200, { ok: false, op: op, error: stats.lastError, diagnosis: (e && e.diagnosis) || null });
      }
      return;
    },
  };
}
