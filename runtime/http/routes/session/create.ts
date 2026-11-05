// /session 路由：launch（拆分）
import type { IncomingMessage, ServerResponse } from 'node:http'
import { objArg } from '../deps.js'
import type { HttpRouteDeps } from '../deps.js'
import { errorMessage } from '../../../core/json.js'

export function createLaunchHandler(E: HttpRouteDeps) {
  const { engine, json, readBody, stats } = E
  return async function launch(
    req: IncomingMessage,
    res: ServerResponse,
    _url: URL,
    _p: string,
  ): Promise<void> {
    const raw = await readBody(req)
    let payload: Record<string, unknown>
    try {
      payload = JSON.parse(raw)
    } catch (e) {
      payload = {}
    }
    const t0 = Date.now()
    try {
      const out = objArg(await engine.launchBlender(payload))
      stats.launches = (stats.launches || 0) + 1
      stats.lastLaunchMs = Date.now() - t0
      const doc =
        out.ok && (out.launched || out.already) ? await engine.doctor().catch(() => null) : null
      json(
        res,
        out.ok ? 200 : 200,
        Object.assign({ ok: !!out.ok, ms: Date.now() - t0, launches: stats.launches }, out, {
          doctor: doc,
        }),
      )
    } catch (e) {
      json(res, 500, {
        ok: false,
        ms: Date.now() - t0,
        error: errorMessage(e),
        hint: '看后端日志；也可用 exe/path 显式指定 blender.exe',
      })
    }
    return
  }
}
