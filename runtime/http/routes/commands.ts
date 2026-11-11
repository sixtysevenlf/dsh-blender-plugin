import type { IncomingMessage, ServerResponse } from 'node:http'
import { objArg } from './deps.js'
import type { HttpRouteDeps } from './deps.js'
import { COMMAND_CATALOG } from '../../catalog/commands.js'
import { errorMessage, errorField } from '../../core/json.js'

/** /cmd names that are safe to run without a write grant (read + wire ping). */
export function readOnlyCommand(name: string): boolean {
  return name === 'ping' || COMMAND_CATALOG[name]?.access === 'read'
}
// HTTP 路由 /commands：命令清单 / 透传
export function createCommandsRoutes(E: HttpRouteDeps) {
  const { engine, json, leaseGate, readBody, stats } = E
  return {
    async commands(
      req: IncomingMessage,
      res: ServerResponse,
      _url: URL,
      _p: string,
    ): Promise<void> {
      try {
        const c = objArg(await engine.commands())
        json(res, 200, { ok: true, ...c })
      } catch (e) {
        json(res, 200, {
          ok: false,
          error: errorMessage(e),
          diagnosis: errorField(e, 'diagnosis') || null,
        })
      }
      return
    },
    async cmd(req: IncomingMessage, res: ServerResponse, _url: URL, _p: string): Promise<void> {
      const raw = await readBody(req)
      let payload: Record<string, unknown>
      try {
        payload = JSON.parse(raw)
      } catch (e) {
        payload = {}
      }
      const name = String(payload.name || '')
      const gate = readOnlyCommand(name)
        ? leaseGate(payload, { readOnly: true })
        : leaseGate(payload)
      if (gate) {
        json(res, 409, gate)
        return
      }
      const t0 = Date.now()
      stats.cmds++
      try {
        const out = await engine.cmd(
          name,
          objArg(payload.params),
          Number(payload.timeoutMs) || 120000,
          String(payload.instanceId || ''),
        )
        json(res, 200, { ok: true, ms: Date.now() - t0, result: out })
      } catch (e) {
        stats.lastError = errorMessage(e)
        json(res, 200, {
          ok: false,
          ms: Date.now() - t0,
          error: stats.lastError,
          diagnosis: errorField(e, 'diagnosis') || null,
        })
      }
      return
    },
  }
}
