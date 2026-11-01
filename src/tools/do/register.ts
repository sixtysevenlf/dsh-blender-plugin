/** blender_rt_do 工具声明与注册（schema/参数描述是模型契约，逐字保持）。 */
import { vTool } from '../../contracts/tool.js'
import type { HostContext } from '../../host/context.js'
import { ANY_SCHEMA, HINT_KERNEL } from '../schema-diagnostic.js'
import { T } from '../../constants.js'
import { renderOne } from '../../ui/cards.js'
import { executeDo } from './execute.js'

function buildDoTool(ctx: HostContext, port: number) {
  return vTool({
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
        description:
          'Python 代码（在 Blender 主线程执行；预置 bpy/math/mathutils/Vector 与持久内核 K）。与 file 二选一',
      },
      file: {
        type: 'string',
        description:
          '要执行的 .py 文件路径（WSL / Windows / 相对当前 .blend 均可）—— 长脚本用这个，不必塞进入参',
      },
      see: { type: 'boolean', description: '是否同时回一帧视口（默认 true）' },
      max_size: { type: 'integer', description: '回帧最长边像素，默认 560' },
      force: {
        type: 'boolean',
        description: '回帧与上一帧 hash 完全相同时默认**不重复附图**；传 true 强制重发',
      },
      session: { type: 'string', description: '无头交互会话名（复用同一 blender -b）' },
      auto: { type: 'boolean', description: 'GUI 不可用时降级无头 session（默认 true）' },
      preload: { type: 'string', description: '无头 session 预载模块（逗号分隔）' },
      from: { type: 'string', description: '无头 see 相机位置 "x,y,z"' },
      look_at: { type: 'string', description: '无头 see 看向点 "x,y,z"' },
    },
    output: { schema: ANY_SCHEMA, render: renderOne },
    isConcurrencySafe: () => false,
    timeoutMs: T.act,
    async execute(args: Record<string, unknown>) {
      return executeDo(args, ctx, port)
    },
  })
}

export function registerDo(ctx: HostContext, port: number): void {
  ctx.effect(() => ctx.tools.register(buildDoTool(ctx, port)), 'dsh-blender-plugin: rt-do')
}
