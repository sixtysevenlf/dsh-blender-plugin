import { describe, expect, it, vi } from 'vitest'
import { createPlanCallInner, type PlanCallDeps } from '../../../runtime/core/dispatcher/index.js'
import {
  prepareEvidencePayload,
  routeMainPlanOps,
} from '../../../runtime/core/dispatcher/plan-call.js'

const ensureNames = [
  'ensureAssembly',
  'ensureAudit',
  'ensureBlockout',
  'ensureCalib',
  'ensureClearance',
  'ensureContract',
  'ensureDeliver',
  'ensureEvalharness',
  'ensureFace',
  'ensureFix',
  'ensureGate',
  'ensureGenerator',
  'ensureHuman',
  'ensureImg',
  'ensureMaterial',
  'ensureMontage',
  'ensureMotion',
  'ensurePipeline',
  'ensurePlanner',
  'ensurePrint',
  'ensureQc',
  'ensureQcRender',
  'ensureSculpt',
  'ensureShape',
  'ensureShapesolve',
  'ensureSurface',
  'ensureSweep',
  'ensureUv',
  'ensureVehicle',
  'ensureView',
  'ensureVisualReport',
] as const

function makeDeps() {
  const sends: Array<{ op: unknown; payload: unknown; timeout: unknown }> = []
  const send = vi.fn(async (op: unknown, payload: unknown, timeout: unknown) => {
    sends.push({ op, payload, timeout })
    return { result: 'LOOP {"ok":true}' }
  })
  const ensures = Object.fromEntries(
    ensureNames.map((name) => [name, vi.fn(async () => undefined)]),
  ) as Record<(typeof ensureNames)[number], ReturnType<typeof vi.fn>>
  const deps = {
    SESSION_NAME: 'unit-test',
    addon: { send } as unknown as PlanCallDeps['addon'],
    ...ensures,
    jobStart: vi.fn(() => ({ id: 'j1' })),
    readModuleSource: vi.fn(() => ''),
  } as unknown as PlanCallDeps
  return { deps, send, sends, ensures }
}

describe('dispatcher F1.1 regression guards', () => {
  it('routes montage through the direct montage API, not generic dispatch', async () => {
    const { deps, sends, ensures } = makeDeps()
    const decision = await routeMainPlanOps(deps, 'montage_create', { label: 'x' })
    expect(decision.matched).toBe(true)
    await (decision as { result: Promise<unknown> }).result
    expect(ensures.ensureMontage).toHaveBeenCalledTimes(1)
    expect(sends).toHaveLength(1)
    const code = String((sends[0].payload as Record<string, unknown>).code)
    expect(code).toContain('K.dsh_montage_api["montage"]')
    expect(code).not.toContain('K.dsh_montage_api["dispatch"]')
  })

  it('ensures view before preparing the evidence payload', async () => {
    const { deps, ensures } = makeDeps()
    const prepared = (await prepareEvidencePayload(deps, 'evidence', {})) as Record<string, unknown>
    expect(ensures.ensureView).toHaveBeenCalledTimes(1)
    expect((prepared.view as Record<string, unknown>).path).toContain('dsh_evidence_unit-test')
  })

  it('ensures view before the evidence contract route sends code', async () => {
    const { deps, ensures, sends } = makeDeps()
    const out = await createPlanCallInner(deps).planCallInner('evidence', {})
    expect(ensures.ensureView).toHaveBeenCalledTimes(1)
    expect(sends).toHaveLength(1)
    const code = String((sends[0].payload as Record<string, unknown>).code)
    expect(code).toContain('dsh_contract_api["dispatch"]')
    expect(out).toMatchObject({ ok: true })
  })
})
