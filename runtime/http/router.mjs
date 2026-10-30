// HTTP 路由分发（T7）：注册表匹配 → 处理器；未命中 404，处理器抛错 500。
// 匹配顺序由 routes/index.mjs 的数组顺序决定（与拆分前 if 链顺序一致）。
import { json } from "./response.mjs";

export function createRouter(E) {
  const routes = E.routes;
  return async function router(req, res) {
    const url = new URL(req.url, "http://" + E.HOST + ":" + String(E.PORT));
    const p = url.pathname;
    E.bump(p);
    try {
      for (const r of routes) {
        if (r.method && req.method !== r.method) continue;
        const matched = r.paths ? r.paths.indexOf(p) >= 0 : (p === r.exact || (r.prefix && p.startsWith(r.prefix)));
        if (!matched) continue;
        await r.fn(req, res, url, p);
        return;
      }
      json(res, 404, { ok: false, error: "not found", path: p });
    } catch (e) {
      json(res, 500, { ok: false, error: String((e && e.stack) || e) });
    }
  };
}
