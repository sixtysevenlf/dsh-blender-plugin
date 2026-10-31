import { KERNEL_BOOTSTRAP } from '../kernel.js'
import { errorMessage } from '../json.js'
import type { AddonClient } from '../../transport/probes.js'

export interface ShapeSearchDeps {
  addon: AddonClient
  ensureQc: () => Promise<unknown>
  ensureRunner: () => Promise<unknown>
  ensureView: () => Promise<unknown>
  loopBoard: (top: number, withVariants: boolean) => Promise<unknown>
  loopExport: (spec: Record<string, unknown>) => Promise<unknown>
  loopStart: (spec: Record<string, unknown>) => Promise<unknown>
  loopStatus: (limit: number) => Promise<Record<string, unknown> | null>
  loopStop: () => Promise<unknown>
}

/** Declarative search: validate a spec locally, then orchestrate the Blender-side loop. */
export function createShapeSearch(
  E: ShapeSearchDeps,
): (payload?: unknown) => Promise<Record<string, unknown>> {
  const {
    addon,
    ensureQc,
    ensureRunner,
    ensureView,
    loopBoard,
    loopExport,
    loopStart,
    loopStatus,
    loopStop,
  } = E
  const SHAPE_SEARCH_SKELETON =
    'blender_rt_plan(op="shape_search", args={objective:"silhouette_iou", ' +
    'ref:"D:/ref/side.png", view:{from:[0,-6,1.0], look_at:[0,0,0.5], lens:50}, ' +
    "apply:\"ob.location.y = p['dy_mm']/1000.0\\n    ob.location.z = p['dz_mm']/1000.0\\n" +
    '    bpy.context.view_layer.update()", ' +
    'params:{dy_mm:{min:-30,max:30,step:5}, dz_mm:{min:-20,max:20,step:5}}, ' +
    'iterations:200, budget_ms:30000, top_k:10})'

  /** Python 字面量（JSON 的 true/false/null 在 Python 里不是这个写法 —— 直接 JSON.stringify 会 NameError） */
  function pyLit(v: unknown): string {
    if (v === null || v === undefined) return 'None'
    if (typeof v === 'boolean') return v ? 'True' : 'False'
    if (typeof v === 'number') return isFinite(v) ? String(v) : 'None'
    if (typeof v === 'string') return JSON.stringify(v)
    if (Array.isArray(v)) return '[' + (v as unknown[]).map(pyLit).join(', ') + ']'
    if (typeof v === 'object') {
      const obj = v as Record<string, unknown>
      return (
        '{' +
        Object.keys(obj)
          .map((k) => JSON.stringify(k) + ': ' + pyLit(obj[k]))
          .join(', ') +
        '}'
      )
    }
    return JSON.stringify(String(v))
  }
  const pyIndent = (src: unknown, pad: string): string =>
    String(src)
      .split('\n')
      .map((l) => (l.length ? pad + l : l))
      .join('\n')

  /** 一个轴（参数维度）的候选值：enum 直接取 values；range 按 step 切（没给 step 就等分 10 档） */
  interface AxisSpec {
    kind: 'enum' | 'range'
    values?: unknown[]
    min?: number
    max?: number
    step?: number
    integer?: boolean
  }
  function searchAxisValues(spec0: AxisSpec, cap: number): unknown[] {
    if (spec0.kind === 'enum') return (spec0.values || []).slice(0, cap)
    const out: unknown[] = []
    const lo = Number(spec0.min) || 0
    const hi = Number(spec0.max) || 0
    const stepN = Number(spec0.step)
    const step = stepN > 0 ? stepN : (hi - lo) / 9
    if (!(step > 0)) return [lo]
    for (let v = lo; v <= hi + 1e-12 && out.length < cap; v += step) {
      out.push(spec0.integer ? Math.round(v) : Number(v.toPrecision(12)))
    }
    if (out.length < cap && out[out.length - 1] !== hi) out.push(hi)
    return out.length ? out : [lo]
  }

  async function shapeSearchOp(payload?: unknown): Promise<Record<string, unknown>> {
    const p =
      payload && typeof payload === 'object' && !Array.isArray(payload)
        ? (payload as Record<string, unknown>)
        : {}
    const OBJECTIVES = ['silhouette_iou', 'profile_err', 'aabb_err']
    const objective = String(p.objective || 'silhouette_iou')
    if (OBJECTIVES.indexOf(objective) < 0) {
      return {
        ok: false,
        op: 'shape_search',
        stage: 'validate',
        error: 'objective 只支持 ' + OBJECTIVES.join(' / '),
        got: objective,
        skeleton: SHAPE_SEARCH_SKELETON,
      }
    }
    const rawParams = p.params
    if (
      !rawParams ||
      typeof rawParams !== 'object' ||
      Array.isArray(rawParams) ||
      Object.keys(rawParams as Record<string, unknown>).length === 0
    ) {
      return {
        ok: false,
        op: 'shape_search',
        stage: 'validate',
        error: 'params 必填：{参数名: {min,max(,step)} 或 {values:[...]}}',
        got: rawParams === undefined ? null : rawParams,
        skeleton: SHAPE_SEARCH_SKELETON,
      }
    }
    const norm: Record<string, AxisSpec> = {}
    const problems: string[] = []
    const rawParamsRec = rawParams as Record<string, unknown>
    for (const key of Object.keys(rawParamsRec)) {
      const v = rawParamsRec[key]
      if (!v || typeof v !== 'object') {
        problems.push(key + ': 要 {min,max} 或 {values:[...]}')
        continue
      }
      const vv = v as Record<string, unknown>
      if (Array.isArray(vv.values)) {
        if (!vv.values.length) {
          problems.push(key + ': values 为空')
          continue
        }
        norm[key] = { kind: 'enum', values: vv.values.slice(0, 500) }
        continue
      }
      const lo = Number(vv.min),
        hi = Number(vv.max)
      if (!isFinite(lo) || !isFinite(hi) || hi < lo) {
        problems.push(key + ': min/max 不合法（' + String(vv.min) + ',' + String(vv.max) + '）')
        continue
      }
      const step = Number(vv.step)
      norm[key] = {
        kind: 'range',
        min: lo,
        max: hi,
        step: isFinite(step) && step > 0 ? step : 0,
        integer: !!vv.integer,
      }
    }
    if (problems.length)
      return {
        ok: false,
        op: 'shape_search',
        stage: 'validate',
        error: '参数区间有问题',
        problems: problems,
        skeleton: SHAPE_SEARCH_SKELETON,
      }

    const iterationsReq = Math.max(1, Math.min(200000, Number(p.iterations) || 200))
    const budgetMs = Math.max(1000, Math.min(600000, Number(p.budget_ms) || 30000))
    const topK = Math.max(0, Math.min(200, p.top_k === undefined ? 10 : Number(p.top_k)))
    const patience = Math.max(0, Number(p.patience) || 0)
    const minimize = p.minimize === undefined ? true : !!p.minimize
    const interval = Math.max(0, Number(p.interval) || 0)
    const measureEvery = Math.max(1, Number(p.measure_every) || 1)

    const axes = Object.keys(norm).map((k) => ({
      k: k,
      spec: norm[k],
      vals: searchAxisValues(norm[k], 600),
    }))
    let combos = 1
    for (const a of axes) combos *= a.vals.length
    let strategy = String(p.strategy || 'auto')
    if (strategy === 'auto') strategy = combos <= iterationsReq ? 'grid' : 'random'
    if (strategy !== 'grid' && strategy !== 'random') {
      return {
        ok: false,
        op: 'shape_search',
        stage: 'validate',
        error: 'strategy 只支持 auto / grid / random',
        got: strategy,
        skeleton: SHAPE_SEARCH_SKELETON,
      }
    }
    if (strategy === 'grid' && combos > 20000) {
      return {
        ok: false,
        op: 'shape_search',
        stage: 'validate',
        error: 'grid 组合数 ' + combos + ' 太大（>20000）→ 用 strategy:"random" 或缩小 step',
        combos: combos,
        skeleton: SHAPE_SEARCH_SKELETON,
      }
    }
    const iterations = strategy === 'grid' ? Math.min(iterationsReq, combos) : iterationsReq

    const renderObjective = objective !== 'aabb_err'
    const views = []
    if (Array.isArray(p.views) && p.views.length) {
      for (const v of p.views) views.push(v)
    } else if (p.view && typeof p.view === 'object') views.push(p.view)
    const ref = p.ref ? String(p.ref) : ''
    const refBox = p.ref_box === undefined || p.ref_box === null ? null : p.ref_box
    const bins = Math.max(2, Number(p.bins) || 24)
    const target = p.target ? String(p.target) : ''
    const targetSize = Array.isArray(p.target_size) ? p.target_size.slice(0, 3) : null
    const warnings = []
    if (renderObjective) {
      if (!views.length)
        return {
          ok: false,
          op: 'shape_search',
          stage: 'validate',
          error: objective + ' 需要 view 或 views（机位）',
          skeleton: SHAPE_SEARCH_SKELETON,
        }
      if (!ref)
        return {
          ok: false,
          op: 'shape_search',
          stage: 'validate',
          error: objective + ' 需要 ref（参考图路径）',
          skeleton: SHAPE_SEARCH_SKELETON,
        }
    } else {
      if (!target)
        return {
          ok: false,
          op: 'shape_search',
          stage: 'validate',
          error: 'aabb_err 需要 target（对象名）',
          skeleton: SHAPE_SEARCH_SKELETON,
        }
      if (!targetSize || targetSize.length !== 3)
        return {
          ok: false,
          op: 'shape_search',
          stage: 'validate',
          error: 'aabb_err 需要 target_size:[x,y,z]',
          skeleton: SHAPE_SEARCH_SKELETON,
        }
    }
    const applyBody = p.apply ? String(p.apply) : ''
    if (renderObjective && !applyBody && !p.measure) {
      return {
        ok: false,
        op: 'shape_search',
        stage: 'validate',
        error:
          '渲染类目标需要 apply：每轮把 ns["params"] 落到场景上的代码（改完记得 view_layer.update()）',
        skeleton: SHAPE_SEARCH_SKELETON,
      }
    }
    if (!applyBody && !p.measure)
      warnings.push(
        '没有给 apply：每轮只改 ns["params"]，场景不动 —— 目标必须是纯几何量（如 aabb_err）',
      )

    const setupSrc = (function () {
      /**
       * 代码块归一化（实测踩过：apply 写成"首行顶格 + 续行缩进"时，pyIndent 会把续行多加 4 格
       * ⇒ 生成的 Python 直接 IndentationError；只做字符串断言抓不到，tests/unit/js/shape-search.test.ts
       * 里有一条"用 python3 真编译"的回归）。
       * 两段式：① 按**首行**缩进整体去缩进；② 续行的**公共**缩进再收一次（保留嵌套块的相对层次）。
       */
      const normBody = (src: unknown): string => {
        const lines = String(src).replace(/\t/g, '    ').split('\n')
        const ind = (l: string): number => l.match(/^ */)![0].length
        const firstIdx = lines.findIndex((l) => l.trim())
        if (firstIdx < 0) return lines.join('\n')
        const i0 = ind(lines[firstIdx])
        let out =
          i0 > 0
            ? lines.map((l, k) =>
                k === firstIdx || !l.trim()
                  ? k === firstIdx
                    ? l.slice(i0)
                    : l
                  : l.slice(Math.min(i0, ind(l))),
              )
            : lines.slice()
        const tail = out.slice(firstIdx + 1).filter((l) => l.trim())
        const iTail = tail.length ? Math.min(...tail.map(ind)) : 0
        if (iTail > 0) out = out.map((l, k) => (k > firstIdx && l.trim() ? l.slice(iTail) : l))
        return out.join('\n')
      }
      const parts = ['import bpy', 'import math', 'import random']
      if (p.setup) parts.push(normBody(p.setup))
      parts.push(
        applyBody
          ? 'def apply(p):\n' + pyIndent(normBody(applyBody), '    ')
          : 'def apply(p):\n    pass',
      )
      return parts.join('\n') + '\n'
    })()
    const stepSrc = (function () {
      if (strategy === 'grid') {
        const axesLit =
          '[' +
          axes.map((a) => '[' + JSON.stringify(a.k) + ', ' + pyLit(a.vals) + ']').join(', ') +
          ']'
        return (
          '_axes = ' +
          axesLit +
          '\n_i = ns["i"] % ' +
          String(combos) +
          '\n_p = {}\n' +
          'for _k, _vs in _axes:\n    _p[_k] = _vs[_i % len(_vs)]\n    _i //= len(_vs)\n' +
          'ns["params"] = _p\n'
        )
      }
      const lines = axes.map((a) => {
        if (a.spec.kind === 'enum')
          return '    ' + JSON.stringify(a.k) + ': random.choice(' + pyLit(a.vals) + ')'
        if (a.spec.integer)
          return (
            '    ' +
            JSON.stringify(a.k) +
            ': int(round(random.uniform(' +
            a.spec.min +
            ', ' +
            a.spec.max +
            ')))'
          )
        return (
          '    ' + JSON.stringify(a.k) + ': random.uniform(' + a.spec.min + ', ' + a.spec.max + ')'
        )
      })
      return 'ns["params"] = {\n' + lines.join(',\n') + '\n}\n'
    })()
    const measureSrc = (function () {
      if (p.measure) return (applyBody ? 'apply(ns["params"])\n' : '') + String(p.measure) + '\n'
      const lines = []
      if (applyBody) lines.push('apply(ns["params"])')
      if (objective === 'aabb_err') {
        lines.push(
          'ns["score"] = K.dsh_measure["aabb_err"](' +
            JSON.stringify(target) +
            ', ' +
            pyLit(targetSize) +
            ')',
        )
      } else {
        const fn = objective === 'profile_err' ? 'profile_err' : 'silhouette_iou'
        const extra = fn === 'profile_err' ? ', ' + String(bins) : ''
        const call = (viewLit: string) =>
          'K.dsh_measure[' +
          JSON.stringify(fn) +
          '](' +
          viewLit +
          ', ' +
          JSON.stringify(ref) +
          ', ' +
          pyLit(refBox) +
          extra +
          ')'
        if (views.length === 1) {
          lines.push('ns["score"] = ' + call(pyLit(views[0])))
        } else {
          lines.push('_vs = ' + pyLit(views))
          lines.push('_ss = [' + call('_v') + ' for _v in _vs]')
          lines.push('ns["score"] = sum(_ss) / float(len(_ss))')
          lines.push('ns["metrics"] = {"per_view": _ss}')
        }
      }
      return lines.join('\n') + '\n'
    })()

    const spec = {
      setup: setupSrc,
      step: stepSrc,
      measure: measureSrc,
      iterations: iterations,
      budget_ms: budgetMs,
      interval: interval,
      measure_every: measureEvery,
      minimize: minimize,
      top_k: topK,
      patience: patience,
      redraw_every: 0,
    }
    const specEcho = {
      objective: objective,
      strategy: strategy,
      combos: combos,
      iterations: iterations,
      budget_ms: budgetMs,
      minimize: minimize,
      top_k: topK,
      patience: patience,
      param_names: Object.keys(norm),
      views: views.length,
      ref: ref || null,
      target: target || null,
      restore: String(p.restore || 'best'),
    }
    if (p.dry_run === true) {
      return {
        ok: true,
        op: 'shape_search',
        dry_run: true,
        spec_echo: specEcho,
        spec: spec,
        warnings: warnings,
        note: 'dry_run：只校验并回 spec，没有启动内环（想手跑就把它喂给 blender_rt_loop(op="start", spec=…)）',
      }
    }

    try {
      await ensureRunner()
      if (renderObjective) {
        await ensureQc()
        await ensureView()
      }
    } catch (e) {
      return {
        ok: false,
        op: 'shape_search',
        stage: 'preload',
        error: '注入 runner/qc/view 失败：' + errorMessage(e).slice(0, 240),
        hint: 'GUI Blender 连着吗？本 op 只在 GUI 会话有效（无头下 bpy.app.timers 不触发）',
      }
    }
    let started: Record<string, unknown> | null = null
    try {
      started = (await loopStart(spec)) as Record<string, unknown> | null
    } catch (e) {
      return {
        ok: false,
        op: 'shape_search',
        stage: 'start',
        error: errorMessage(e).slice(0, 300),
        spec_echo: specEcho,
      }
    }
    if (!started || started.ok === false) {
      return {
        ok: false,
        op: 'shape_search',
        stage: 'start',
        spec_echo: specEcho,
        error: (started && (started.err || started.error)) || '内环启动失败',
        detail: started || null,
        hint: '若提示 loop already running：先 blender_rt_loop(op="status") 看上一轮，必要时 op="stop"',
      }
    }

    const waitCap = Math.max(5000, Math.min(600000, Number(p.wait_ms) || budgetMs + 20000))
    const t0 = Date.now()
    let st: Record<string, unknown> | null = null,
      timedOut = false,
      pollError: string | null = null
    try {
      st = await loopStatus(8)
      while (st && st.running === true) {
        if (Date.now() - t0 > waitCap) {
          timedOut = true
          await loopStop()
          await new Promise<void>((r) => setTimeout(r, 300))
          st = await loopStatus(8)
          break
        }
        await new Promise<void>((r) => setTimeout(r, 300))
        st = await loopStatus(8)
      }
    } catch (e) {
      pollError = errorMessage(e).slice(0, 300)
    }

    let board: Record<string, unknown> | null = null,
      exported: Record<string, unknown> | null = null
    try {
      board = (await loopBoard(topK > 0 ? topK : 10, false)) as Record<string, unknown> | null
    } catch (e) {
      board = null
    }
    if (p.export) {
      try {
        exported = (await loopExport({
          path: String(p.export),
          top: 1,
          includeVariants: false,
          note: 'shape_search ' + objective,
        })) as Record<string, unknown>
      } catch (e) {
        exported = { ok: false, error: errorMessage(e).slice(0, 200) }
      }
    }

    const best = ((st && st.best) || (board && board.best) || null) as Record<
      string,
      unknown
    > | null
    let applied = null
    if (best && String(p.restore || 'best') !== 'none' && applyBody) {
      try {
        const body =
          'import json as _json\n' +
          'try:\n' +
          pyIndent(setupSrc, '    ') +
          '\n' +
          '    apply(' +
          pyLit(best.params || {}) +
          ')\n' +
          '    print("SEARCH_APPLIED " + _json.dumps({"ok": True, "params": _json.loads(' +
          JSON.stringify(JSON.stringify(best.params || {})) +
          ')}))\n' +
          'except Exception as _e:\n' +
          '    print("SEARCH_APPLIED " + _json.dumps({"ok": False, "error": str(_e)[:200]}))\n'
        const out = await addon.send(
          'execute_code',
          { code: KERNEL_BOOTSTRAP + '\n' + body },
          60000,
        )
        const outRec = (out || null) as Record<string, unknown> | null
        const raw = outRec && typeof outRec.result === 'string' ? outRec.result : ''
        const mm = raw.match(/SEARCH_APPLIED (\{[\s\S]*?\})\s*$/m)
        applied = mm ? JSON.parse(mm[1]) : { ok: false, raw_tail: raw.slice(-200) }
      } catch (e) {
        applied = { ok: false, error: errorMessage(e).slice(0, 200) }
      }
    } else if (best && String(p.restore || 'best') === 'none') {
      applied = {
        ok: false,
        skipped: true,
        note: 'restore:"none" —— 场景仍停在最后一次迭代的参数上，best 在回执里',
      }
    }

    const nextSteps: string[] = []
    if (best)
      nextSteps.push(
        '换另一条通路复核（内环只优化你写的 score）：blender_rt_plan(op="qc_render_views", args={views:[…], ref_path:"…"}) + op="audit_measure" + blender_rt_see',
      )
    if (!p.export)
      nextSteps.push(
        '要落可复现脚本：本 op 加 export:"D:/out/best_fit.py"（或 blender_rt_loop(op="export", path=…)）',
      )
    if (objective === 'aabb_err')
      nextSteps.push(
        'aabb_err 只量包围盒三轴误差：形状/轮廓差异它看不见，必须配 qc_render_views 图上对拍',
      )
    if (objective !== 'aabb_err')
      nextSteps.push('单视轮廓 IoU 是必要不充分（填满轮廓就能刷高）→ 再配间隙/尺寸/特征线证据')
    if (timedOut)
      nextSteps.push(
        'wait_ms 到点已急停：结果仍在（best/board 有效），需要更多迭代就加大 budget_ms 后重跑',
      )

    return {
      ok: !!best,
      op: 'shape_search',
      spec_echo: specEcho,
      stop_reason: (st && st.stop_reason) || (timedOut ? 'wait_timeout' : null),
      timed_out: timedOut,
      iterations_done: st ? st.i : null,
      tps: st ? st.tps : null,
      elapsed_ms: st ? st.elapsed_ms : null,
      best: best,
      board: board ? board.board : null,
      loop_error: st ? st.error : null,
      poll_error: pollError,
      applied: applied,
      exported: exported,
      warnings: warnings,
      next_steps: nextSteps,
      note: '声明式搜索：本 op 生成 spec 后跑 Blender 侧内环（bpy.app.timers，只在 GUI 会话有效）；内环结束时场景停在最后一次迭代，已按 restore 口径处理',
    }
  }
  return shapeSearchOp
}
