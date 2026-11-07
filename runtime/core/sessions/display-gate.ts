// Pre-spawn gate for virtual-gui sessions. It returns the same availability
// envelope as the rest of the Gateway and never spawns Blender/display helpers.
import { fail } from './types.js'
import type { SessionResult } from './types.js'
import type { DisplayCapabilities } from '../../display/types.js'

export function startVirtualGuiGate(
  capability: DisplayCapabilities,
): SessionResult<Record<string, unknown>> {
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
  return fail(
    'provider_unavailable',
    'config',
    'virtual-gui provider is selected but this build has no startable provider',
    'use user-gui or blender_run headless',
    evidence,
  )
}
