// plan/qc/audit 前缀路由（F1.1 从 dispatch.ts 拆出，行为冻结）
import { WIN_TMP, blenderJoin } from '../../paths.js'
import { extractLoop } from '../output/index.js'
import { KERNEL_BOOTSTRAP } from '../kernel/index.js'
import type { PlanCallDeps } from './index.js'

export type RouteDecision = { matched: true; result: Promise<unknown> } | { matched: false }

export const hit = (result: unknown): RouteDecision => ({
  matched: true,
  result: Promise.resolve(result),
})
export const miss = (): RouteDecision => ({ matched: false })

/** evidence：先注入默认 view.path 再走契约层 */
export async function prepareEvidencePayload(
  E: PlanCallDeps,
  o: string,
  payload: unknown,
): Promise<unknown> {
  if (o !== 'evidence') return payload
  await E.ensureView()
  const { SESSION_NAME } = E
  const p: Record<string, unknown> =
    payload !== null && typeof payload === 'object' ? (payload as Record<string, unknown>) : {}
  const viewRec: Record<string, unknown> =
    p.view !== null && typeof p.view === 'object' ? (p.view as Record<string, unknown>) : {}
  if (!viewRec.path)
    viewRec.path = blenderJoin(
      WIN_TMP,
      'dsh_evidence_' + SESSION_NAME.replace(/[^A-Za-z0-9_.-]/g, '_') + '.png',
    )
  p.view = viewRec
  return p
}

/** 通用：把 op 派给 Blender 侧内核模块的 dispatch(op, json) */
export async function dispatchViaKernelModule(
  E: PlanCallDeps,
  ensure: () => Promise<unknown>,
  api: string,
  subOp: string,
  payload: unknown,
  timeoutMs: number,
): Promise<unknown> {
  await ensure()
  const body =
    'print("LOOP " + K.' +
    api +
    '["dispatch"](' +
    JSON.stringify(subOp) +
    ', _json.dumps(_json.loads(' +
    JSON.stringify(JSON.stringify(payload || {})) +
    '))))'
  return extractLoop(
    await E.addon.send(
      'execute_code',
      { code: KERNEL_BOOTSTRAP + '\nimport json as _json\n' + body },
      timeoutMs,
    ),
  )
}

async function handlePlan(E: PlanCallDeps, o: string, payload: unknown): Promise<unknown> {
  await E.ensurePlanner()
  const body =
    'print("LOOP " + K.dsh_plan_api["dispatch"](' +
    JSON.stringify(o.slice(5)) +
    ', _json.dumps(_json.loads(' +
    JSON.stringify(JSON.stringify(payload || {})) +
    '))))'
  return extractLoop(
    await E.addon.send(
      'execute_code',
      { code: KERNEL_BOOTSTRAP + '\nimport json as _json\n' + body },
      300000,
    ),
  )
}

async function handleQcRender(E: PlanCallDeps, o: string, payload: unknown): Promise<unknown> {
  const p: Record<string, unknown> =
    payload !== null && typeof payload === 'object' ? (payload as Record<string, unknown>) : {}
  if (p.asJob) {
    const { preloadChunk, jobStart } = E
    const mods = ['qc', 'qc_render'].map(preloadChunk).join(String.fromCharCode(10))
    const driver =
      'import json as _json' +
      String.fromCharCode(10) +
      'print("HEADLESS " + K.dsh_qc_render_api["render_views"](_json.loads(' +
      JSON.stringify(JSON.stringify(p)) +
      ')))'
    const j = jobStart({
      file: p.file,
      outdir: p.outdir || WIN_TMP,
      engine: p.engine && String(p.engine) !== 'keep' ? String(p.engine) : 'eevee',
      timeoutMs: Number(p.timeoutMs) || 3600000,
      script: mods + String.fromCharCode(10) + driver,
    })
    return {
      ok: true,
      mode: 'job',
      jobId: j.id,
      ms: 0,
      outdir: j.outdir,
      logDir: j.logDir,
      jsonl: blenderJoin(String(p.outdir || WIN_TMP), 'render_views.jsonl'),
      job: j,
      hint:
        '长活已转作业层：blender_rt_job(op="status"/"collect", id="' +
        j.id +
        '") 跟进；产物与日志在 outdir/jobs/ 下',
    }
  }
  await E.ensureQcRender()
  const body =
    'print("LOOP " + K.dsh_qc_render_api["dispatch"](' +
    JSON.stringify(o.slice(3)) +
    ', _json.dumps(_json.loads(' +
    JSON.stringify(JSON.stringify(p)) +
    '))))'
  return extractLoop(
    await E.addon.send(
      'execute_code',
      { code: KERNEL_BOOTSTRAP + '\nimport json as _json\n' + body },
      900000,
    ),
  )
}

async function handleMontage(E: PlanCallDeps, payload: unknown): Promise<unknown> {
  await E.ensureMontage()
  const body =
    'print("LOOP " + K.dsh_montage_api["montage"](_json.loads(' +
    JSON.stringify(JSON.stringify(payload || {})) +
    ')))'
  return extractLoop(
    await E.addon.send(
      'execute_code',
      { code: KERNEL_BOOTSTRAP + '\nimport json as _json\n' + body },
      300000,
    ),
  )
}

export async function routeMainPlanOps(
  E: PlanCallDeps,
  o: string,
  payload: unknown,
): Promise<RouteDecision> {
  if (o.startsWith('plan_')) return hit(handlePlan(E, o, payload))
  if (o.indexOf('qc_render') === 0) return hit(handleQcRender(E, o, payload))
  if (o.indexOf('qc_') === 0)
    return hit(dispatchViaKernelModule(E, E.ensureQc, 'dsh_qc_api', o.slice(3), payload, 300000))
  if (o === 'audit_clash_all' || o.indexOf('audit_clash_all_') === 0)
    return hit(
      dispatchViaKernelModule(E, E.ensureAssembly, 'dsh_assembly_api', 'scan', payload, 900000),
    )
  if (o.indexOf('audit_') === 0)
    return hit(
      dispatchViaKernelModule(E, E.ensureAudit, 'dsh_audit_api', o.slice(6), payload, 600000),
    )
  if (o.indexOf('montage') === 0) return hit(handleMontage(E, payload))
  return miss()
}

async function handleMotion(E: PlanCallDeps, o: string, payload: unknown): Promise<unknown> {
  await E.ensureMotion()
  const name = o.slice(7)
  const body = [
    'import json as _json',
    'A = K.dsh_motion_api',
    'P = _json.loads(' + JSON.stringify(JSON.stringify(payload || {})) + ')',
    'if isinstance(P, dict) and isinstance(P.get("args"), dict): P = P["args"]',
    'print("LOOP " + (A["dispatch"](' +
      JSON.stringify(name) +
      ', _json.dumps(P)) if A.get("dispatch") else A[' +
      JSON.stringify(name) +
      '](**P)))',
  ].join(String.fromCharCode(10))
  return extractLoop(
    await E.addon.send('execute_code', { code: KERNEL_BOOTSTRAP + '\n' + body }, 900000),
  )
}

async function handleDeliver(E: PlanCallDeps, o: string, payload: unknown): Promise<unknown> {
  await E.ensureDeliver()
  const name = o.slice(8)
  const body = [
    'import json as _json',
    'A = K.dsh_deliver_api',
    'P = _json.loads(' + JSON.stringify(JSON.stringify(payload || {})) + ')',
    'if isinstance(P, dict) and isinstance(P.get("args"), dict): P = P["args"]',
    'print("LOOP " + (A["dispatch"](' +
      JSON.stringify(name) +
      ', _json.dumps(P)) if A.get("dispatch") else A[' +
      JSON.stringify(name) +
      '](**P)))',
  ].join(String.fromCharCode(10))
  return extractLoop(
    await E.addon.send('execute_code', { code: KERNEL_BOOTSTRAP + '\n' + body }, 300000),
  )
}

async function handleAssemblyAlias(
  E: PlanCallDeps,
  subOp: string,
  payload: unknown,
): Promise<unknown> {
  await E.ensureAssembly()
  const body =
    'print("LOOP " + K.dsh_assembly_api["dispatch"](' +
    JSON.stringify(subOp) +
    ', _json.dumps(_json.loads(' +
    JSON.stringify(JSON.stringify(payload || {})) +
    '))))'
  return extractLoop(
    await E.addon.send(
      'execute_code',
      { code: KERNEL_BOOTSTRAP + '\nimport json as _json\n' + body },
      900000,
    ),
  )
}

export async function routeModulePlanOps(
  E: PlanCallDeps,
  o: string,
  payload: unknown,
): Promise<RouteDecision> {
  if (o.indexOf('motion_') === 0) return hit(handleMotion(E, o, payload))
  if (o.indexOf('deliver_') === 0) return hit(handleDeliver(E, o, payload))
  if (o.indexOf('generator_') === 0)
    return hit(
      dispatchViaKernelModule(
        E,
        E.ensureGenerator,
        'dsh_generator_api',
        o.slice(10),
        payload,
        600000,
      ),
    )
  if (o === 'fix_clash' || o === 'fix_interference') {
    const sub = o === 'fix_clash' ? 'repair' : 'fix_interference'
    return hit(handleAssemblyAlias(E, sub, payload))
  }
  if (o === 'qc_visual_report') {
    await E.ensureVisualReport()
    const body =
      'print("LOOP " + K.dsh_visualreport_api["dispatch"]("report", _json.dumps(_json.loads(' +
      JSON.stringify(JSON.stringify(payload || {})) +
      '))))'
    return hit(
      extractLoop(
        await E.addon.send(
          'execute_code',
          { code: KERNEL_BOOTSTRAP + '\nimport json as _json\n' + body },
          600000,
        ),
      ),
    )
  }
  if (o === 'shape_solve') {
    await E.ensureShapesolve()
    const body =
      'print("LOOP " + K.dsh_shapesolve_api["dispatch"]("solve", _json.dumps(_json.loads(' +
      JSON.stringify(JSON.stringify(payload || {})) +
      '))))'
    return hit(
      extractLoop(
        await E.addon.send(
          'execute_code',
          { code: KERNEL_BOOTSTRAP + '\nimport json as _json\n' + body },
          900000,
        ),
      ),
    )
  }
  if (o === 'model_preflight' || o === 'model_checkpoint' || o === 'model_deliver')
    return hit(
      dispatchViaKernelModule(
        E,
        E.ensureGate,
        'dsh_gate_api',
        o.slice('model_'.length),
        payload,
        1800000,
      ),
    )
  return miss()
}
