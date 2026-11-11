/** blender_inspect：只读命令目录与执行（help 只列可执行只读命令）。 */
import { vTool } from '../../contracts/tool.js'
import type { HostContext } from '../../host/context.js'
import { ANY_SCHEMA } from '../schema-diagnostic.js'
import { T } from '../../constants.js'
import { renderOne } from '../../ui/cards.js'
import { executeInspect } from './execute.js'

function buildInspectTool(ctx: HostContext, port: number) {
  return vTool({
    name: 'blender_inspect',
    description:
      '【只读检查】help 列出 addon 的可执行只读命令（name(args) / enabled / pause_safe / description）；command=<name> args={...} 执行其中一条。' +
      '内部传输命令（execute_code / get_viewport_screenshot / send_*）不列出也不可执行：执行 Python 用 blender_py，截图/看视口用 blender_see，作业用 blender_run。' +
      '已知写命令返回 wrong_tool 并指向正确工具；未知命令返回 unknown_command + did_you_mean。' +
      '旧工具迁移：blender_rt_commands → action=help；blender_rt_cmd 的只读面 → command=<name>（写命令改走 blender_py / blender_op / blender_run / blender_session）。',
    parameters: {
      command: {
        type: 'string',
        description: '只读命令名；省略=help（列目录）。参数见 help 的 name(args)',
      },
      args: { type: 'json', description: '命令参数对象，例如 {"name":"Cube"}（可选）' },
      session_id: {
        type: 'string',
        description: '目标 GUI 会话 id；多实例必填（action=list 可看）',
      },
    },
    output: { schema: ANY_SCHEMA, render: renderOne },
    isConcurrencySafe: () => true,
    timeoutMs: T.cmd,
    async execute(args: Record<string, unknown>) {
      return executeInspect(port, args)
    },
  })
}

export function registerInspect(ctx: HostContext, port: number): void {
  ctx.effect(() => ctx.tools.register(buildInspectTool(ctx, port)), 'dsh-blender-plugin: inspect')
}
