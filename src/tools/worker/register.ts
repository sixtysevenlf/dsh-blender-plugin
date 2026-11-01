/** blender_rt_worker 工具声明与注册（schema/参数描述是模型契约，逐字保持）。 */
import { vTool } from '../../contracts/tool.js'
import { ANY_SCHEMA } from '../schema-diagnostic.js'
import { T } from '../../constants.js'
import { renderOne } from '../../ui/cards.js'
import { executeWorker } from './execute.js'
import type { HostContext } from '../../host/context.js'

function buildWorkerTool(ctx: HostContext, port: number) {
  return vTool({
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
        description: '实例名（默认 default）；起多个热会话做批量小活，脏了用 restart 换新会话',
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
      return executeWorker(args, ctx, port)
    },
  })
}

export function registerWorker(ctx: HostContext, port: number): void {
  ctx.effect(() => ctx.tools.register(buildWorkerTool(ctx, port)), 'dsh-blender-plugin: rt-worker')
}
