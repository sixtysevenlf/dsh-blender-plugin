// 宿主集成路由入口（拆分）
import type { IncomingMessage, ServerResponse } from 'node:http'
import type { EngineSurface } from '../../core/engine/index.js'
import { handleHost } from './routes.js'

export interface SelectionStoreView {
  put: (payload: unknown) => Record<string, unknown>
  snapshot: () => unknown
  consume: (seq: unknown, payload: Record<string, unknown>) => Record<string, unknown>
}
export interface HostRoutesDeps {
  engine: EngineSurface
  selectionStore: SelectionStoreView
  hostJson: (res: ServerResponse, code: number, obj: unknown, origin?: unknown) => void
  hostErrText: (e: unknown) => string
}

export function createHostRoutes(E: HostRoutesDeps) {
  return {
    async host(req: IncomingMessage, res: ServerResponse, url: URL, p: string): Promise<void> {
      try {
        await handleHost(E, req, res, url, p)
      } catch (e) {
        try {
          E.hostJson(res, 500, { ok: false, error: E.hostErrText(e) }, req.headers.origin || null)
        } catch (e2) {
          try {
            res.end()
          } catch (e3) {
            /* 已经断了就算了 */
          }
        }
      }
      return
    },
  }
}
