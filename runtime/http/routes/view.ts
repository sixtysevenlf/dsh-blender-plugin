import type { IncomingMessage, ServerResponse } from "node:http";
import { objArg } from "./deps.js";
import type { HttpRouteDeps } from "./deps.js";
import { errorMessage, errorField } from "../../core/json.js";
// HTTP 路由 /view：自定义视角（T7）
export function createViewRoutes(E: HttpRouteDeps) {
  const { engine, json, pushRtt, readBody, stats } = E;
  return {
    async view(req: IncomingMessage, res: ServerResponse, _url: URL, _p: string): Promise<void> {
      const raw = await readBody(req);
      let payload: Record<string, unknown> = {};
      try { payload = JSON.parse(raw); } catch (e) { payload = {}; }
      const t0 = Date.now();
      try {
        const out = objArg(await engine.view(objArg(payload)));
        const meta = objArg(out.meta);
        stats.views++; stats.lastViewMs = Date.now() - t0; pushRtt('view', stats.lastViewMs); stats.lastError = null;
        if (payload.asJson) { json(res, 200, { ok: true, ms: Date.now() - t0, meta: out.meta }); return; }
        res.writeHead(200, { 'content-type': 'image/png', 'cache-control': 'no-store',
          // ⚠ 实测坑（v0.9.1）：HTTP 头值不能含非 ASCII —— warning 里有中文，直接塞会 502 "Invalid character in header content"。
          // 所以整段 URL 编码后再放头里（客户端 decodeURIComponent 还原）。
          'x-dsh-view': encodeURIComponent(JSON.stringify({
            mode: meta.mode, ms: meta.ms, path: meta.path, width: meta.width, height: meta.height,
            coverage_estimate: meta.coverage_estimate, objects_in_frame: meta.objects_in_frame,
            min_margin_px: meta.min_margin_px, scene_bbox: meta.scene_bbox,
            warning: meta.warning, fallback_from: meta.fallback_from,
            region: objArg(meta.meta).region || null,
          }).slice(0, 6000)) });
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
