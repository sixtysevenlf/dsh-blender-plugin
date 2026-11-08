import { vTool } from '../../contracts/tool.js'
import type { HostContext } from '../../host/context.js'
import { ANY_SCHEMA } from '../schema-diagnostic.js'
import { T } from '../../constants.js'
import { renderOne } from '../../ui/cards.js'
import { executeWatch } from './execute.js'

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
            return executeWatch(ctx, port, args)
          },
        }),
      ),
    'dsh-blender-plugin: rt-watch',
  )
}
