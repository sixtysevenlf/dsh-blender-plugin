// /session 路由：lease / release（拆分）
import type { IncomingMessage, ServerResponse } from 'node:http'
import type { HttpRouteDeps } from '../deps.js'
import { currentCaller } from '../../caller.js'
import { dispatchSessionAction } from './api.js'

export function createLeaseHandler(E: HttpRouteDeps) {
  const { json, leaseAcquire, leaseView, readBody, releaseStaleLease, sessionRegistry } = E
  return async function lease(
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
    if (typeof payload.action === 'string' && payload.action) {
      const result = await dispatchSessionAction(sessionRegistry, payload.action, payload)
      json(res, result.status, result.body)
      return
    }
    const holder = String(payload.holder || 'anonymous')
    releaseStaleLease('op=lease 到达时探活发现持有者进程不存在') // v0.9.4
    if (payload.renewOnly) {
      // 心跳：只有本来就持有租约时才续期 —— 不在空闲时抢占，避免多会话互相锁死
      const v = leaseView()
      if (v.active && v.holder === holder) {
        const rr = leaseAcquire(holder, payload.ttlMs, false, payload.scope)
        json(res, 200, { ok: true, lease: 'lease' in rr ? rr.lease : null, renewed: true })
      } else {
        json(res, 200, { ok: true, lease: v, renewed: false })
      }
      return
    }
    const r = leaseAcquire(holder, payload.ttlMs, !!payload.force, payload.scope)
    if ('denied' in r) {
      const otherScope = leaseView().scope || null
      json(res, 409, {
        ok: false,
        error: 'leased',
        holder: r.holder,
        expiresInMs: r.expiresInMs,
        holderScope: otherScope,
        hint:
          '另一个会话持有写权限租约；force:true 可抢占，或等 ' +
          Math.round(r.expiresInMs / 1000) +
          's 后重试',
      })
      return
    }
    json(res, 200, {
      ok: true,
      lease: 'lease' in r ? r.lease : null,
      renewed: !!payload.renewOnly,
    })
    return
  }
}

export function createReleaseHandler(E: HttpRouteDeps) {
  const { LEASE, json, leaseView, readBody, sessionRegistry } = E
  return async function release(
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
    const caller = currentCaller()
    if (caller) {
      const result = sessionRegistry.release(
        caller,
        payload.sessionId === undefined ? undefined : String(payload.sessionId),
      )
      json(res, result.ok ? 200 : 409, result as unknown as Record<string, unknown>)
      return
    }
    const want = String(payload.holder || '')
    let released: string | null = null
    if (LEASE.holder && (!want || LEASE.holder === want)) {
      released = LEASE.holder
      LEASE.holder = null
      LEASE.token = null
      LEASE.expiresAt = null
      LEASE.acquiredAt = null
    }
    json(res, 200, { ok: true, released: released, lease: leaseView() })
    return
  }
}
