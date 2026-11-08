// shape-search 入口（拆分）：解析 → 脚本 → 内环 → 结果
import type { AddonClient } from '../../../transport/probes.js'
import { parseShapeSearchPayload } from './parse.js'
import {
  buildMeasureSrc,
  buildSetupSrc,
  buildSpec,
  buildSpecEcho,
  buildStepSrc,
  fetchBoardAndExport,
  pollSearchLoop,
  preloadForSearch,
  startSearchLoop,
} from './search.js'
import { applyBestParams, buildNextSteps, buildSearchResult } from './rank.js'

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
  return (payload?: unknown) => shapeSearchOp(E, payload)
}

async function shapeSearchOp(
  E: ShapeSearchDeps,
  payload?: unknown,
): Promise<Record<string, unknown>> {
  const parsed = parseShapeSearchPayload(payload)
  if (!parsed.ok) return parsed
  const { p, objective, norm, limits, strategy, targets } = parsed.input
  const { axes, combos, strategy: strategyName, iterations } = strategy
  const setupSrc = buildSetupSrc(p, targets.applyBody)
  const stepSrc = buildStepSrc(strategyName, axes, combos)
  const measureSrc = buildMeasureSrc(
    targets.applyBody,
    p.measure,
    objective,
    targets.target,
    targets.targetSize,
    targets.views,
    targets.ref,
    targets.refBox,
    targets.bins,
  )
  const spec = buildSpec(setupSrc, stepSrc, measureSrc, iterations, limits)
  const specEcho = buildSpecEcho(
    objective,
    strategyName,
    combos,
    iterations,
    limits,
    norm,
    targets,
    p,
  )
  if (p.dry_run === true) {
    return {
      ok: true,
      op: 'shape_search',
      dry_run: true,
      spec_echo: specEcho,
      spec: spec,
      warnings: targets.warnings,
      note: 'dry_run：只校验并回 spec，没有启动内环（想手跑就把它喂给 blender_op(op="start", spec=…)）',
    }
  }
  const preloadError = await preloadForSearch(E, targets.renderObjective)
  if (preloadError) return preloadError
  const startError = await startSearchLoop(E, spec, specEcho)
  if (startError) return startError
  const waitCap = Math.max(5000, Math.min(600000, Number(p.wait_ms) || limits.budgetMs + 20000))
  const poll = await pollSearchLoop(E, waitCap)
  const { board, exported } = await fetchBoardAndExport(E, p, limits.topK, objective)
  const { best, applied } = await applyBestParams(E, p, setupSrc, targets.applyBody, poll, board)
  const nextSteps = buildNextSteps(p, objective, best, poll.timedOut)
  return buildSearchResult({
    preview: specEcho,
    st: poll.st,
    timedOut: poll.timedOut,
    best,
    board,
    pollError: poll.pollError,
    applied,
    exported,
    warnings: targets.warnings,
    nextSteps,
  })
}
