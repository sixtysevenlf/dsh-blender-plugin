// HTTP 路由 /view：自定义视角（T7）
export function createViewRoutes(E) {
  const { engine, json, pushRtt, readBody, stats } = E;
  return {
    async view(req, res, url, p) {
      const raw = await readBody(req);
      let payload = {};
      try { payload = JSON.parse(raw); } catch (e) { payload = {}; }
      const t0 = Date.now();
      try {
        const out = await engine.view(payload);
        stats.views++; stats.lastViewMs = Date.now() - t0; pushRtt('view', stats.lastViewMs); stats.lastError = null;
        if (payload.asJson) { json(res, 200, { ok: true, ms: Date.now() - t0, meta: out.meta }); return; }
        res.writeHead(200, { 'content-type': 'image/png', 'cache-control': 'no-store',
          // ⚠ 实测坑（v0.9.1）：HTTP 头值不能含非 ASCII —— warning 里有中文，直接塞会 502 "Invalid character in header content"。
          // 所以整段 URL 编码后再放头里（客户端 decodeURIComponent 还原）。
          'x-dsh-view': encodeURIComponent(JSON.stringify({
            mode: out.meta.mode, ms: out.meta.ms, path: out.meta.path, width: out.meta.width, height: out.meta.height,
            coverage_estimate: out.meta.coverage_estimate, objects_in_frame: out.meta.objects_in_frame,
            min_margin_px: out.meta.min_margin_px, scene_bbox: out.meta.scene_bbox,
            warning: out.meta.warning, fallback_from: out.meta.fallback_from,
            region: (out.meta.meta || {}).region || null,
          }).slice(0, 6000)) });
        res.end(out.png);
      } catch (e) {
        stats.lastError = String((e && e.message) || e);
        json(res, 502, { ok: false, error: stats.lastError, diagnosis: (e && e.diagnosis) || null });
      }
      return;
    },
  };
}
