import { KERNEL_BOOTSTRAP } from '../kernel/index.js'
import { extractLoop } from '../output/index.js'
import { wslToWin } from '../../config.js'
import { errorMessage } from '../json.js'
import type { AddonClient } from '../../transport/probes.js'

export interface RenderGuardDeps {
  RENDER_FLAG_WSL: string
  RENDER_STALE_MS: number
  addon: AddonClient
  clearRenderFlag: () => boolean
  ensureRenderGuard: () => Promise<unknown>
  readRenderFlag: () => {
    present: boolean
    stale: boolean
    ageMs: number | null
    info: Record<string, unknown> | null
  }
}

/** Local render-guard ops (read/wait/reset flags; install/selftest take one Blender round-trip). */
export function createRenderGuardOp(
  E: RenderGuardDeps,
): (op: string, payload?: unknown) => Promise<unknown> {
  const {
    RENDER_FLAG_WSL,
    RENDER_STALE_MS,
    addon,
    clearRenderFlag,
    ensureRenderGuard,
    readRenderFlag,
  } = E
  /** v0.9.6：渲染 guard 的本地 op（读/等/清标记；install/selftest 走 Blender 一次） */
  async function renderGuardCall(modOp: unknown, payload?: unknown): Promise<unknown> {
    await ensureRenderGuard()
    const body =
      'print("LOOP " + K.dsh_render_guard["dispatch"](' +
      JSON.stringify(String(modOp)) +
      ', _json.dumps(_json.loads(' +
      JSON.stringify(JSON.stringify(payload || {})) +
      '))))'
    return extractLoop(
      await addon.send(
        'execute_code',
        { code: KERNEL_BOOTSTRAP + '\nimport json as _json\n' + body },
        120000,
      ),
    )
  }
  async function renderGuardOp(op: string, payload?: unknown): Promise<unknown> {
    const p = payload && typeof payload === 'object' ? (payload as Record<string, unknown>) : {}
    if (op === 'render_state') {
      const g = readRenderFlag()
      const out: Record<string, unknown> = {
        ok: true,
        busy: !!(g.present && !g.stale),
        stale: !!g.stale,
        ageMs: g.ageMs,
        flag: g.info,
        flagPath: wslToWin(RENDER_FLAG_WSL),
        staleMs: RENDER_STALE_MS,
        note: 'busy=true ⇒ 主线程命令会被秒拒；用 op="render_wait" 等它，或 op="render_reset" 清陈标记',
      }
      if (p.probe) {
        try {
          out.blender = await renderGuardCall('state', {})
        } catch (e) {
          out.probe_error = errorMessage(e).slice(0, 200)
        }
      }
      return out
    }
    if (op === 'render_wait') {
      const cap = Math.max(1000, Math.min(600000, Number(p.timeout_ms) || 120000))
      const t0 = Date.now()
      for (;;) {
        const g = readRenderFlag()
        if (!g.present || g.stale) {
          return {
            ok: true,
            idle: true,
            waitedMs: Date.now() - t0,
            stale_cleared: !!g.stale,
            last: g.info,
          }
        }
        if (Date.now() - t0 >= cap) {
          return {
            ok: false,
            idle: false,
            timeout: true,
            waitedMs: Date.now() - t0,
            flag: g.info,
            hint: '仍在渲染：长渲染请一开始就用 blender_run(action="submit", kind="render")，这样它是独立进程、不占主线程',
          }
        }
        await new Promise<void>((r) => setTimeout(r, 400))
      }
    }
    if (op === 'render_reset') {
      const had = readRenderFlag()
      const removed = clearRenderFlag()
      let blender: unknown
      try {
        blender = await renderGuardCall('clear', { why: String(p.why || 'reset') })
      } catch (e) {
        blender = { ok: false, error: errorMessage(e).slice(0, 160) }
      }
      return {
        ok: true,
        cleared: removed,
        previous: had.info,
        blender: blender,
        note: '只在"标记是陈的/渲染其实已崩"时用；真在渲染时清了会让写命令排队',
      }
    }
    if (op === 'render_guard_install') {
      const r = await renderGuardCall('install', { force: !!p.force })
      return { ok: true, install: r, flagPath: wslToWin(RENDER_FLAG_WSL) }
    }
    // render_guard_selftest
    return await renderGuardCall('selftest', {})
  }
  return renderGuardOp
}
