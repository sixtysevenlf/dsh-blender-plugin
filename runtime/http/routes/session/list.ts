// /session 路由：who（F1.1 拆分）
import type { IncomingMessage, ServerResponse } from 'node:http'
import type { HttpRouteDeps } from '../deps.js'

export function createWhoHandler(E: HttpRouteDeps) {
  const { PORT, describeConfig, engine, json, leaseView, reqCounts, stats } = E
  return async function who(
    req: IncomingMessage,
    res: ServerResponse,
    _url: URL,
    _p: string,
  ): Promise<void> {
    json(res, 200, {
      ok: true,
      lease: leaseView(),
      metrics: engine.metrics(),
      stats: stats,
      config: describeConfig({ httpPort: PORT }),
      requests: Object.fromEntries(reqCounts),
    })
    return
  }
}
