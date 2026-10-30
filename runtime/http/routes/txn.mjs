// HTTP 路由 /txn：事务（T7）
export function createTxnRoutes(E) {
  const { engine, isReadOnly, json, leaseGate, readBody, stats, streamJson } = E;
  return {
    async txn(req, res, url, p) {
      // 事务：snapshot / restore / list / prune / mark / revert / marks / drop / help
      const raw = await readBody(req);
      let payload = {};
      try { payload = JSON.parse(raw); } catch (e) { payload = {}; }
      const op = String(payload.op || 'list');
      const gate = isReadOnly('/txn', op) ? null : leaseGate(payload);
      if (gate) { json(res, 409, gate); return; }
      stats.txn = (stats.txn || 0) + 1;
      await streamJson(res, async () => {
        try {
          const r = await engine.txn(op, payload.args || {});
          return { ok: !!(r && r.ok !== false), op: op, result: r };
        } catch (e) {
          stats.lastError = String((e && e.message) || e);
          return { ok: false, op: op, error: stats.lastError, diagnosis: (e && e.diagnosis) || null };
        }
      });
      return;
    },
  };
}
