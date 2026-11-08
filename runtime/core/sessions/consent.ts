// One-time virtual-gui consent tokens for 3.1.
//
// The model can never mint consent: the DSH host asks the human, then calls the
// internal session action to issue a short-lived token bound to the caller.
import { randomUUID } from 'node:crypto'
import { fail } from './types.js'
import type { CallerIdentity, SessionResult } from './types.js'

export const CONSENT_TTL_MS = 30_000

interface ConsentToken {
  callerKey: string
  expiresAt: number
  used: boolean
}

const tokens = new Map<string, ConsentToken>()

export function issueConsentToken(
  c: CallerIdentity,
  now = Date.now(),
): { consentToken: string; expiresAt: number } {
  for (const [id, record] of tokens) if (now > record.expiresAt) tokens.delete(id)
  const token = randomUUID()
  const expiresAt = now + CONSENT_TTL_MS
  tokens.set(token, {
    callerKey: [c.hostId, c.dshSessionId, c.agentId].join('|'),
    expiresAt,
    used: false,
  })
  return { consentToken: token, expiresAt }
}

export type ConsentReason = 'missing' | 'invalid' | 'expired' | 'used'

export function consumeConsentToken(
  c: CallerIdentity,
  token: unknown,
  now = Date.now(),
): ConsentReason | 'ok' {
  if (!token) return 'missing'
  const record = tokens.get(String(token))
  if (!record) return 'invalid'
  if (record.callerKey !== [c.hostId, c.dshSessionId, c.agentId].join('|')) return 'invalid'
  if (record.used) return 'used'
  if (now > record.expiresAt) {
    tokens.delete(String(token))
    return 'expired'
  }
  record.used = true
  return 'ok'
}

export function consentFailure<T = Record<string, unknown>>(
  reason: ConsentReason,
): SessionResult<T> {
  return fail(
    'consent_required',
    'session',
    'virtual-gui requires explicit human consent (' + reason + ')',
    'ask the human to approve starting a virtual Blender window, then retry with the one-time consent token',
    {
      reason,
      options: [
        { action: 'attach', label: 'attach the running Blender window' },
        { action: 'start', label: 'start a virtual Blender window after human approval' },
      ],
      ttl_ms: CONSENT_TTL_MS,
    },
  ) as SessionResult<T>
}
