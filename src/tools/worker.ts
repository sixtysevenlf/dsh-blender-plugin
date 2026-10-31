import { vTool } from '../contracts/tool.js'
import { ANY_SCHEMA } from './schema-diagnostic.js'
import { T, BACKEND_HINT } from '../constants.js'
import { ensureBackend } from '../host/backend.js'
import { backendPost, leasedText, numTriple } from '../host/http.js'
import { toAttachment, readLocalPng } from '../utils/image.js'
import { renderOne } from '../ui/cards.js'
import { redactSecrets, toolError } from './errors.js'
import { asArray, asRecord, text } from '../contracts/dynamic.js'
import type { HostContext } from '../host/context.js'

export function registerWorker(ctx: HostContext, port: number): void {
  ctx.effect(
    () =>
      ctx.tools.register(
        vTool({
          name: 'blender_rt_worker',
          description:
            '【交互式无头热会话】常驻 blender -b 进程：start 拉起 / exec 跑代码 / see 离屏出一帧（Workbench）/ watch 逐帧采样 / reset 清场景 / status / stop / restart / list。' +
            'start 可带 preload（把 imgtools/assembly/blockout/visualreport/shapesolve 等模块注入 K）与 warmup；see 在无 GUI 时就是 AI 的视口，每步 50–100 ms。' +
            '同一 session 的场景与 K 跨调用保留；空闲 TTL 默认 15 min（DSH_WORKER_TTL_MS 可调）。' +
            '串行、无窗口：依赖 GUI 的 bpy.ops 可能失败；参数细节见 blender_rt_plan(op="catalog", args={tool:"rt_worker"})。',
          parameters: {
            op: {
              type: 'string',
              required: true,
              description: 'start | exec | see | reset | status | stop | restart | list',
            },
            name: {
              type: 'string',
              description:
                '实例名（默认 default）；起多个热会话做批量小活，脏了用 restart 换新会话',
            },
            code: {
              type: 'string',
              description: 'op=exec 的 Python 源码（预置 K/bpy/math/mathutils/Vector）',
            },
            timeout_ms: {
              type: 'integer',
              description: 'op=exec 的响应超时，默认 120000；长代码调大',
            },
            preload: {
              type: 'string',
              description:
                'op=start 时预载模块（逗号分隔，如 "audit,qc,runner"）；preload 失败 start 直接报错',
            },
            warmup: { type: 'boolean', description: 'op=start 预热渲染器（默认 true）' },
            from: { type: 'string', description: 'op=see 相机位置 "x,y,z"' },
            look_at: { type: 'string', description: 'op=see 看向点 "x,y,z"' },
            lens: { type: 'number', description: 'op=see 焦距 mm' },
            ortho: { type: 'boolean', description: 'op=see 正交投影' },
            view_size: { type: 'string', description: 'op=see 分辨率 "宽x高"' },
            gpu: {
              type: 'string',
              description: 'op=start 的 GPU 语义（仅 cycles）：auto / true / false',
            },
            engine: {
              type: 'string',
              description: 'op=start 的渲染引擎：eevee（默认）/ cycles / workbench / keep',
            },
            purge_prefix: {
              type: 'string',
              description:
                'op=exec 前清掉的模块前缀（逗号分隔）—— 改了用户模块必须清，否则 import 命中旧代码',
            },
          },
          output: { schema: ANY_SCHEMA, render: renderOne },
          isConcurrencySafe: () => false,
          timeoutMs: T.worker,
          async execute(args: Record<string, unknown>) {
            if (!(await ensureBackend(port)))
              return toolError(
                'backend_unreachable',
                '后端不可用（127.0.0.1:' + String(port) + '）',
                BACKEND_HINT,
              )
            const op = text(args.op) || 'status'
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
            const budget = 60000 + Number(body.timeoutMs || 120000)
            const r = await backendPost(port, '/worker', body, budget)
            const lt = leasedText(r)
            if (lt) return { text: lt }
            if (!r || r.ok !== true) {
              const rr = asRecord(r.result)
              const bits: string[] = []
              bits.push('WORKER ' + op + ' 失败 · ' + text(r.error || rr.error || 'unknown'))
              if (rr.traceback)
                bits.push(
                  '--- traceback ---' +
                    String.fromCharCode(10) +
                    redactSecrets(rr.traceback).slice(0, 2000),
                )
              if (rr.stderr && String(rr.stderr).trim())
                bits.push('stderr: ' + redactSecrets(rr.stderr).slice(0, 1200))
              if (rr.stdout && String(rr.stdout).trim())
                bits.push('stdout: ' + redactSecrets(rr.stdout).slice(0, 800))
              if (r.hint) bits.push(text(r.hint))
              return toolError(
                'backend_error',
                bits.join(String.fromCharCode(10)),
                '检查后端 /worker 路由；先 blender_viewport op=doctor 看进程与端口',
              )
            }
            const res = asRecord(r.result)
            if (op === 'see') {
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
              const textOut =
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
              return att.ref
                ? { text: textOut, image: att.ref }
                : {
                    text:
                      textOut +
                      String.fromCharCode(10) +
                      '⚠ 图片未回传：' +
                      text(att.why || '未知原因'),
                  }
            }
            if (op === 'reset') {
              if (!res.ok)
                return toolError(
                  'backend_error',
                  'WORKER reset 失败 · ' + text(res.error || 'unknown'),
                  '用 blender_rt_worker op=list 查看会话；必要时重新 op=start',
                )
              return { text: 'WORKER reset ok · objects=' + text(res.objects) }
            }
            if (op === 'exec') {
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
                parts.push(
                  '--- stdout ---' +
                    String.fromCharCode(10) +
                    redactSecrets(res.stdout).slice(-2500),
                )
              if (res.stderr && String(res.stderr).trim())
                parts.push(
                  '--- stderr ---' +
                    String.fromCharCode(10) +
                    redactSecrets(res.stderr).slice(-1200),
                )
              return { text: parts.join(String.fromCharCode(10)) }
            }
            if (op === 'list') {
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
                text:
                  'WORKER list ok · ' +
                  String(ws.length) +
                  ' 个实例' +
                  String.fromCharCode(10) +
                  lines.join(String.fromCharCode(10)),
              }
            }
            const gpu = asRecord(res.gpu)
            const after = asRecord(gpu.after)
            const gpuEnabled = asArray(after.gpu_enabled)
            const parts2: string[] = []
            parts2.push(
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
              parts2.push(
                'GPU：' +
                  (gpu.fell_back_to_cpu ? '⚠️ 回落 CPU' : '✅ ' + text(after.device_type)) +
                  ' ' +
                  gpuEnabled.map(text).join(','),
              )
            if (res.status) parts2.push('status: ' + JSON.stringify(res.status))
            if (res.lastError) parts2.push('lastError: ' + text(res.lastError))
            if (res.stdoutTail)
              parts2.push('worker stdout 尾：' + redactSecrets(res.stdoutTail).slice(-800))
            return { text: parts2.join(String.fromCharCode(10)) }
          },
        }),
      ),
    '@dsh-external/dsh-blender-plugin: rt-worker',
  )
}
