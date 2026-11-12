// /session 路由：who（拆分）
import type { IncomingMessage, ServerResponse } from 'node:http'
import type { HttpRouteDeps } from '../deps.js'
import { currentCaller } from '../../caller.js'

export function createWhoHandler(E: HttpRouteDeps) {
  const { PORT, describeConfig, engine, json, leaseView, reqCounts, sessionRegistry, stats } = E
  return async function who(
    req: IncomingMessage,
    res: ServerResponse,
    _url: URL,
    _p: string,
  ): Promise<void> {
    const caller = currentCaller()
    json(res, 200, {
      ok: true,
      // No caller header means no identity: never fall back to every session.
      sessions: !sessionRegistry || !caller ? [] : sessionRegistry.listFor(caller),
      lease: leaseView(),
      metrics: engine.metrics(),
      stats: stats,
      config: describeConfig({ httpPort: PORT }),
      requests: Object.fromEntries(reqCounts),
    })
    return
  }
}
