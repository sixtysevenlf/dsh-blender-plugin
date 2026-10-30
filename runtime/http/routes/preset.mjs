// HTTP 路由 /preset：配方库（T7）
export function createPresetRoutes(E) {
  const { engine, isReadOnly, json, leaseGate, readBody, stats, streamJson } = E;
  return {
    async preset(req, res, url, p) {
      // 配方库：save / list / get / delete / apply / export / import / help
      const raw = await readBody(req);
      let payload = {};
      try { payload = JSON.parse(raw); } catch (e) { payload = {}; }
      const op = String(payload.op || 'list');
      const gate = isReadOnly('/preset', op) ? null : leaseGate(payload);
      if (gate) { json(res, 409, gate); return; }
      stats.preset = (stats.preset || 0) + 1;
      await streamJson(res, async () => {
        try {
          const r = await engine.preset(op, payload.args || {});
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
