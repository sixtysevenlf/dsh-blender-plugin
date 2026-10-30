// HTTP 路由 /frame：单帧 PNG（T7）
export function createFrameRoutes(E) {
  const { engine, json, pushRtt, stats } = E;
  return {
    async frame(req, res, url, p) {
      const size = Math.max(120, Math.min(1600, Number(url.searchParams.get('size') || 560)));
      const full = url.searchParams.get('full') === '1';
      const areaIndex = Number(url.searchParams.get('area') || 0);
      const t0 = Date.now();
      try {
        const out = full ? await engine.frameArea(areaIndex) : await engine.frame(size);
        stats.frames++; stats.lastFrameMs = Date.now() - t0; pushRtt('frame', stats.lastFrameMs); stats.lastError = null;
        res.writeHead(200, { 'content-type': 'image/png', 'cache-control': 'no-store' });
        res.end(out.png);
      } catch (e) {
        stats.lastError = String((e && e.message) || e);
        json(res, 502, { ok: false, error: stats.lastError, diagnosis: (e && e.diagnosis) || null });
      }
      return;
    },
  };
}
