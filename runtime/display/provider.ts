// Provider selection order for virtual-gui displays. 3.1 GA ships the Windows
// W1 hidden-desktop provider; Windows W2 (IDD) is only a fallback when W1 fails
// acceptance, Linux Xvfb+VirtualGL stays preview-only, and macOS has no
// provider. Until a provider is installed, selection returns null so the
// session layer fails closed instead of faking readiness.
import type { DisplayCapabilities, DisplayProvider } from './types.js'
import { displayCapabilities } from './prerequisites.js'

export interface ProviderCandidate {
  kind: string
  preview: boolean
  implemented: boolean
}

export interface ProviderSelection {
  provider: DisplayProvider | null
  candidates: ProviderCandidate[]
  capabilities: DisplayCapabilities
  reason: string | null
}

/** Ordered fallback list; the first implemented candidate wins. */
export function providerCandidates(
  platform: NodeJS.Platform | string = process.platform,
): ProviderCandidate[] {
  if (platform === 'win32') {
    return [
      { kind: 'win-hidden-desktop', preview: false, implemented: false },
      { kind: 'win-idd', preview: false, implemented: false },
    ]
  }
  if (platform === 'linux') {
    return [{ kind: 'linux-xvfb-virtualgl', preview: true, implemented: false }]
  }
  return [{ kind: 'macos-provider', preview: false, implemented: false }]
}

export function selectDisplayProvider(): ProviderSelection {
  const capabilities = displayCapabilities()
  // No platform implementation is bundled yet, so selection never returns one.
  // Candidates stay visible to doctor/capabilities without claiming readiness.
  return {
    provider: null,
    candidates: providerCandidates(),
    capabilities,
    reason: capabilities.ready ? 'provider_unavailable' : capabilities.reason,
  }
}
