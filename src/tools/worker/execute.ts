/** blender_rt_worker 执行层：会话命令转发 + 回执（行为与 v2.6.2 一致）。 */
import type { HostContext } from '../../host/context.js'
import { BACKEND_HINT } from '../../constants.js'
import { ensureBackend } from '../../host/backend/index.js'
import { backendPost, leasedText, numTriple } from '../../host/http.js'
import { toAttachment, readLocalPng } from '../../utils/image.js'
import { redactSecrets, toolError } from '../errors.js'
import { asArray, asRecord, text } from '../../contracts/dynamic.js'

const LF = String.fromCharCode(10)

export async function executeWorker(
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
  const op = text(args.op) || 'status'
  const body = buildWorkerBody(op, args)
  const budget = 60000 + Number(body.timeoutMs || 120000)
  const r = await backendPost(port, '/worker', body, budget)
  const lt = leasedText(r)
  if (lt) return { text: lt }
  if (!r || r.ok !== true) return workerFailure(op, r)
  const res = asRecord(r.result)
  if (op === 'see') return workerSee(ctx, res)
  if (op === 'reset') return workerReset(res)
  if (op === 'exec') return workerExec(res)
  if (op === 'list') return workerList(res)
  return workerStatus(op, res)
}

function buildWorkerBody(op: string, args: Record<string, unknown>): Record<string, unknown> {
  const body: Record<string, unknown> = { op: op, isolated: true }
  if (args.code !== undefined) body.code = text(args.code)
  if (args.timeout_ms) body.timeoutMs = Number(args.timeout_ms)
  if (args.gpu) body.gpu = text(args.gpu)
  if (args.engine) body.engine = text(args.engine)
  if (args.preload) body.preload = text(args.preload)
  if (args.warmup !== undefined) body.warmup = !!args.warmup
  if (args.purge_prefix) body.purgePrefix = text(args.purge_prefix)
  if (args.name) body.name = text(args.name)
  if (op === 'see') {
    const vs = text(args.view_size) || '560x315'
    const mm = vs.match(/^(\d+)\s*[x×]\s*(\d+)$/)
    body.spec = {
      from: numTriple(args.from) || [7, -7, 5],
      look_at: numTriple(args.look_at) || [0, 0, 1],
      lens: args.lens !== undefined ? Number(args.lens) : undefined,
      ortho: args.ortho !== undefined ? !!args.ortho : undefined,
      ortho_scale: args.ortho_scale !== undefined ? Number(args.ortho_scale) : undefined,
      shading: args.shading || undefined,
      width: mm ? Number(mm[1]) : 560,
      height: mm ? Number(mm[2]) : 315,
    }
  }
  return body
}

function workerFailure(op: string, raw: unknown): unknown {
  const r = asRecord(raw)
  const rr = asRecord(r.result)
  const bits: string[] = []
  bits.push('WORKER ' + op + ' 失败 · ' + text(r.error || rr.error || 'unknown'))
  if (rr.traceback) bits.push('--- traceback ---' + LF + redactSecrets(rr.traceback).slice(0, 2000))
  if (rr.stderr && String(rr.stderr).trim())
    bits.push('stderr: ' + redactSecrets(rr.stderr).slice(0, 1200))
  if (rr.stdout && String(rr.stdout).trim())
    bits.push('stdout: ' + redactSecrets(rr.stdout).slice(0, 800))
  if (r.hint) bits.push(text(r.hint))
  if (text(r.code) === 'blender_exe_missing')
    return toolError(
      'blender_exe_missing',
      bits.join(LF),
      '设置 DSH_BLENDER_EXE 或 dsh-blender.config.json 的 blenderExe（Steam 版路径形如 <Steam>\\steamapps\\common\\Blender\\blender.exe）；改完重启 DSH，再 blender_rt_worker op=start 重试',
    )
  return toolError(
    'backend_error',
    bits.join(LF),
    '检查后端 /worker 路由；先 blender_viewport op=doctor 看进程与端口',
  )
}

async function workerSee(ctx: HostContext, res: Record<string, unknown>): Promise<unknown> {
  if (res.ok === false)
    return toolError(
      'backend_error',
      'WORKER see 失败 · ' + text(res.error || 'unknown'),
      '用 blender_rt_worker op=start 检查无头 worker；或改用 blender_rt_see',
      res.traceback ? redactSecrets(res.traceback).slice(-1200) : undefined,
    )
  let data: Uint8Array | null = null
  if (res.png_b64) {
    try {
      data = Buffer.from(text(res.png_b64), 'base64')
    } catch {
      data = null
    }
  }
  if (!data && res.path) data = readLocalPng(res.path)
  const att = data
    ? await toAttachment(ctx, data, 'worker-see-' + String(Date.now()) + '.png')
    : { why: '没有 png_b64/path' }
  const textOut = formatWorkerSeeText(res)
  return att.ref
    ? { text: textOut, image: att.ref }
    : { text: textOut + LF + '⚠ 图片未回传：' + text(att.why || '未知原因') }
}

function formatWorkerSeeText(res: Record<string, unknown>): string {
  return (
    'WORKER see ok · ' +
    text(res.ms) +
    'ms · ' +
    text(res.width) +
    'x' +
    text(res.height) +
    ' · ' +
    text(res.bytes || 0) +
    'B · ' +
    text(res.mode || 'workbench') +
    (res.coverage_estimate === null || res.coverage_estimate === undefined
      ? ''
      : ' · 覆盖 ' + (Number(res.coverage_estimate) * 100).toFixed(2) + '%')
  )
}

function workerReset(res: Record<string, unknown>): unknown {
  if (!res.ok)
    return toolError(
      'backend_error',
      'WORKER reset 失败 · ' + text(res.error || 'unknown'),
      '用 blender_rt_worker op=list 查看会话；必要时重新 op=start',
    )
  return { text: 'WORKER reset ok · objects=' + text(res.objects) }
}

function workerExec(res: Record<string, unknown>): unknown {
  if (!res.ok)
    return toolError(
      'backend_error',
      'WORKER exec 失败 · ' + text(res.error || 'unknown'),
      '用 blender_rt_worker op=start 检查会话；或改用 blender_rt_headless',
      res.traceback ? redactSecrets(res.traceback).slice(0, 1500) : undefined,
    )
  const parts: string[] = []
  parts.push('WORKER exec ok · ' + text(res.ms) + 'ms')
  if (res.result) parts.push('result: ' + JSON.stringify(res.result).slice(0, 2000))
  if (res.stdout && String(res.stdout).trim())
    parts.push('--- stdout ---' + LF + redactSecrets(res.stdout).slice(-2500))
  if (res.stderr && String(res.stderr).trim())
    parts.push('--- stderr ---' + LF + redactSecrets(res.stderr).slice(-1200))
  return { text: parts.join(LF) }
}

function workerList(res: Record<string, unknown>): unknown {
  const ws = asArray(res.workers)
  const lines = ws.length
    ? ws.map((raw) => {
        const w = asRecord(raw)
        return (
          '- ' +
          text(w.name) +
          (w.alive
            ? w.ready
              ? ' · 热（pid ' +
                text(w.pid) +
                '，端口 ' +
                text(w.port) +
                '，已运行 ' +
                String(Math.round(Number(w.uptimeMs || 0) / 1000)) +
                's）'
              : ' · 进程在但未就绪'
            : ' · 未启动（端口 ' + text(w.port) + '）')
        )
      })
    : ['- （还没有任何 worker 实例；op=start 起一个）']
  return {
    text: 'WORKER list ok · ' + String(ws.length) + ' 个实例' + LF + lines.join(LF),
  }
}

function workerStatus(op: string, res: Record<string, unknown>): unknown {
  const gpu = asRecord(res.gpu)
  const after = asRecord(gpu.after)
  const gpuEnabled = asArray(after.gpu_enabled)
  const parts: string[] = []
  parts.push(
    'WORKER ' +
      op +
      ' ok · ' +
      JSON.stringify({
        name: res.name,
        alive: res.alive,
        ready: res.ready,
        pid: res.pid,
        port: res.port,
        uptimeMs: res.uptimeMs,
      }),
  )
  if (res.gpu)
    parts.push(
      'GPU：' +
        (gpu.fell_back_to_cpu ? '⚠️ 回落 CPU' : '✅ ' + text(after.device_type)) +
        ' ' +
        gpuEnabled.map(text).join(','),
    )
  if (res.status) parts.push('status: ' + JSON.stringify(res.status))
  if (res.lastError) parts.push('lastError: ' + text(res.lastError))
  if (res.stdoutTail) parts.push('worker stdout 尾：' + redactSecrets(res.stdoutTail).slice(-800))
  return { text: parts.join(LF) }
}
