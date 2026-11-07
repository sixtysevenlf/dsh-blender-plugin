import { describe, expect, it } from 'vitest'
import {
  GATEWAY_RESTART_LIMIT,
  canRestartGateway,
  gatewayRestartBackoffMs,
  gatewayRestartState,
  noteGatewayRestartFailure,
  noteGatewayRestartSuccess,
  resetGatewayRestartBudget,
} from '../../../src/host/backend/restart.js'

describe('Gateway bounded restart policy', () => {
  it('backs off exponentially and caps at 30 s', () => {
    expect(gatewayRestartBackoffMs(1)).toBe(1000)
    expect(gatewayRestartBackoffMs(2)).toBe(2000)
    expect(gatewayRestartBackoffMs(3)).toBe(4000)
    expect(gatewayRestartBackoffMs(4)).toBe(8000)
    expect(gatewayRestartBackoffMs(5)).toBe(16000)
    expect(gatewayRestartBackoffMs(6)).toBe(30000)
    expect(gatewayRestartBackoffMs(60)).toBe(30000)
  })

  it('stops after five consecutive failures until an explicit reset', () => {
    resetGatewayRestartBudget()
    const base = 1_000_000
    for (let i = 1; i <= GATEWAY_RESTART_LIMIT; i++) {
      expect(canRestartGateway(base + 10 * 60_000)).toBe(true)
      noteGatewayRestartFailure(base)
    }
    expect(gatewayRestartState().failures).toBe(GATEWAY_RESTART_LIMIT)
    expect(canRestartGateway(base + 24 * 60 * 60_000)).toBe(false)
    resetGatewayRestartBudget()
    expect(canRestartGateway(base)).toBe(true)
  })

  it('does not restart inside the backoff window', () => {
    resetGatewayRestartBudget()
    noteGatewayRestartFailure(10_000)
    expect(canRestartGateway(10_000)).toBe(false)
    expect(canRestartGateway(10_999)).toBe(false)
    expect(canRestartGateway(11_000)).toBe(true)
    noteGatewayRestartSuccess()
    expect(canRestartGateway(11_000)).toBe(true)
  })
})
