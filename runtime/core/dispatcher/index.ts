// plan/EXT 前缀路由入口（拆分自 dispatch.ts）
export interface PlanCallDeps {
  SESSION_NAME: string
  addon: import('../../transport/probes.js').AddonClient
  ensureAssembly: () => Promise<unknown>
  ensureAudit: () => Promise<unknown>
  ensureBlockout: () => Promise<unknown>
  ensureCalib: () => Promise<unknown>
  ensureClearance: () => Promise<unknown>
  ensureContract: () => Promise<unknown>
  ensureDeliver: () => Promise<unknown>
  ensureEvalharness: () => Promise<unknown>
  ensureFace: () => Promise<unknown>
  ensureFix: () => Promise<unknown>
  ensureGate: () => Promise<unknown>
  ensureGenerator: () => Promise<unknown>
  ensureHuman: () => Promise<unknown>
  ensureImg: () => Promise<unknown>
  ensureMaterial: () => Promise<unknown>
  ensureMontage: () => Promise<unknown>
  ensureMotion: () => Promise<unknown>
  ensurePipeline: () => Promise<unknown>
  ensurePlanner: () => Promise<unknown>
  ensurePrint: () => Promise<unknown>
  ensureQc: () => Promise<unknown>
  ensureQcRender: () => Promise<unknown>
  ensureSculpt: () => Promise<unknown>
  ensureShape: () => Promise<unknown>
  ensureShapesolve: () => Promise<unknown>
  ensureSurface: () => Promise<unknown>
  ensureSweep: () => Promise<unknown>
  ensureUv: () => Promise<unknown>
  ensureVehicle: () => Promise<unknown>
  ensureView: () => Promise<unknown>
  ensureVisualReport: () => Promise<unknown>
  jobStart: (opts: Record<string, unknown>) => Record<string, unknown>
  readModuleSource: (name: string) => string
  preloadChunk: (name: string) => string
}

import { prepareEvidencePayload, routeMainPlanOps, routeModulePlanOps } from './plan-call.js'
import { routeExtRouting } from './ext-routing.js'

async function routePlanCall(E: PlanCallDeps, op: unknown, payload: unknown): Promise<unknown> {
  const o = String(op || 'status')
  const withEvidence = await prepareEvidencePayload(E, o, payload)
  const main = await routeMainPlanOps(E, o, withEvidence)
  if (main.matched) return main.result
  const moduleRoute = await routeModulePlanOps(E, o, withEvidence)
  if (moduleRoute.matched) return moduleRoute.result
  const ext = await routeExtRouting(E, o, withEvidence)
  if (!ext.matched) return { ok: false, op: o, error: 'no route for ' + o }
  return ext.result
}

export function createPlanCallInner(E: PlanCallDeps) {
  return {
    planCallInner: (op: unknown, payload: unknown) => routePlanCall(E, op, payload),
  }
}
