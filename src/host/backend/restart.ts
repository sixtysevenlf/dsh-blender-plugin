/** Bounded auto-restart policy for the host-owned Gateway process. */
export const GATEWAY_RESTART_LIMIT = 5
export const GATEWAY_RESTART_BASE_MS = 1000
export const GATEWAY_RESTART_MAX_MS = 30000

interface RestartState {
  failures: number
  nextAt: number
}

const state: RestartState = { failures: 0, nextAt: 0 }

/** Exponential backoff capped at 30 s; attempt is 1-based. */
export function gatewayRestartBackoffMs(attempt: number): number {
  const n = Math.max(1, Math.floor(attempt))
  return Math.min(GATEWAY_RESTART_MAX_MS, GATEWAY_RESTART_BASE_MS * 2 ** (n - 1))
}

/** True while the watchdog may try another restart (under the cap and past the backoff). */
export function canRestartGateway(now = Date.now()): boolean {
  return state.failures < GATEWAY_RESTART_LIMIT && now >= state.nextAt
}

/** Record one failed restart and return the next allowed attempt time. */
export function noteGatewayRestartFailure(now = Date.now()): number {
  state.failures += 1
  state.nextAt = now + gatewayRestartBackoffMs(state.failures)
  return state.nextAt
}

/** A healthy Gateway (or an explicit op=start) resets the budget. */
export function noteGatewayRestartSuccess(): void {
  state.failures = 0
  state.nextAt = 0
}

/** Explicit user action: allow a fresh bounded-restart budget. */
export function resetGatewayRestartBudget(): void {
  noteGatewayRestartSuccess()
}

export function gatewayRestartState(): { failures: number; nextAt: number } {
  return { failures: state.failures, nextAt: state.nextAt }
}
