// HTTP 路由 /loop：内环（T7）
export function createLoopRoutes(E) {
  const { engine, isReadOnly, json, leaseGate, readBody, stats } = E;
  return {
    async loop(req, res, url, p) {
      const raw = await readBody(req);
      let payload = {};
      try { payload = JSON.parse(raw); } catch (e) { payload = {}; }
      const op = String(payload.op || 'status');
      const gate = isReadOnly('/loop', op) ? null : leaseGate(payload);
      if (gate) { json(res, 409, gate); return; }
      stats.loops++;
      try {
        let out;
        if (op === 'start') out = await engine.loopStart(payload.spec || {});
        else if (op === 'stop') out = await engine.loopStop();
        else if (op === 'bench') out = await engine.loopBench(payload.iterations);
        else if (op === 'board') out = await engine.loopBoard(payload.limit || 10, payload.groups !== false);
        else if (op === 'export') out = await engine.loopExport({ path: payload.path, top: payload.top, includeVariants: !!payload.include_variants, note: payload.note });
        else if (op === 'help') out = await engine.loopHelp();
        else out = await engine.loopStatus(payload.history || 8, payload.board || 0);
        json(res, 200, { ok: true, op: op, result: out });
      } catch (e) {
        json(res, 200, { ok: false, op: op, error: String((e && e.message) || e), diagnosis: (e && e.diagnosis) || null });
      }
      return;
    },
  };
}
