// 轨迹记录器
import path from 'node:path'
import fs from 'node:fs'

export interface TrajWriteDeps {
  TRAJ_OFF: boolean
  TRAJ_DIR: string
  TRAJ_FULL: boolean
  TRAJ_MAX_BYTES: number
}

function trajShortHash(s: string): string {
  let h = 0
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0
  return (h >>> 0).toString(16).padStart(8, '0')
}

export function trajRecord(
  deps: TrajWriteDeps,
  route: string,
  op: unknown,
  args: unknown,
  ms: number,
  ok: unknown,
  err: unknown,
  extra: unknown,
): void {
  if (deps.TRAJ_OFF) return
  // v0.9.6：加 family / args_keys / hint_shown —— 用来做"家族覆盖率"和"意图→工具"周报。
  // family 只对 plan 路由有意义（frame 的"op"其实是尺寸、act 的"op"是代码片段，切出来是噪声）。
  const fam = route === 'plan' && op ? String(op).split('_')[0] : null
  try {
    fs.mkdirSync(deps.TRAJ_DIR, { recursive: true })
    const d = new Date()
    const key =
      String(d.getFullYear()) +
      String(d.getMonth() + 1).padStart(2, '0') +
      String(d.getDate()).padStart(2, '0')
    const f = path.join(deps.TRAJ_DIR, 'blender_rt-' + key + '.jsonl')
    try {
      const st = fs.statSync(f)
      if (st.size > deps.TRAJ_MAX_BYTES)
        fs.renameSync(
          f,
          path.join(deps.TRAJ_DIR, 'blender_rt-' + key + '-' + Date.now() + '.jsonl'),
        )
    } catch (e) {
      /* 首次写 */
    }
    const s =
      args === undefined || args === null
        ? ''
        : typeof args === 'string'
          ? args
          : JSON.stringify(args)
    fs.appendFileSync(
      f,
      JSON.stringify({
        t: d.toISOString(),
        route: route,
        op: op === undefined || op === null ? null : String(op),
        ms: ms,
        ok: !!ok,
        err: err ? String(err).slice(0, 300) : null,
        args_bytes: s.length,
        args_digest: trajShortHash(s),
        args_head: s ? s.slice(0, deps.TRAJ_FULL ? 8000 : 300) : null,
        // v0.9.6：可统计"家族覆盖率"与"参数键分布"（不记值，只记键名）
        family: fam,
        args_keys:
          args && typeof args === 'object' && !Array.isArray(args)
            ? Object.keys(args).slice(0, 20)
            : null,
        hint_shown: !!(
          extra !== null &&
          typeof extra === 'object' &&
          (extra as Record<string, unknown>).hint_shown
        ),
      }) + String.fromCharCode(10),
      'utf8',
    )
  } catch (e) {
    /* 轨迹是旁路：出错就丢这一行 */
  }
}
