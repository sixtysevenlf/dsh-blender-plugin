// Pre-spawn gate for virtual-gui sessions. It returns the same availability
// envelope as the rest of the Gateway and never spawns Blender/display helpers.
import { consentFailure, consumeConsentToken } from './consent.js'
import { fail } from './types.js'
import type { CallerIdentity, SessionRecord, SessionResult } from './types.js'
import type { DisplayCapabilities } from '../../display/types.js'

export interface VirtualGuiStartInput {
  caller: CallerIdentity
  kind?: string
  consentToken?: string
  capability: DisplayCapabilities
  existing: SessionRecord | null
}

export function startVirtualGuiGate(
  input: VirtualGuiStartInput,
): SessionResult<{ capability: DisplayCapabilities }> {
  const { capability, existing } = input
  if ((input.kind || 'virtual-gui') !== 'virtual-gui')
    return fail(
      'invalid_params',
      'session',
      'start only applies to virtual-gui',
      'use attach for a user-started Blender',
      { kind: input.kind },
    )
  if (existing && existing.kind === 'user-gui' && existing.status === 'ready')
    return fail(
      'session_already_available',
      'session',
      'a ready user-gui session already exists for this caller',
      'attach the running Blender window instead of starting a virtual one',
      { sessionId: existing.id, kind: existing.kind },
    )
  const evidence = {
    feature: 'virtualGui',
    provider: capability.provider,
    provider_implemented: capability.provider_implemented,
    enabled: capability.enabled,
    preview: capability.preview,
    ready: capability.ready,
    reason: capability.reason,
    missing: capability.missing,
    prerequisites: capability.prerequisites,
    feature_source: capability.feature_source,
  }
  if (!capability.enabled)
    return fail(
      'feature_disabled',
      'config',
      'virtual-gui is not enabled (features.virtualGui=' + capability.feature + ')',
      capability.fix,
      evidence,
    )
  if (!capability.ready)
    return fail(
      capability.reason && capability.reason.indexOf('prerequisites') === 0
        ? 'prerequisites_missing'
        : 'provider_unavailable',
      'config',
      'virtual-gui is not ready: ' +
        (capability.reason || 'unknown') +
        (capability.missing.length ? ' (missing: ' + capability.missing.join(', ') + ')' : ''),
      capability.fix,
      evidence,
    )
  const consent = consumeConsentToken(input.caller, input.consentToken)
  if (consent !== 'ok') return consentFailure<{ capability: DisplayCapabilities }>(consent)
  return { ok: true, capability }
}
