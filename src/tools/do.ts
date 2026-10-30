import { vTool } from '../contracts/tool.js'
import { ANY_SCHEMA, HINT_KERNEL } from './schema-diagnostic.js'
import { T, BACKEND_HINT } from '../constants.js'
import { ensureBackend } from '../host/backend.js'
import { backendPost, fetchFrame, leasedText, numTriple } from '../host/http.js'
import { frameDedupe, dedupeNote } from '../utils/frame-cache.js'
import { toAttachment, readLocalPng } from '../utils/image.js'
import { renderOne } from '../ui/cards.js'
import { isHostAbort, redactSecrets, toolError } from './errors.js'

/** 交互式无头一步：exec 代码 + 可选 see（持久 worker；不是 job / 不是异步回合）。 */
async function doViaSession(ctx: any, port: number, opts: any): Promise<any | null> {
  try {
    const nm = String(opts.session || 'interactive')
    const preload = opts.preload ? String(opts.preload) : ''
    if (preload) {
      const st: any = await backendPost(
        port,
        '/worker',
        { op: 'start', name: nm, preload: preload, engine: 'workbench', warmup: true, isolated: true },
        120000
      )
      if (!st || st.ok !== true)
        return toolError(
          'backend_error',
          '无头会话启动失败 · ' + String((st && (st.error || st.raw)) || 'unknown'),
          '先 blender_rt_worker op=start 启动持久 worker，或检查后端 /worker 路由'
        )
    }
    let code = String(opts.code || '')
    if (!code && opts.file) {
      const data = readLocalPng(opts.file)
      if (!data) return { text: '读不到脚本文件：' + String(opts.file) }
      code = Buffer.from(data).toString('utf8')
    }
    if (!code) return null
    const r: any = await backendPost(port, '/worker', { op: 'exec', name: nm, code: code, isolated: true }, 180000)
    const rr = (r && r.result) || {}
    if (!r || r.ok !== true) {
      return toolError(
        'backend_error',
        'ACT(session) 失败 · ' + String((r && (r.error || rr.error)) || 'unknown'),
        '先 blender_rt_worker op=start name=' + nm + '，或检查后端 /worker 路由'
      )
    }
    const parts: string[] = ['ACT(session=' + nm + ') ok · ' + String(rr.ms) + 'ms']
    if (rr.stdout && String(rr.stdout).trim()) parts.push('stdout: ' + redactSecrets(rr.stdout).slice(0, 3000))
    if (rr.stderr && String(rr.stderr).trim()) parts.push('stderr: ' + redactSecrets(rr.stderr).slice(0, 1200))
    let image: any = undefined
    const see = opts.see !== false
    if (see) {
      const size = Math.max(120, Math.min(1600, Number(opts.max_size || 560)))
      const spec: any = {
        from: numTriple(opts.from) || [7, -7, 5],
        look_at: numTriple(opts.look_at) || [0, 0, 1],
        lens: opts.lens === undefined ? undefined : Number(opts.lens),
        ortho: opts.ortho === undefined ? undefined : !!opts.ortho,
        ortho_scale: opts.ortho_scale === undefined ? undefined : Number(opts.ortho_scale),
        width: size,
        height: Math.max(120, Math.round((size * 9) / 16))
      }
      const vs = String(opts.view_size || '')
      const mm = vs.match(/^(\d+)\s*[x×]\s*(\d+)$/)
      if (mm) {
        spec.width = Number(mm[1])
        spec.height = Number(mm[2])
      }
      const sr: any = await backendPost(port, '/worker', { op: 'see', name: nm, spec: spec, isolated: true }, 90000)
      const sres = (sr && sr.result) || {}
      if (sres.ok !== false) {
        let data: Uint8Array | null = null
        if (sres.png_b64) {
          try {
            data = Buffer.from(String(sres.png_b64), 'base64')
          } catch (e: any) {
            data = null
          }
        }
        if (!data && sres.path) data = readLocalPng(sres.path)
        const att = data ? await toAttachment(ctx, data, 'session-see-' + String(Date.now()) + '.png') : { why: '没有 png_b64/path' }
        if (att.ref) image = att.ref
        parts.push(
          'SEE(session) ' +
            String(spec.width) +
            'x' +
            String(spec.height) +
            ' · ' +
            String(sres.ms) +
            'ms' +
            (sres.coverage_estimate === null || sres.coverage_estimate === undefined
              ? ''
              : ' · 覆盖 ' + (Number(sres.coverage_estimate) * 100).toFixed(2) + '%')
        )
        if (!image) parts.push('⚠ 图片未回传：' + String(att.why || '未知原因'))
      } else {
        parts.push('SEE(session) 失败: ' + String(sres.error || 'unknown'))
      }
    }
    const out: any = { text: parts.join(String.fromCharCode(10)) }
    if (image) out.image = image
    return out
  } catch (e: any) {
    return null
  }
}

export function registerDo(ctx: any, port: number): void {
  ctx.effect(
    () =>
      ctx.tools.register(
        vTool({
          name: 'blender_rt_do',
          description:
            '实时驱动 Blender：在持久内核 K 里执行 Python（或直接跑 .py 文件），可选立刻回一帧视口。只补 op 没有的局部编辑；有 plan op 先用 op。' +
            'GUI 可用时走 addon；传 session 时在持久无头 worker 里 exec+see（GUI 不可达自动降级，不是异步 job）；每步约 50–150 ms。' +
            HINT_KERNEL +
            ' 异常也会回传 partial stdout / stderr / traceback（诊断信息不丢）；' +
            ' file 参数可直接执行工作区脚本（等价 K.run(path)，支持 WSL / Windows / 相对 .blend 三种路径）；' +
            ' 内置路径辅助 K.win_path / K.wsl_path / K.blend_path / K.out_dir；' +
            ' ④ 返回的 ms 即**主线程占用**，>1 s 会提示改走 blender_rt_headless / blender_rt_worker。' +
            ' **P0 无头交互**：GUI 不在线或传 session 时，自动降级到持久无头会话（exec + Workbench see，同一 worker 热路径；不是 job/异步）。' +
            '第一次会冷启动 worker，之后每步 ~50–150ms；session 名固定即复用同一个 Blender 会话。',
          parameters: {
            code: {
              type: 'string',
              description: 'Python 代码（在 Blender 主线程执行；预置 bpy/math/mathutils/Vector 与持久内核 K）。与 file 二选一'
            },
            file: {
              type: 'string',
              description: '要执行的 .py 文件路径（WSL / Windows / 相对当前 .blend 均可）—— 长脚本用这个，不必塞进入参'
            },
            see: { type: 'boolean', description: '是否同时回一帧视口（默认 true）' },
            max_size: { type: 'integer', description: '回帧最长边像素，默认 560' },
            force: { type: 'boolean', description: '回帧与上一帧 hash 完全相同时默认**不重复附图**；传 true 强制重发' },
            session: { type: 'string', description: '无头交互会话名（复用同一 blender -b）' },
            auto: { type: 'boolean', description: 'GUI 不可用时降级无头 session（默认 true）' },
            preload: { type: 'string', description: '无头 session 预载模块（逗号分隔）' },
            from: { type: 'string', description: '无头 see 相机位置 "x,y,z"' },
            look_at: { type: 'string', description: '无头 see 看向点 "x,y,z"' }
          },
          output: { schema: ANY_SCHEMA, render: renderOne },
          isConcurrencySafe: () => false,
          timeoutMs: T.act,
          async execute(args: any) {
            if (!(await ensureBackend(port)))
              return toolError('backend_unreachable', '后端不可用（127.0.0.1:' + String(port) + '）', BACKEND_HINT)
            const code = String((args && args.code) || '')
            const file = args && args.file ? String(args.file) : null
            if (!code && !file) return toolError('invalid_params', '需要 code 或 file 之一', '传 code=<python> 或 file=<脚本路径>')
            const sessOpts = {
              code: code,
              file: file,
              session: args && args.session,
              preload: args && args.preload,
              see: !(args && args.see === false),
              max_size: args && args.max_size,
              from: args && args.from,
              look_at: args && args.look_at,
              lens: args && args.lens,
              ortho: args && args.ortho,
              ortho_scale: args && args.ortho_scale,
              view_size: args && args.view_size
            }
            // P0：显式 session → 不探 GUI，直接持久无头会话（exec + see，同一 turn 内可连续调用）
            if (args && args.session) {
              const s = await doViaSession(ctx, port, sessOpts)
              return (
                s ||
                toolError(
                  'backend_unreachable',
                  '无头交互会话失败：worker 不可用（session=' + String(args.session) + '）',
                  '先 blender_rt_worker op=start name=' + String(args.session) + '，再重试'
                )
              )
            }
            const body: any = { code: code }
            if (file) body.file = file
            let r: any = null
            try {
              r = await backendPost(port, '/act', body, 180000)
            } catch (e: any) {
              if (isHostAbort(e)) throw e
              if (!(args && args.auto === false)) {
                const s = await doViaSession(ctx, port, sessOpts)
                if (s) return s
              }
              return toolError(
                'backend_unreachable',
                'ACT 失败 · 127.0.0.1:' + String(port) + ' · ' + String((e && (e as Error).message) || e),
                BACKEND_HINT
              )
            }
            const lt = leasedText(r)
            if (lt) return { text: lt }
            // GUI 不在线时 /act 会回 ok:false + connect timeout —— 自动降级到持久无头会话
            if (r && r.ok === false && !(args && args.auto === false)) {
              const s = await doViaSession(ctx, port, sessOpts)
              if (s) return s
            }
            const parts: string[] = []
            if (!r || r.ok !== true)
              return toolError(
                'backend_error',
                'ACT 失败 · ' + String((r && (r.error || r.raw)) || 'unknown'),
                '用 blender_viewport op=status 确认后端与 addon；需要降级时保留 auto=true'
              )
            parts.push('ACT ok · ' + String(r.ms) + 'ms')
            if (r && Number(r.mainThreadMs) > 1000)
              parts.push(
                '⚠️ 本次占用 Blender 主线程约 ' +
                  String(r.mainThreadMs) +
                  'ms —— 超过 ~1s 的重活建议改走 blender_rt_headless / blender_rt_worker（不卡 GUI）'
              )
            if (r && r.stderr && String(r.stderr).trim()) parts.push('stderr: ' + redactSecrets(r.stderr).slice(0, 1200))
            if (r && r.traceback) parts.push('--- traceback ---' + String.fromCharCode(10) + redactSecrets(r.traceback).slice(0, 2000))
            if (r && r.stdout) parts.push('stdout: ' + redactSecrets(r.stdout).slice(0, 3000))
            let image: any = undefined
            const see = !(args && args.see === false)
            if (see) {
              try {
                const size = Math.max(120, Math.min(1600, Number((args && args.max_size) || 560)))
                const f = await fetchFrame(port, size)
                // v0.9.4（P0-2）：连续 rt_do 里画面没变就不重复附图（迭代回路最常见的浪费）
                const dd3 = frameDedupe('do-see:' + String(size), f.hash, !!(args && args.force))
                if (dd3.dup) {
                  parts.push('SEE ' + String(size) + 'px · ' + String(f.ms) + 'ms · hash ' + f.hash + dedupeNote(dd3.repeats))
                } else {
                  const att2 = await toAttachment(ctx, f.png, 'viewport-' + String(Date.now()) + '.png')
                  image = att2.ref
                  if (!image) parts.push('⚠ 图片未回传：' + String(att2.why || '未知原因'))
                  parts.push('SEE ' + String(size) + 'px · ' + String(f.ms) + 'ms · hash ' + f.hash)
                }
              } catch (e: any) {
                parts.push('SEE 失败: ' + String((e && e.message) || e))
              }
            }
            const out: any = { text: parts.join('\n') }
            if (image) out.image = image
            return out
          }
        })
      ),
    '@dsh-external/dsh-blender-plugin: rt-do'
  )
}
