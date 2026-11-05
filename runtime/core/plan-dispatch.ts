import { catalogPayload, extractLoop } from './output/index.js'
import { KERNEL_BOOTSTRAP } from './kernel/index.js'
import { planOpSuggestion } from '../catalog/index.js'
import { extCheckOp } from './plan/ext-check.js'
import { createRenderGuardOp } from './plan/render-guard.js'
import { createShapeSearch } from './plan/shape-search/index.js'
import type { AddonClient } from '../transport/probes.js'

export interface PlanDispatchDeps {
  RENDER_FLAG_WSL: string
  RENDER_STALE_MS: number
  addon: AddonClient
  clearRenderFlag: () => boolean
  ensurePerf: () => Promise<unknown>
  ensureQc: () => Promise<unknown>
  ensureRenderGuard: () => Promise<unknown>
  ensureRunner: () => Promise<unknown>
  ensureView: () => Promise<unknown>
  loopBoard: (top: number, withVariants: boolean) => Promise<unknown>
  loopExport: (spec: Record<string, unknown>) => Promise<unknown>
  loopStart: (spec: Record<string, unknown>) => Promise<unknown>
  loopStatus: (limit: number) => Promise<Record<string, unknown> | null>
  loopStop: () => Promise<unknown>
  planCallInner: (op: string, payload?: unknown) => Promise<unknown>
  readRenderFlag: () => {
    present: boolean
    stale: boolean
    ageMs: number | null
    info: Record<string, unknown> | null
  }
}

export function createPlanDispatch(E: PlanDispatchDeps) {
  const {
    RENDER_FLAG_WSL,
    RENDER_STALE_MS,
    addon,
    clearRenderFlag,
    ensurePerf,
    ensureQc,
    ensureRenderGuard,
    ensureRunner,
    ensureView,
    loopBoard,
    loopExport,
    loopStart,
    loopStatus,
    loopStop,
    planCallInner,
    readRenderFlag,
  } = E
  async function perfCall(op: string, payload?: unknown): Promise<unknown> {
    await ensurePerf()
    const body =
      'import json as _json' +
      '\n' +
      'print("LOOP " + K.dsh_perf_api["dispatch"](' +
      JSON.stringify(op) +
      ', ' +
      JSON.stringify(JSON.stringify(payload === undefined ? {} : payload)) +
      '))'
    return extractLoop(
      await addon.send('execute_code', { code: KERNEL_BOOTSTRAP + '\n' + body }, 300000),
    )
  }
  /** v0.9.6：把模块返回里的 unknown op 变成"一步改对"——补 did_you_mean + 最小骨架 */
  function enhancePlanResult(out: unknown, o: string): unknown {
    let obj: unknown = out
    let asString = false
    if (typeof out === 'string') {
      try {
        obj = JSON.parse(out) as Record<string, unknown>
        asString = true
      } catch (e) {
        return out
      }
    }
    if (!obj || typeof obj !== 'object') return out
    const rec = obj as Record<string, unknown>
    if (rec.ok !== false) return out
    const err = String(rec.error || '')
    if (!/unknown|不认识|not found|参数不匹配/i.test(err)) return out
    const near = planOpSuggestion(o)
    if (near) {
      rec.did_you_mean = near
      rec.hint =
        '回执里有 did_you_mean：直接用这个名字重发，别猜参数——骨架见 did_you_mean.skeleton。'
      if (rec.hint_shown === undefined) rec.hint_shown = true
    }
    return asString ? JSON.stringify(rec) : rec
  }

  /**
   * 契约层 / 规划器统一调用：op = 契约 op（status、register_component、check_envelope、destructive_guard、verify、flip …）
   * 或 plan_<op>（load/validate/order/build/graph/status/help）。
   * 两个 python 模块都提供 dispatch(op, args)，因此这里只发 {op, args}。
   *
   * v0.9.6（可发现性）：op="catalog" 在**本地**直接返回目录（不占 Blender 往返），
   * 其余结果统一过 enhancePlanResult（未知 op → 最近邻 + 骨架）。
   */
  async function planCall(op: unknown, payload?: unknown): Promise<unknown> {
    const o = String(op || 'status')
    if (o === 'catalog' || o === 'catalog_help' || o === 'help_all') return catalogPayload(payload)
    // v0.9.6：渲染状态跟踪的 op 在**本地**处理（读磁盘标记，不占 Blender 往返）
    // -③：外部通路（资产门 glTF-Validator / 第二交通路 open3d）—— 本地 spawn，不占 Blender
    if (o === 'gltf_validate' || o === 'ext_mesh_check') return await extCheckOp(o, payload)
    if (
      o === 'render_state' ||
      o === 'render_wait' ||
      o === 'render_reset' ||
      o === 'render_guard_install' ||
      o === 'render_guard_selftest'
    ) {
      return await renderGuardOp(o, payload)
    }
    // 声明式搜索：目标 + 参数区间 → 内环全流程（spec 由 shape_search 生成；本地编排，不占额外往返）
    if (o === 'shape_search') return await shapeSearchOp(payload)
    const out = await planCallInner(o, payload)
    try {
      return enhancePlanResult(out, o)
    } catch (e) {
      return out
    }
  }
  const renderGuardOp = createRenderGuardOp({
    RENDER_FLAG_WSL,
    RENDER_STALE_MS,
    addon,
    clearRenderFlag,
    ensureRenderGuard,
    readRenderFlag,
  })
  const shapeSearchOp = createShapeSearch({
    addon,
    ensureQc,
    ensureRunner,
    ensureView,
    loopBoard,
    loopExport,
    loopStart,
    loopStatus,
    loopStop,
  })
  return { perfCall, enhancePlanResult, planCall }
}
