import { vTool } from '../contracts/tool.js'
import { ANY_SCHEMA } from './schema-diagnostic.js'
import { T, BACKEND_HINT } from '../constants.js'
import { ensureBackend } from '../host/backend.js'
import { backendPost, jsonPayload, leasedText } from '../host/http.js'
import { renderOne } from '../ui/cards.js'

export function registerPerf(ctx: any, port: number): void {
  ctx.effect(
    () =>
      ctx.tools.register(
        vTool({
          name: 'blender_rt_perf',
          description:
            '【渲染性能】Cycles CPU 瓶颈诊断与优化预设。op=analyze 差分实测「每轮同步 / 每采样 GPU 成本」（占主线程十几秒）；op=apply 应用预设（persistent_data / OptiX 降噪 / denoising_use_gpu / auto_tile off / 采样上限）；op=revert 还原 apply 之前；op=status 看当前设置与已存快照；op=help 契约。实测背景：2318 对象场景每轮 CPU 侧同步 ≈4.2 s、GPU 单采样 ≈0.2 s（1080p）、首次 kernel JIT ≈15 s；persistent_data 让重复渲染 14.6 s → 0.79 s。',
          parameters: {
            op: { type: 'string', required: true, description: 'status | analyze | apply | revert | help' },
            args: {
              type: 'json',
              description:
                'analyze{pct=25,low=1,high=4}；apply{samples=1024,persistent=true,denoiser="OPTIX",denoise_gpu=true,auto_tile=false}'
            }
          },
          output: { schema: ANY_SCHEMA, render: renderOne },
          isConcurrencySafe: () => false,
          timeoutMs: T.perf,
          async execute(args: any) {
            if (!(await ensureBackend(port))) return { text: '后端不可用（127.0.0.1:' + String(port) + '）。' + BACKEND_HINT }
            const op = String((args && args.op) || 'status')
            // ⚠ 无参时必须给 undefined（给 {} 会让 python 侧多收一个位置参数：实测 "_dsh_perf_status_cycles() takes 0 positional arguments"）
            const pargs = jsonPayload(args && args.args)
            const r = await backendPost(port, '/perf', { op: op, args: Object.keys(pargs).length ? pargs : undefined }, 600000)
            const lt = leasedText(r)
            if (lt) return { text: lt }
            if (!r || r.ok !== true) return { text: 'PERF ' + op + ' 失败 · ' + String((r && (r.error || r.raw)) || 'unknown') }
            return { text: 'PERF ' + op + ' ' + JSON.stringify(r.result) }
          }
        })
      ),
    '@dsh-external/dsh-blender-plugin: rt-perf'
  )
}
