/** blender_viewport 工具声明与注册（schema/参数描述是模型契约，逐字保持）。 */
import { vTool } from '../../contracts/tool.js'
import { T } from '../../constants.js'
import { executeViewport } from './execute.js'
import { renderViewportOutput } from './presenter.js'
import type { HostContext } from '../../host/context.js'

function buildViewportTool(port: number) {
  return vTool({
    name: 'blender_viewport',
    description:
      '实时通道后端（127.0.0.1:' +
      String(port) +
      '）运维入口：**status**（健康/视口区域/计数/版本）、**doctor**（真跑一次 bpy 往返的三级体检：未连 / 主线程忙 / addon 线程卡死 + 修法）、' +
      '**start / stop / restart**（后端进程与 15 s 看护）、**who / lease / release**（写租约；被别人持有时写路由 409，只读 op 豁免）、' +
      '**launch**（一键拉起 GUI Blender 并自动 Connect addon：写 boot 脚本 → detached spawn → **轮询 addon 端口**（唯一可信判据）→ 顺手 doctor；幂等）。' +
      '参数细节见 blender_rt_plan(op="catalog", args={tool:"viewport"})。',
    parameters: {
      op: {
        type: 'string',
        required: true,
        description: 'status | doctor | who | lease | release | start | stop | restart | launch',
      },
      holder: {
        type: 'string',
        description: 'lease/release 的持有者名（默认本插件进程 pid 标识）',
      },
      ttl_ms: {
        type: 'integer',
        description: 'lease 有效期毫秒，默认 600000；写操作会自动续期',
      },
      force: {
        type: 'boolean',
        description: 'lease 时抢占别人的租约（默认 false，被占用返回 409）',
      },
      scope: {
        type: 'string',
        description:
          '【lease】写作用域（第二维）：collection:COL_Lights 或 prefix:LGT- ，逗号分隔；声明式，会出现在 who/lease/409 回执里（装配层用 K.route_collections() 真正归位）',
      },
      wait_ms: {
        type: 'integer',
        description: '【launch】等 addon 端口打开的上限，默认 90000',
      },
      file: {
        type: 'string',
        description: '【launch】启动时要打开的 .blend（WSL/Windows 路径都收）',
      },
      exe: { type: 'string', description: '【launch】显式指定 blender.exe（覆盖配置探测）' },
      addon_module: {
        type: 'string',
        description:
          '【launch】要 enable 的 addon 模块名（逗号分隔）；默认自动扫名字带 mcp / blender_bridge / dshbb 的模块（旧名与新名都能扫到）',
      },
      addon_file: {
        type: 'string',
        description: '【launch】按文件 import 的 addon .py 绝对路径（extension 布局兜底）',
      },
      dry_run: {
        type: 'boolean',
        description: '【launch】true = 只回报将执行的命令，不真的启动',
      },
    },
    output: {
      schema: { type: 'string' },
      render: renderViewportOutput,
    },
    timeoutMs: T.viewport,
    async execute(args: Record<string, unknown>) {
      return executeViewport(args, port)
    },
  })
}

export function registerViewport(ctx: HostContext, port: number): void {
  ctx.effect(
    () => ctx.tools.register(buildViewportTool(port)),
    '@dsh-external/dsh-blender-plugin: tool',
  )
}
