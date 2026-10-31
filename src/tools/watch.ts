import { vTool } from '../contracts/tool.js'
import type { HostContext } from '../host/context.js'
import { ANY_SCHEMA } from './schema-diagnostic.js'
import { T, BACKEND_HINT } from '../constants.js'
import { ensureBackend, sleep } from '../host/backend.js'
import { backendPost, fetchFrame, leasedText } from '../host/http.js'
import { toAttachment, readLocalPng } from '../utils/image.js'
import { renderOne } from '../ui/cards.js'
import { asRecord, text } from '../contracts/dynamic.js'
import { redactSecrets, toolError } from './errors.js'

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
    const fr = Array.isArray(res.frames) ? res.frames : []
    const hashes = fr.filter((x) => asRecord(x).ok).map((x) => asRecord(x).hash)
    const distinct = new Set(hashes).size
    const parts: string[] = []
    const codeInfo = asRecord(res.code)
    if (res.code)
      parts.push(
        'code: ' +
          (codeInfo.ok ? 'ok' : '失败') +
          (codeInfo.error ? ' · ' + text(codeInfo.error) : ''),
      )
    parts.push(
      'WATCH(headless) ' +
        text(res.sampled || fr.length) +
        ' 帧 · 不同画面 ' +
        String(distinct) +
        '/' +
        String(hashes.length) +
        ' · ' +
        text(res.ms || 0) +
        'ms' +
        (res.static_render ? ' · 无动画帧范围，静态重复采样' : ''),
    )
    parts.push(
      'timeline: ' +
        fr
          .slice(0, 16)
          .map((x) => text(asRecord(x).frame) + ':' + text(asRecord(x).hash))
          .join(' '),
    )
    const images: unknown[] = []
    let whyNot = ''
    const picks = Array.isArray(res.picks) ? res.picks.slice(0, 6) : []
    for (let i = 0; i < picks.length; i++) {
      const p = asRecord(picks[i])
      let data: Uint8Array | null = null
      if (p.png_b64) {
        try {
          data = Buffer.from(String(p.png_b64), 'base64')
        } catch {
          data = null
        }
      }
      if (!data && p.path) data = readLocalPng(p.path)
      if (!data) {
        if (!whyNot) whyNot = '无 png_b64/path'
        continue
      }
      const att = await toAttachment(
        ctx,
        data,
        'watch-hl-' + text(p.frame) + '-' + text(p.hash) + '.png',
      )
      if (att.ref) images.push(att.ref)
      else if (!whyNot) whyNot = att.why || '未知原因'
    }
    parts.push('附帧：' + String(images.length) + ' 张' + (whyNot ? ' · ⚠ ' + whyNot : ''))
    return images.length
      ? { text: parts.join(String.fromCharCode(10)), images: images }
      : { text: parts.join(String.fromCharCode(10)) }
  } catch (e) {
    return { text: 'WATCH 无头异常 · ' + text(e) }
  }
}

export function registerWatch(ctx: HostContext, port: number): void {
  ctx.effect(
    () =>
      ctx.tools.register(
        vTool({
          name: 'blender_rt_watch',
          description:
            '观察时间窗/帧序列：先跑 code，再采样最多 6 帧 + 逐帧 hash（判断动画/修改器有没有动）；gui 不可用时自动走无头 worker。',
          parameters: {
            code: { type: 'string', description: '可选：观察前先执行的 Python' },
            seconds: { type: 'number', description: '观察时长，默认 2 秒（0.2-20）' },
            fps: { type: 'number', description: '采样率，默认 4（0.5-8）' },
            max_size: { type: 'integer', description: '帧最长边像素，默认 420' },
            session: { type: 'string', description: '无头 worker 会话（不给则 GUI 失败自动降级）' },
          },
          output: { schema: ANY_SCHEMA, render: renderOne },
          isConcurrencySafe: () => false,
          timeoutMs: T.watch,
          async execute(args: Record<string, unknown>) {
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
            const hashes = shots.map((s) => s.hash)
            const distinct = Array.from(new Set(hashes)).length
            const avgMs = Math.round(
              shots.reduce((a, s) => a + Math.max(0, s.ms), 0) / Math.max(1, shots.length),
            )
            parts.push(
              'WATCH ' +
                String(seconds) +
                's @' +
                String(fps) +
                'fps → ' +
                String(shots.length) +
                ' 帧 · 平均 ' +
                String(avgMs) +
                'ms/帧 · 不同画面 ' +
                String(distinct) +
                '/' +
                String(hashes.length),
            )
            parts.push(
              'timeline: ' +
                shots.map((s, i) => String(i) + '@' + String(s.t) + 'ms:' + s.hash).join(' '),
            )
            const picks: Array<{ t: number; ms: number; hash: string; png: Uint8Array | null }> = []
            const maxImages = 6
            if (shots.length <= maxImages) {
              for (const s of shots) if (s.png) picks.push(s)
            } else {
              for (let k = 0; k < maxImages; k++) {
                const idx = Math.round((k * (shots.length - 1)) / (maxImages - 1))
                if (shots[idx] && shots[idx].png) picks.push(shots[idx])
              }
            }
            const images: unknown[] = []
            let whyNot: string | undefined
            for (let k = 0; k < picks.length; k++) {
              const att = await toAttachment(
                ctx,
                picks[k].png as Uint8Array,
                'watch-' + String(k) + '-' + picks[k].hash + '.png',
              )
              if (att.ref) images.push(att.ref)
              else if (!whyNot) whyNot = att.why
            }
            parts.push(
              '附帧：' +
                String(images.length) +
                ' 张（均匀抽取）' +
                (whyNot ? ' · ⚠ 有帧未回传：' + whyNot : ''),
            )
            const out: Record<string, unknown> = { text: parts.join('\n') }
            if (images.length) out.images = images
            return out
          },
        }),
      ),
    '@dsh-external/dsh-blender-plugin: rt-watch',
  )
}
