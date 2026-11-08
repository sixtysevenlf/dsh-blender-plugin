// shape-search 结果判定/恢复/建议
import { KERNEL_BOOTSTRAP } from '../../kernel/index.js'
import { errorMessage } from '../../json.js'
import { pyIndent, pyLit } from './axes.js'
import type { ShapeSearchDeps } from './index.js'
import type { SearchPoll } from './search.js'

export interface SearchApplied {
  best: Record<string, unknown> | null
  applied: Record<string, unknown> | null
}

/** 按 restore 口径把 best 参数落回场景 */
export async function applyBestParams(
  E: ShapeSearchDeps,
  p: Record<string, unknown>,
  setupSrc: string,
  applyBody: string,
  poll: SearchPoll,
  board: Record<string, unknown> | null,
): Promise<SearchApplied> {
  const best = ((poll.st && poll.st.best) || (board && board.best) || null) as Record<
    string,
    unknown
  > | null
  let applied: Record<string, unknown> | null = null
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
      const out = await E.addon.send(
        'execute_code',
        { code: KERNEL_BOOTSTRAP + '\n' + body },
        60000,
      )
      const outRec = (out || null) as Record<string, unknown> | null
      const raw = outRec && typeof outRec.result === 'string' ? outRec.result : ''
      const mm = raw.match(/SEARCH_APPLIED (\{[\s\S]*?\})\s*$/m)
      applied = mm
        ? (JSON.parse(mm[1]) as Record<string, unknown>)
        : { ok: false, raw_tail: raw.slice(-200) }
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
  return { best, applied }
}

export function buildNextSteps(
  p: Record<string, unknown>,
  objective: string,
  best: Record<string, unknown> | null,
  timedOut: boolean,
): string[] {
  const nextSteps: string[] = []
  if (best)
    nextSteps.push(
      '换另一条通路复核（内环只优化你写的 score）：blender_op(op="qc_render_views", args={views:[…], ref_path:"…"}) + op="audit_measure" + blender_see',
    )
  if (!p.export)
    nextSteps.push(
      '要落可复现脚本：本 op 加 export:"D:/out/best_fit.py"（或 blender_op(op="export", path=…)）',
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
  return nextSteps
}

export interface SearchResultArgs {
  preview: Record<string, unknown>
  st: Record<string, unknown> | null
  timedOut: boolean
  best: Record<string, unknown> | null
  board: Record<string, unknown> | null
  pollError: string | null
  applied: Record<string, unknown> | null
  exported: Record<string, unknown> | null
  warnings: string[]
  nextSteps: string[]
}

export function buildSearchResult(a: SearchResultArgs): Record<string, unknown> {
  const {
    preview: specEcho,
    st,
    timedOut,
    best,
    board,
    pollError,
    applied,
    exported,
    warnings,
    nextSteps,
  } = a
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
