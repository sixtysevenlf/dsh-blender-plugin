/** watch 执行层：GUI 逐帧采样；失败/指定 session 时走无头 worker。 */
import type { HostContext } from '../../host/context.js'
import { BACKEND_HINT } from '../../constants.js'
import { ensureBackend, sleep } from '../../host/backend/index.js'
import { backendPost, fetchFrame, leasedText } from '../../host/http.js'
import { asRecord, text } from '../../contracts/dynamic.js'
import { redactSecrets, toolError } from '../errors.js'
import { guiWatchResponse, workerWatchResponse } from './response.js'

export async function executeWatch(
  ctx: HostContext,
  port: number,
  args: Record<string, unknown>,
): Promise<unknown> {
  if (!(await ensureBackend(port)))
    return toolError(
      'backend_unreachable',
      '后端不可用（127.0.0.1:' + String(port) + '）',
      BACKEND_HINT,
    )
  const seconds = Math.max(0.2, Math.min(20, Number(args.seconds || 2)))
  const fps = Math.max(0.5, Math.min(8, Number(args.fps || 4)))
  const size = Math.max(120, Math.min(1600, Number(args.max_size || 420)))
  const parts: string[] = []
  if (args.session) return await watchViaWorker(ctx, port, args, String(args.session))
  if (args.code) {
    const r = await backendPost(port, '/act', { code: String(args.code) }, 180000)
    const lt = leasedText(r)
    parts.push(
      lt
        ? lt
        : r.ok
          ? 'ACT ok · ' +
            text(r.ms) +
            'ms' +
            (r.stdout ? ' · stdout: ' + redactSecrets(r.stdout).slice(0, 600) : '')
          : 'ACT 失败 · ' + text(r.error || r.raw || ''),
    )
  }
  const total = Math.max(1, Math.min(32, Math.round(seconds * fps)))
  const interval = (seconds * 1000) / total
  const shots: Array<{ t: number; ms: number; hash: string; png: Uint8Array | null }> = []
  const t0 = Date.now()
  for (let i = 0; i < total; i++) {
    try {
      const f = await fetchFrame(port, size)
      shots.push({ t: Date.now() - t0, ms: f.ms, hash: f.hash, png: f.png })
    } catch {
      shots.push({ t: Date.now() - t0, ms: -1, hash: 'ERR', png: null })
    }
    const wait = (i + 1) * interval - (Date.now() - t0)
    if (wait > 1) await sleep(wait)
  }
  if (shots.length && shots.every((s) => s.hash === 'ERR')) {
    const fb = await watchViaWorker(ctx, port, args, undefined)
    const fbRecord = asRecord(fb)
    if (fb && fbRecord.text) return fb
  }
  return guiWatchResponse(ctx, shots, parts, seconds, fps)
}

async function watchViaWorker(
  ctx: HostContext,
  port: number,
  args: Record<string, unknown>,
  session?: string,
): Promise<unknown> {
  const size = Math.max(120, Math.min(1600, Number(args.max_size || 420)))
  const spec: Record<string, unknown> = {
    width: size,
    height: Math.max(120, Math.round((size * 9) / 16)),
    seconds: Math.max(0.2, Math.min(20, Number(args.seconds || 2))),
    fps: Math.max(0.5, Math.min(8, Number(args.fps || 4))),
    code: args.code ? String(args.code) : undefined,
  }
  const nm = session ? String(session) : 'watch'
  try {
    await backendPost(
      port,
      '/worker',
      { op: 'start', name: nm, engine: 'workbench', warmup: true, isolated: true },
      120000,
    )
    const r = await backendPost(
      port,
      '/worker',
      { op: 'watch', name: nm, spec: spec, isolated: true },
      180000,
    )
    const res = asRecord(r.result)
    if (r.ok !== true || res.ok !== true)
      return toolError(
        'backend_error',
        'WATCH 无头失败 · ' + text(r.error || res.error || 'unknown'),
        '先 blender_rt_worker op=start 启动 worker，或改用 GUI 通道',
      )
    return await workerWatchResponse(ctx, res)
  } catch (e) {
    return { text: 'WATCH 无头异常 · ' + text(e) }
  }
}
