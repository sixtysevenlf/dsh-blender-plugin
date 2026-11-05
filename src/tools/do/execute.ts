/** blender_rt_do 执行层：GUI /act 与持久无头会话两条路径（行为与 v2.6.2 一致）。 */
import type { HostContext } from '../../host/context.js'
import { BACKEND_HINT } from '../../constants.js'
import { ensureBackend } from '../../host/backend/index.js'
import { backendPost, fetchFrame, leasedText, numTriple } from '../../host/http.js'
import { frameDedupe, dedupeNote } from '../../utils/frame-cache.js'
import { toAttachment, readLocalPng } from '../../utils/image.js'
import { asRecord, text } from '../../contracts/dynamic.js'
import { isHostAbort, toolError } from '../errors.js'
import { formatActText, formatSessionExec, formatSessionSee } from './envelope.js'

const LF = String.fromCharCode(10)

export async function executeDo(
  args: Record<string, unknown>,
  ctx: HostContext,
  port: number,
): Promise<unknown> {
  if (!(await ensureBackend(port)))
    return toolError(
      'backend_unreachable',
      '后端不可用（127.0.0.1:' + String(port) + '）',
      BACKEND_HINT,
    )
  const code = String(args.code || '')
  const file = args.file ? String(args.file) : null
  if (!code && !file)
    return toolError(
      'invalid_params',
      '需要 code 或 file 之一',
      '传 code=<python> 或 file=<脚本路径>',
    )
  const sessOpts: Record<string, unknown> = {
    code: code,
    file: file,
    session: args.session,
    preload: args.preload,
    see: !(args.see === false),
    max_size: args.max_size,
    from: args.from,
    look_at: args.look_at,
    lens: args.lens,
    ortho: args.ortho,
    ortho_scale: args.ortho_scale,
    view_size: args.view_size,
  }
  // 显式 session → 不探 GUI，直接持久无头会话（exec + see，同一 turn 内可连续调用）
  if (args.session) {
    const s = await doViaSession(ctx, port, sessOpts)
    return (
      s ||
      toolError(
        'backend_unreachable',
        '无头交互会话失败：worker 不可用（session=' + String(args.session) + '）',
        '先 blender_rt_worker op=start name=' + String(args.session) + '，再重试',
      )
    )
  }
  return doAct(ctx, port, args, code, file, sessOpts)
}

async function doAct(
  ctx: HostContext,
  port: number,
  args: Record<string, unknown>,
  code: string,
  file: string | null,
  sessOpts: Record<string, unknown>,
): Promise<unknown> {
  const body: Record<string, unknown> = { code: code }
  if (file) body.file = file
  let r: Record<string, unknown> | null
  try {
    r = await backendPost(port, '/act', body, 180000)
  } catch (e) {
    if (isHostAbort(e)) throw e
    if (args.auto !== false) {
      const s = await doViaSession(ctx, port, sessOpts)
      if (s) return s
    }
    return toolError(
      'backend_unreachable',
      'ACT 失败 · 127.0.0.1:' + String(port) + ' · ' + text(e),
      BACKEND_HINT,
    )
  }
  const lt = leasedText(r)
  if (lt) return { text: lt }
  // GUI 不在线时 /act 会回 ok:false + connect timeout —— 自动降级到持久无头会话
  if (r.ok === false && args.auto !== false) {
    const s = await doViaSession(ctx, port, sessOpts)
    if (s) return s
  }
  if (r.ok !== true)
    return toolError(
      'backend_error',
      'ACT 失败 · ' + text(r.error || r.raw || 'unknown'),
      '用 blender_viewport op=status 确认后端与 addon；需要降级时保留 auto=true',
    )
  const parts = formatActText(r)
  const image = await maybeActSee(ctx, port, args, parts)
  const out: Record<string, unknown> = { text: parts.join(LF) }
  if (image) out.image = image
  return out
}

async function maybeActSee(
  ctx: HostContext,
  port: number,
  args: Record<string, unknown>,
  parts: string[],
): Promise<unknown> {
  if (args.see === false) return undefined
  try {
    const size = Math.max(120, Math.min(1600, Number(args.max_size || 560)))
    const f = await fetchFrame(port, size)
    // v0.9.4：连续 rt_do 里画面没变就不重复附图（迭代回路最常见的浪费）
    const dd = frameDedupe('do-see:' + String(size), f.hash, !!args.force)
    if (dd.dup) {
      parts.push(
        'SEE ' +
          String(size) +
          'px · ' +
          String(f.ms) +
          'ms · hash ' +
          f.hash +
          dedupeNote(dd.repeats),
      )
      return undefined
    }
    const att = await toAttachment(ctx, f.png, 'viewport-' + String(Date.now()) + '.png')
    if (!att.ref) parts.push('⚠ 图片未回传：' + text(att.why || '未知原因'))
    parts.push('SEE ' + String(size) + 'px · ' + String(f.ms) + 'ms · hash ' + f.hash)
    return att.ref
  } catch (e) {
    parts.push('SEE 失败: ' + text(e))
    return undefined
  }
}

/** 交互式无头一步：exec 代码 + 可选 see（持久 worker；不是 job / 不是异步回合）。 */
export async function doViaSession(
  ctx: HostContext,
  port: number,
  opts: Record<string, unknown>,
): Promise<unknown> {
  try {
    const nm = String(opts.session || 'interactive')
    const preload = opts.preload ? String(opts.preload) : ''
    if (preload) {
      const bad = await startSession(port, nm, preload)
      if (bad) return bad
    }
    const code = readSessionCode(opts)
    if (code === null) return { text: '读不到脚本文件：' + String(opts.file) }
    if (!code) return null
    const r = await backendPost(
      port,
      '/worker',
      { op: 'exec', name: nm, code: code, isolated: true },
      180000,
    )
    const rr = asRecord(r.result)
    if (r.ok !== true) {
      return toolError(
        'backend_error',
        'ACT(session) 失败 · ' + text(r.error || rr.error || 'unknown'),
        '先 blender_rt_worker op=start name=' + nm + '，或检查后端 /worker 路由',
      )
    }
    const parts = formatSessionExec(rr, nm)
    const image = opts.see !== false ? await sessionSee(ctx, port, nm, opts, parts) : undefined
    const out: Record<string, unknown> = { text: parts.join(LF) }
    if (image) out.image = image
    return out
  } catch {
    return null
  }
}

async function startSession(port: number, nm: string, preload: string): Promise<unknown> {
  const st = await backendPost(
    port,
    '/worker',
    { op: 'start', name: nm, preload: preload, engine: 'workbench', warmup: true, isolated: true },
    120000,
  )
  if (st.ok === true) return null
  return toolError(
    'backend_error',
    '无头会话启动失败 · ' + text(st.error || st.raw || 'unknown'),
    '先 blender_rt_worker op=start 启动持久 worker，或检查后端 /worker 路由',
  )
}

function readSessionCode(opts: Record<string, unknown>): string | null {
  let code = String(opts.code || '')
  if (!code && opts.file) {
    const data = readLocalPng(opts.file)
    if (!data) return null
    code = Buffer.from(data).toString('utf8')
  }
  return code || ''
}

async function sessionSee(
  ctx: HostContext,
  port: number,
  nm: string,
  opts: Record<string, unknown>,
  parts: string[],
): Promise<unknown> {
  const size = Math.max(120, Math.min(1600, Number(opts.max_size || 560)))
  const spec: Record<string, unknown> = {
    from: numTriple(opts.from) || [7, -7, 5],
    look_at: numTriple(opts.look_at) || [0, 0, 1],
    lens: opts.lens === undefined ? undefined : Number(opts.lens),
    ortho: opts.ortho === undefined ? undefined : !!opts.ortho,
    ortho_scale: opts.ortho_scale === undefined ? undefined : Number(opts.ortho_scale),
    width: size,
    height: Math.max(120, Math.round((size * 9) / 16)),
  }
  const mm = String(opts.view_size || '').match(/^(\d+)\s*[x×]\s*(\d+)$/)
  if (mm) {
    spec.width = Number(mm[1])
    spec.height = Number(mm[2])
  }
  const sr = await backendPost(
    port,
    '/worker',
    { op: 'see', name: nm, spec: spec, isolated: true },
    90000,
  )
  const sres = asRecord(sr.result)
  if (sres.ok === false) {
    parts.push('SEE(session) 失败: ' + text(sres.error || 'unknown'))
    return undefined
  }
  const image = await attachSessionSee(ctx, sres, 'session-see-' + String(Date.now()) + '.png')
  parts.push(formatSessionSee(sres, spec))
  if (!image.ref) parts.push('⚠ 图片未回传：' + text(image.why || '未知原因'))
  return image.ref
}

async function attachSessionSee(
  ctx: HostContext,
  sres: Record<string, unknown>,
  name: string,
): Promise<{ ref?: unknown; why?: string }> {
  let data: Uint8Array | null = null
  if (sres.png_b64) {
    try {
      data = Buffer.from(String(sres.png_b64), 'base64')
    } catch {
      data = null
    }
  }
  if (!data && sres.path) data = readLocalPng(sres.path)
  return data ? toAttachment(ctx, data, name) : { why: '没有 png_b64/path' }
}
