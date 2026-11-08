// shape-search Python 脚本生成与内环驱动
import { errorMessage } from '../../json.js'
import { pyIndent, pyLit } from './axes.js'
import type { AxisSpec } from './axes.js'
import type { ShapeSearchDeps } from './index.js'
import type { ShapeSearchLimits, ShapeSearchTargets } from './parse.js'

type AxisEntry = { k: string; spec: AxisSpec; vals: unknown[] }

/** 两段式去缩进：① 按首行缩进整体去缩进；② 续行公共缩进再收一次（保留嵌套块相对层次）。 */
function normalizeBody(src: unknown): string {
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

export function buildSetupSrc(p: Record<string, unknown>, applyBody: string): string {
  const parts = ['import bpy', 'import math', 'import random']
  if (p.setup) parts.push(normalizeBody(p.setup))
  parts.push(
    applyBody
      ? 'def apply(p):\n' + pyIndent(normalizeBody(applyBody), '    ')
      : 'def apply(p):\n    pass',
  )
  return parts.join('\n') + '\n'
}

export function buildStepSrc(strategy: string, axes: AxisEntry[], combos: number): string {
  if (strategy === 'grid') {
    const axesLit =
      '[' + axes.map((a) => '[' + JSON.stringify(a.k) + ', ' + pyLit(a.vals) + ']').join(', ') + ']'
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
        String(a.spec.min) +
        ', ' +
        String(a.spec.max) +
        ')))'
      )
    return (
      '    ' +
      JSON.stringify(a.k) +
      ': random.uniform(' +
      String(a.spec.min) +
      ', ' +
      String(a.spec.max) +
      ')'
    )
  })
  return 'ns["params"] = {\n' + lines.join(',\n') + '\n}\n'
}

export function buildMeasureSrc(
  applyBody: string,
  measure: unknown,
  objective: string,
  target: string,
  targetSize: unknown,
  views: unknown[],
  ref: string,
  refBox: unknown,
  bins: number,
): string {
  if (measure) return (applyBody ? 'apply(ns["params"])\n' : '') + String(measure) + '\n'
  const lines: string[] = []
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
}

export function buildSpec(
  setupSrc: string,
  stepSrc: string,
  measureSrc: string,
  iterations: number,
  limits: ShapeSearchLimits,
): Record<string, unknown> {
  const { budgetMs, interval, measureEvery, minimize, topK, patience } = limits
  return {
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
}

export function buildSpecEcho(
  objective: string,
  strategy: string,
  combos: number,
  iterations: number,
  limits: ShapeSearchLimits,
  norm: Record<string, AxisSpec>,
  targets: ShapeSearchTargets,
  p: Record<string, unknown>,
): Record<string, unknown> {
  const { budgetMs, topK, patience, minimize } = limits
  const { views, ref, target } = targets
  return {
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
}

export async function preloadForSearch(
  E: ShapeSearchDeps,
  renderObjective: boolean,
): Promise<Record<string, unknown> | null> {
  try {
    await E.ensureRunner()
    if (renderObjective) {
      await E.ensureQc()
      await E.ensureView()
    }
    return null
  } catch (e) {
    return {
      ok: false,
      op: 'shape_search',
      stage: 'preload',
      error: '注入 runner/qc/view 失败：' + errorMessage(e).slice(0, 240),
      hint: 'GUI Blender 连着吗？本 op 只在 GUI 会话有效（无头下 bpy.app.timers 不触发）',
    }
  }
}

export async function startSearchLoop(
  E: ShapeSearchDeps,
  spec: Record<string, unknown>,
  specEcho: Record<string, unknown>,
): Promise<Record<string, unknown> | null> {
  let started: Record<string, unknown> | null
  try {
    started = (await E.loopStart(spec)) as Record<string, unknown> | null
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
      hint: '若提示 loop already running：先 blender_op(op="status") 看上一轮，必要时 op="stop"',
    }
  }
  return null
}

export interface SearchPoll {
  st: Record<string, unknown> | null
  timedOut: boolean
  pollError: string | null
}

export async function pollSearchLoop(E: ShapeSearchDeps, waitCap: number): Promise<SearchPoll> {
  const t0 = Date.now()
  let st: Record<string, unknown> | null = null,
    timedOut = false,
    pollError: string | null = null
  try {
    st = await E.loopStatus(8)
    while (st && st.running === true) {
      if (Date.now() - t0 > waitCap) {
        timedOut = true
        await E.loopStop()
        await new Promise<void>((r) => setTimeout(r, 300))
        st = await E.loopStatus(8)
        break
      }
      await new Promise<void>((r) => setTimeout(r, 300))
      st = await E.loopStatus(8)
    }
  } catch (e) {
    pollError = errorMessage(e).slice(0, 300)
  }
  return { st, timedOut, pollError }
}

export interface SearchBoard {
  board: Record<string, unknown> | null
  exported: Record<string, unknown> | null
}

export async function fetchBoardAndExport(
  E: ShapeSearchDeps,
  p: Record<string, unknown>,
  topK: number,
  objective: string,
): Promise<SearchBoard> {
  let board: Record<string, unknown> | null
  let exported: Record<string, unknown> | null = null
  try {
    board = (await E.loopBoard(topK > 0 ? topK : 10, false)) as Record<string, unknown> | null
  } catch (e) {
    board = null
  }
  if (p.export) {
    try {
      exported = (await E.loopExport({
        path: String(p.export),
        top: 1,
        includeVariants: false,
        note: 'shape_search ' + objective,
      })) as Record<string, unknown>
    } catch (e) {
      exported = { ok: false, error: errorMessage(e).slice(0, 200) }
    }
  }
  return { board, exported }
}
