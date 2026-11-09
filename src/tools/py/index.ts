/** blender_py：在 ready 的 GUI 会话里执行 Python（不启动 headless）。 */
import { vTool } from '../../contracts/tool.js'
import type { HostContext } from '../../host/context.js'
import { ANY_SCHEMA } from '../schema-diagnostic.js'
import { T } from '../../constants.js'
import { renderOne } from '../../ui/cards.js'
import { requireReadySession } from '../shared/session.js'
import { executePy } from './execute.js'

function buildPyTool(ctx: HostContext, port: number) {
  return vTool({
    name: 'blender_py',
    description:
      '【执行 Python】在 ready 的 GUI 会话里跑 agent 写的 Python（/act 通道，固定 exclusive，不卡队列）。' +
      '没有会话直接拒绝，不会启动 headless、不会自动降级。脚本可以重跑；超过 ~1s 的重活请改用 blender_run(kind=headless)。' +
      '可选 see=true（默认）跑完顺手抓当前视口一帧；同画面默认不重复附图。' +
      '旧工具迁移：blender_rt_do → blender_py；旧 blender_rt_watch 的 code 执行 → blender_py。',
    parameters: {
      code: { type: 'string', description: '要执行的 Python 源码（与 file 二选一）' },
      file: { type: 'string', description: '要执行的 .py 文件路径（与 code 二选一）' },
      see: { type: 'boolean', description: '跑完是否抓当前视口一帧（默认 true）' },
      max_size: { type: 'integer', description: 'see 的最长边像素，默认 560' },
      force: { type: 'boolean', description: 'see：同画面默认不重复附图，true 强制重发' },
      wait_ms: {
        type: 'integer',
        description: '等待其它 py 释放的毫秒数（0–5000，默认 0；占用中返回 py_busy）',
      },
      session_id: {
        type: 'string',
        description: '目标 GUI 会话 id；多实例必填，blender_session action=list 可看',
      },
    },
    output: { schema: ANY_SCHEMA, render: renderOne },
    isConcurrencySafe: () => false,
    timeoutMs: T.act,
    async execute(args: Record<string, unknown>) {
      const ready = await requireReadySession(port, undefined, String(args.session_id || '').trim())
      if (!ready.ok) return ready.failure
      return executePy(ctx, port, args, ready.session)
    },
  })
}

export function registerPy(ctx: HostContext, port: number): void {
  ctx.effect(() => ctx.tools.register(buildPyTool(ctx, port)), 'dsh-blender-plugin: py')
}
