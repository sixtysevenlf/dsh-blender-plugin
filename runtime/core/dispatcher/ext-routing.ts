// EXT 前缀路由 + gui/render/contract 兜底（F1.1 从 dispatch.ts 拆出，行为冻结）
import { extractLoop } from '../output/index.js'
import { KERNEL_BOOTSTRAP } from '../kernel/index.js'
import type { PlanCallDeps } from './index.js'
import type { RouteDecision } from './plan-call.js'
import { hit } from './plan-call.js'

interface ExtRoute {
  p: string
  n: number
  ensure: () => Promise<unknown>
  api: string
  ms: number
}

/** v0.9.6（上游整合 A1–A5）：EXT_ROUTES 表（每个模块暴露 K.dsh_<x>_api["dispatch"](op, json_str)） */
function buildExtRoutes(E: PlanCallDeps): ExtRoute[] {
  return [
    { p: 'gate_', n: 5, ensure: E.ensureGate, api: 'dsh_gate_api', ms: 1800000 },
    { p: 'img_', n: 4, ensure: E.ensureImg, api: 'dsh_img_api', ms: 300000 },
    { p: 'face_', n: 5, ensure: E.ensureFace, api: 'dsh_face_api', ms: 300000 },
    { p: 'human_', n: 6, ensure: E.ensureHuman, api: 'dsh_human_api', ms: 900000 },
    { p: 'clear_', n: 6, ensure: E.ensureClearance, api: 'dsh_clearance_api', ms: 600000 },
    { p: 'vehicle_', n: 8, ensure: E.ensureVehicle, api: 'dsh_vehicle_api', ms: 900000 },
    { p: 'shape_', n: 6, ensure: E.ensureShape, api: 'dsh_shape_api', ms: 900000 },
    { p: 'pipe_', n: 5, ensure: E.ensurePipeline, api: 'dsh_pipe_api', ms: 600000 },
    { p: 'asm_', n: 4, ensure: E.ensureAssembly, api: 'dsh_assembly_api', ms: 900000 },
    { p: 'blockout_', n: 9, ensure: E.ensureBlockout, api: 'dsh_blockout_api', ms: 900000 },
    {
      p: 'visualreport_',
      n: 13,
      ensure: E.ensureVisualReport,
      api: 'dsh_visualreport_api',
      ms: 600000,
    },
    {
      p: 'shapesolve_',
      n: 11,
      ensure: E.ensureShapesolve,
      api: 'dsh_shapesolve_api',
      ms: 900000,
    },
    { p: 'eval_', n: 5, ensure: E.ensureEvalharness, api: 'dsh_evalharness_api', ms: 900000 },
    { p: 'surface_', n: 8, ensure: E.ensureSurface, api: 'dsh_surface_api', ms: 900000 },
    { p: 'calib_', n: 6, ensure: E.ensureCalib, api: 'dsh_calib_api', ms: 1800000 },
    { p: 'material_', n: 9, ensure: E.ensureMaterial, api: 'dsh_material_api', ms: 900000 },
    { p: 'sculpt_', n: 7, ensure: E.ensureSculpt, api: 'dsh_sculpt_api', ms: 900000 },
    { p: 'fix_', n: 4, ensure: E.ensureFix, api: 'dsh_fix_api', ms: 600000 },
    { p: 'uv_', n: 3, ensure: E.ensureUv, api: 'dsh_uv_api', ms: 600000 },
    { p: 'print_', n: 6, ensure: E.ensurePrint, api: 'dsh_print_api', ms: 900000 },
    { p: 'sweep_', n: 6, ensure: E.ensureSweep, api: 'dsh_sweep_api', ms: 300000 },
  ]
}

async function handleExtRoute(
  E: PlanCallDeps,
  o: string,
  payload: unknown,
): Promise<RouteDecision> {
  for (const ext of buildExtRoutes(E)) {
    if (o.indexOf(ext.p) === 0) {
      await ext.ensure()
      const body =
        'print("LOOP " + K.' +
        ext.api +
        '["dispatch"](' +
        JSON.stringify(o.slice(ext.n)) +
        ', _json.dumps(_json.loads(' +
        JSON.stringify(JSON.stringify(payload || {})) +
        '))))'
      return hit(
        extractLoop(
          await E.addon.send(
            'execute_code',
            { code: KERNEL_BOOTSTRAP + '\nimport json as _json\n' + body },
            ext.ms,
          ),
        ),
      )
    }
  }
  return { matched: false }
}

async function handleGui(E: PlanCallDeps, o: string, payload: unknown): Promise<unknown> {
  await E.ensureView()
  const name = o.slice(4)
  const body =
    'print("LOOP " + K.dsh_view_api[' +
    JSON.stringify('gui_' + name) +
    '](**_json.loads(' +
    JSON.stringify(JSON.stringify(payload || {})) +
    ')))'
  return extractLoop(
    await E.addon.send(
      'execute_code',
      { code: KERNEL_BOOTSTRAP + '\nimport json as _json\n' + body },
      120000,
    ),
  )
}

async function handleRender(E: PlanCallDeps, o: string, payload: unknown): Promise<unknown> {
  await E.ensureQcRender()
  // 路由契约测试发现：render_lock_status 直切会变成 qc_render 不认识的 lock_status（白名单里却算只读）。
  // qc_render 的锁查询 op 就叫 status（render_status 就是这么通的）=> 别名显式归一。
  const rsub = o.slice(7) === 'lock_status' ? 'status' : o.slice(7)
  const body =
    'print("LOOP " + K.dsh_qc_render_api["dispatch"](' +
    JSON.stringify(rsub) +
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

async function handleContract(E: PlanCallDeps, o: string, payload: unknown): Promise<unknown> {
  await E.ensureContract()
  const body =
    'print("LOOP " + K.dsh_contract_api["dispatch"](' +
    JSON.stringify(o) +
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

export async function routeExtRouting(
  E: PlanCallDeps,
  o: string,
  payload: unknown,
): Promise<RouteDecision> {
  const ext = await handleExtRoute(E, o, payload)
  if (ext.matched) return ext
  if (o.indexOf('gui_') === 0) return hit(handleGui(E, o, payload))
  if (o.indexOf('render_') === 0) return hit(handleRender(E, o, payload))
  return hit(handleContract(E, o, payload))
}
