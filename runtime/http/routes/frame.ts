import type { IncomingMessage, ServerResponse } from "node:http";
import { objArg } from "./deps.js";
import type { HttpRouteDeps } from "./deps.js";
import { errorMessage, errorField } from "../../core/json.js";
// HTTP 路由 /frame：单帧 PNG（T7）
export function createFrameRoutes(E: HttpRouteDeps) {
  const { engine, json, pushRtt, stats } = E;
  return {
    async frame(req: IncomingMessage, res: ServerResponse, url: URL, _p: string): Promise<void> {
      const size = Math.max(120, Math.min(1600, Number(url.searchParams.get('size') || 560)));
      const full = url.searchParams.get('full') === '1';
      const areaIndex = Number(url.searchParams.get('area') || 0);
      const t0 = Date.now();
      try {
        const out = objArg(full ? await engine.frameArea(areaIndex) : await engine.frame(size));
        stats.frames++; stats.lastFrameMs = Date.now() - t0; pushRtt('frame', stats.lastFrameMs); stats.lastError = null;
        res.writeHead(200, { 'content-type': 'image/png', 'cache-control': 'no-store' });
        const png = out.png;
        res.end(typeof png === 'string' || Buffer.isBuffer(png) ? png : undefined);
      } catch (e) {
        stats.lastError = errorMessage(e);
        json(res, 502, { ok: false, error: stats.lastError, diagnosis: errorField(e, 'diagnosis') || null });
      }
      return;
    },
  };
}
