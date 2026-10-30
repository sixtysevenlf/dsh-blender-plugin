// HTTP 路由 /worker：热无头会话（T7）
export function createWorkerRoutes(E) {
  const { engine, isReadOnly, json, leaseGate, readBody, stats, streamJson } = E;
  return {
    async worker(req, res, url, p) {
      // 热无头会话：start / exec / status / stop / restart（exec 走流式，长代码不会被客户端超时掐断）
      const raw = await readBody(req);
      let payload = {};
      try { payload = JSON.parse(raw); } catch (e) { payload = {}; }
      const op = String(payload.op || 'status');
      // P0：worker 是**独立无头进程**，不碰 live GUI 场景 ⇒ 显式 isolated:true 时不占全局写租约，
      // 否则多会话并行建模会互相 409（C 组调研的 P0 阻塞项）。同一 worker 仍由自身串行。
      const gate = (payload.isolated || isReadOnly('/worker', op)) ? null : leaseGate(payload);
      if (gate) { json(res, 409, gate); return; }
      stats.worker = (stats.worker || 0) + 1;
      await streamJson(res, async () => {
        try {
          // v0.9.4（P1-1）：全部按 name 走（缺省 = 'default'，与老调用逐字节同义）
          if (op === 'start') return { ok: true, op: op, result: await engine.worker.start(payload, payload && payload.name) };
          if (op === 'stop') return { ok: true, op: op, result: await engine.worker.stop(payload && payload.name) };
          if (op === 'restart') return { ok: true, op: op, result: await engine.worker.restart(payload && payload.name, payload) };
          if (op === 'list') return { ok: true, op: op, result: { ok: true, workers: engine.worker.list() } };
          if (op === 'exec') {
            const purge = payload.purgePrefix ? String(payload.purgePrefix).split(',').map(function (x) { return x.trim(); }).filter(Boolean) : null;
            const r = await engine.worker.exec(String(payload.code || ''), Number(payload.timeoutMs) || 120000, purge, payload && payload.name);
            // v0.8.4：错误/回溯提到信封层，避免客户端只看到 unknown
            return { ok: !!r.ok, op: op, result: r, error: (r && r.error) ? String(r.error) : null,
                     traceback: (r && r.traceback) ? String(r.traceback) : null };
          }
          if (op === 'see') {
            // P0 无头 see：临时相机 + Workbench 离屏；返回 png_b64 + coverage + ms
            return { ok: true, op: op, result: await engine.worker.see(payload.spec || payload.see || {}, payload && payload.name) };
          }
          if (op === 'watch') {
            return { ok: true, op: op, result: await engine.worker.watch(payload.spec || payload.watch || {}, payload && payload.name) };
          }
          if (op === 'reset') {
            return { ok: true, op: op, result: await engine.worker.reset(payload && payload.name) };
          }
          return { ok: true, op: 'status', result: await engine.worker.status(payload && payload.name) };
        } catch (e) {
          stats.lastError = String((e && e.message) || e);
          return { ok: false, op: op, error: stats.lastError, hint: (e && e.hint) || null };
        }
      });
      return;
    },
  };
}
