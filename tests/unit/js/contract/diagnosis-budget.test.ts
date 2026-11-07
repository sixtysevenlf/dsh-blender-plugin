/**
 * 3.6: the first-call diagnostic budgets are a contract value, not an implicit
 * constant in a route. Lock the config numbers and keep every Gateway probe at
 * or below the stage budget it is reported under.
 */
import { describe, expect, it } from 'vitest'
import { AVAILABILITY_BUDGETS_MS } from '../../../../src/contracts/availability'
import {
  ADDON_DOCTOR_STATUS_TIMEOUT_MS,
  ADDON_FLAGS_TIMEOUT_MS,
  ADDON_INTEGRATION_TIMEOUT_MS,
  ADDON_STATUS_TIMEOUT_MS,
} from '../../../../runtime/core/engine-api/doctor.js'

describe('first-call diagnosis budgets', () => {
  it('locks the config/availability.json budgets', () => {
    expect(AVAILABILITY_BUDGETS_MS).toEqual({
      config: 100,
      session: 100,
      spawn: 250,
      transport: 3000,
      handshake: 3000,
      addon: 3000,
      bpy: 3000,
      queue: 3000,
      render: 3000,
    })
  })

  it('keeps each bounded addon probe inside its stage budget', () => {
    expect(ADDON_STATUS_TIMEOUT_MS).toBeLessThanOrEqual(AVAILABILITY_BUDGETS_MS.addon)
    expect(ADDON_DOCTOR_STATUS_TIMEOUT_MS).toBeLessThanOrEqual(AVAILABILITY_BUDGETS_MS.addon)
    expect(ADDON_FLAGS_TIMEOUT_MS).toBeLessThanOrEqual(AVAILABILITY_BUDGETS_MS.transport)
    expect(ADDON_INTEGRATION_TIMEOUT_MS).toBeLessThanOrEqual(AVAILABILITY_BUDGETS_MS.addon)
  })
})
