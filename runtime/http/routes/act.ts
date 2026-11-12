import type { IncomingMessage, ServerResponse } from 'node:http'
import { objArg } from './deps.js'
import type { HttpRouteDeps } from './deps.js'
import { errorMessage, errorField } from '../../core/json.js'
import { currentCaller } from '../caller.js'
import { gateResponse } from '../session-gate.js'
/** Take the session py lock (waiting up to waitMs); a failure is already a gate body. */
async function takePyLock(
  payload: Record<string, unknown>,
  caller: ReturnType<typeof currentCaller>,
  registry: HttpRouteDeps['sessionRegistry'] | null,
): Promise<{ gate: Record<string, unknown> | null; held: boolean }> {
  if (!caller || !registry) return { gate: null, held: false }
  const sessionId = payload.sessionId === undefined ? undefined : String(payload.sessionId)
  const begin = await registry.beginPy(caller, sessionId, Number(payload.waitMs) || 0)
  return begin.ok ? { gate: null, held: true } : { gate: gateResponse(begin), held: false }
}

/** A timed-out call is still running inside Blender: keep the lock and say so. */
function pyTimeoutBody(
  payload: Record<string, unknown>,
  caller: ReturnType<typeof currentCaller>,
  registry: HttpRouteDeps['sessionRegistry'] | null,
): Record<string, unknown> | null {
  if (!caller || !registry) return null
  const sessionId = payload.sessionId === undefined ? undefined : String(payload.sessionId)
  const still = registry.timeoutPy(caller, sessionId)
  return still.ok ? null : gateResponse(still)
}

/** Successful act receipt: one place for the fields the tools read. */
function actReply(o: Record<string, unknown>, t0: number): Record<string, unknown> {
  return {
    ok: o.ok !== false,
    ms: o.ms != null ? o.ms : Date.now() - t0,
    mainThreadMs: o.mainThreadMs != null ? o.mainThreadMs : null,
    executed: !!o.executed,
    stdout: String(o.stdout || ''),
    stderr: String(o.stderr || ''),
    error: o.error || null,
    traceback: o.traceback || null,
    file: o.file || null,
    marker_missing: !!o.marker_missing,
    sceneEpoch: o.sceneEpoch || null,
    errorKind: o.error ? 'execution' : null,
  }
}

// HTTP 路由 /act：Python 执行
export function createActRoutes(E: HttpRouteDeps) {
  const { engine, json, leaseGate, pushRtt, readBody, stats } = E
  return {
    async act(req: IncomingMessage, res: ServerResponse, _url: URL, _p: string): Promise<void> {
      const raw = await readBody(req)
      let payload: Record<string, unknown>
      try {
        payload = JSON.parse(raw)
      } catch (e) {
        payload = { code: raw }
      }
      const isPy = payload.py === true
      const caller = isPy ? currentCaller() : null
      const registry = caller ? E.sessionRegistry : null
      // Ownership, pause, scope and the legacy lease live here. With wait_ms the
      // py mutex is left to beginPy (pyLock:false) so the wait can actually
      // happen; without it the gate answers py_busy before the scope checks.
      const waits = isPy && Number(payload.waitMs) > 0
      const gate = leaseGate(payload, waits ? { pyLock: false } : {})
      if (gate) return json(res, 409, gate)
      const lock = await takePyLock(payload, caller, registry)
      if (lock.gate) return json(res, 409, lock.gate)
      let pyHeld = lock.held
      const t0 = Date.now()
      try {
        const out = objArg(
          await engine.act(
            payload.code || '',
            Number(payload.timeoutMs) || 120000,
            payload.file || null,
            payload.instanceId === undefined ? '' : String(payload.instanceId),
          ),
        )
        stats.acts++
        stats.lastActMs = Date.now() - t0
        pushRtt('act', stats.lastActMs)
        stats.lastError = null
        json(res, 200, actReply(out || {}, t0))
      } catch (e) {
        stats.acts++
        stats.lastError = errorMessage(e)
        // A Gateway timeout does not mean the script stopped: the addon keeps
        // executing on Blender's main thread. Keep the lock and answer
        // py_still_running so the next agent cannot start a second script.
        const timedOut =
          errorField(e, 'code') === 'addon_timeout' || /addon timeout/i.test(stats.lastError)
        if (pyHeld && timedOut) {
          const still = pyTimeoutBody(payload, caller, registry)
          pyHeld = false
          if (still) return json(res, 409, still)
        }
        json(res, 200, {
          ok: false,
          ms: Date.now() - t0,
          error: stats.lastError,
          errorKind: errorField(e, 'kind') || null,
          diagnosis: errorField(e, 'diagnosis') || null,
        })
      } finally {
        if (pyHeld && caller && registry)
          registry.endPy(
            caller,
            payload.sessionId === undefined ? undefined : String(payload.sessionId),
          )
      }
      return
    },
  }
}
