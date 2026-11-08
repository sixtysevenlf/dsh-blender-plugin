// Provider selection order for virtual-gui displays. 3.1 GA ships the Windows
// W1 hidden-desktop provider; Windows W2 (IDD) stays a fallback only if W1
// fails acceptance, Linux Xvfb+VirtualGL ships as preview-only, and macOS has
// no provider. Selection returns a provider only once capabilities are ready, so
// the session layer fails closed instead of faking readiness.
import type { DisplayCapabilities, DisplayProvider } from './types.js'
import { displayCapabilities } from './prerequisites.js'
import { LinuxXvfbVirtualGlProvider } from './linux-xvfb-virtualgl.js'
import { WinHiddenDesktopProvider } from './win-hidden-desktop.js'

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

export interface SelectProviderOptions {
  platform?: NodeJS.Platform | string
  env?: NodeJS.ProcessEnv
  /** Explicit helper path for tests; forwarded to the Windows provider. */
  helperPath?: string | null
  /** Capabilities to select against; defaults to the live machine capabilities. */
  capabilities?: DisplayCapabilities
}

/** Ordered fallback list; the first implemented candidate wins. */
export function providerCandidates(
  platform: NodeJS.Platform | string = process.platform,
): ProviderCandidate[] {
  if (platform === 'win32') {
    return [
      { kind: 'win-hidden-desktop', preview: false, implemented: true },
      { kind: 'win-idd', preview: false, implemented: false },
    ]
  }
  if (platform === 'linux') {
    return [{ kind: 'linux-xvfb-virtualgl', preview: true, implemented: true }]
  }
  return [{ kind: 'macos-provider', preview: false, implemented: false }]
}

export function selectDisplayProvider(options: SelectProviderOptions = {}): ProviderSelection {
  const platform = options.platform || process.platform
  const capabilities = options.capabilities || displayCapabilities()
  const candidates = providerCandidates(platform)
  const chosen = candidates.find((candidate) => candidate.implemented)
  if (!capabilities.ready) {
    return {
      provider: null,
      candidates,
      capabilities,
      reason: capabilities.reason || 'provider_unavailable',
    }
  }
  if (chosen && chosen.kind === 'win-hidden-desktop') {
    return {
      provider: new WinHiddenDesktopProvider({
        env: options.env,
        helperPath: options.helperPath,
      }),
      candidates,
      capabilities,
      reason: null,
    }
  }
  if (chosen && chosen.kind === 'linux-xvfb-virtualgl') {
    return {
      provider: new LinuxXvfbVirtualGlProvider({ env: options.env }),
      candidates,
      capabilities,
      reason: null,
    }
  }
  return { provider: null, candidates, capabilities, reason: 'provider_unavailable' }
}
