/**
 * The failure envelope is a shared contract: a missing field or an unknown
 * stage is a defect, not a message we can improvise at the call site.
 */
import fs from 'node:fs'
import path from 'node:path'
import { describe, expect, it } from 'vitest'
import {
  AVAILABILITY_BUDGETS_MS,
  AVAILABILITY_FALLBACK_KINDS,
  AVAILABILITY_REQUIRED_FIELDS,
  AVAILABILITY_STAGES,
  availabilityFailure,
  validateAvailabilityEnvelope,
} from '../../../../src/contracts/availability'
import { normalizeAvailability } from '../../../../runtime/core/availability.js'

const ROOT = path.resolve(__dirname, '../../../..')
const CONFIG = JSON.parse(fs.readFileSync(path.join(ROOT, 'config', 'availability.json'), 'utf8'))

describe('availability contract', () => {
  it('keeps the generated constants in step with config/availability.json', () => {
    expect(AVAILABILITY_STAGES).toEqual(CONFIG.stages)
    expect(AVAILABILITY_REQUIRED_FIELDS).toEqual(CONFIG.requiredFields)
    expect(AVAILABILITY_FALLBACK_KINDS).toEqual(CONFIG.fallbackKinds)
    expect(AVAILABILITY_BUDGETS_MS).toEqual(CONFIG.budgetsMs)
  })

  it('accepts a complete envelope and reports the failing stage', () => {
    const envelope = availabilityFailure({
      stage: 'transport',
      rootCause: 'Gateway is not running',
      fix: 'start the Gateway with the configured port',
      fallback: { kind: 'unavailable', summary: 'no Blender session is available' },
      evidence: { port: 39877 },
    })
    expect(validateAvailabilityEnvelope(envelope)).toEqual([])
    expect(envelope.stage).toBe('transport')
    expect(envelope.ok).toBe(false)
  })

  it('rejects a missing required field', () => {
    const envelope: Record<string, unknown> = {
      stage: 'addon',
      root_cause: 'no reply',
      fix: 'restart Blender',
      fallback: { kind: 'unavailable', summary: 'not usable' },
      evidence: {},
    }
    delete envelope.fix
    expect(validateAvailabilityEnvelope(envelope)).toContain('fix must be a non-empty string')
  })

  it('rejects an unknown stage instead of folding it into a generic failure', () => {
    expect(
      validateAvailabilityEnvelope({
        stage: 'unknown',
        root_cause: 'x',
        fix: 'y',
        fallback: { kind: 'unavailable', summary: 'z' },
        evidence: {},
      }),
    ).toContain('stage is not in config/availability.json: unknown')
  })

  it('normalizes a legacy route failure into the same envelope', () => {
    const normalized = normalizeAvailability({
      ok: false,
      error: 'Gateway port 39877 is occupied',
      code: 'port_conflict',
    }) as Record<string, unknown>
    expect(normalized.stage).toBe('config')
    expect(normalized.root_cause).toContain('occupied')
    expect(normalized.fix).toBeTruthy()
    expect(validateAvailabilityEnvelope(normalized)).toEqual([])
  })

  it('prefers an explicit diagnosis stage over the code heuristic', () => {
    const normalized = normalizeAvailability({
      ok: false,
      error: 'no reply',
      code: 'backend_error',
      diagnosis: { stage: 'handshake' },
    }) as Record<string, unknown>
    expect(normalized.stage).toBe('handshake')
  })

  it('rejects an unknown fallback kind', () => {
    expect(
      validateAvailabilityEnvelope({
        stage: 'config',
        root_cause: 'x',
        fix: 'y',
        fallback: { kind: 'guess', summary: 'z' },
        evidence: {},
      }),
    ).toContain('fallback.kind is not in config/availability.json: guess')
  })
})
