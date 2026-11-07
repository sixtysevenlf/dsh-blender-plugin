import { vTool } from '../contracts/tool.js'
import { ANY_SCHEMA } from './schema-diagnostic.js'
import { T, BACKEND_HINT } from '../constants.js'
import { ensureBackend } from '../host/backend/index.js'
import { backendPost, leasedText } from '../host/http.js'
import { renderOne } from '../ui/cards.js'
import { toolError } from './errors.js'
import { text } from '../contracts/dynamic.js'
import type { HostContext } from '../host/context.js'

// 3.1 removed the write/asset/telemetry command surface from the add-on. The
// names stay here only to turn a stale model call into an upgrade hint; they
// are not registered anywhere and no alias exists.
const REMOVED_COMMANDS = new Set([
  'export_scene',
  'set_texture',
  'download_polyhaven_asset',
  'get_polyhaven_asset_preview',
  'create_rodin_job',
  'import_generated_asset',
  'download_sketchfab_model',
  'download_polypizza_model',
  'create_hunyuan_job',
  'import_generated_asset_hunyuan',
  'get_telemetry_consent',
  'set_telemetry_consent',
  'drain_human_activity',
])

export function registerCmd(ctx: HostContext, port: number): void {
  ctx.effect(
    () =>
      ctx.tools.register(
        vTool({
          name: 'blender_rt_cmd',
          description:
            '直连调用 Blender addon 的命令（25 条：18 常驻 + 7 集成门控；其中 21 条只读、4 条内部传输），含 get_world_state_snapshot / get_addon_info 以及资产类只读查询（PolyHaven / Sketchfab / Poly Pizza / Hyper3D / Hunyuan3D）。仅 GUI addon 在线时可用；纯无头请用 rt_worker / rt_headless。参数必须匹配 addon 真实签名：get_scene_info 无参、get_object_info 用 name（不是 object_name）。先用 blender_rt_commands 看清单与可用性。',
          parameters: {
            name: {
              type: 'string',
              required: true,
              description: 'addon 命令名，如 get_world_state_snapshot、search_polyhaven_assets',
            },
            params: { type: 'json', description: '参数对象（可选），如 {"asset_type":"hdris"}' },
            timeout_ms: {
              type: 'integer',
              description: '超时毫秒，默认 120000（下载/生成类可调大）',
            },
          },
          output: { schema: ANY_SCHEMA, render: renderOne },
          isConcurrencySafe: () => false,
          timeoutMs: T.cmd,
          async execute(args: Record<string, unknown>) {
            if (!(await ensureBackend(port)))
              return toolError(
                'backend_unreachable',
                '后端不可用（127.0.0.1:' + String(port) + '）',
                BACKEND_HINT,
              )
            const name = text(args.name)
            if (!name)
              return toolError(
                'invalid_params',
                'blender_rt_cmd 需要 name 参数',
                '传 name=<addon 命令>；blender_rt_commands 查看 25 条命令',
              )
            if (REMOVED_COMMANDS.has(name))
              return toolError(
                'command_removed',
                'addon 命令 ' + name + ' 在 3.1 已删除',
                '3.1 addon 只保留 21 条只读 + 4 条内部传输命令；写场景、资产下载/生成导入、遥测类命令不再注册，也不提供兼容别名。用 blender_rt_commands 查看当前命令面',
              )
            const r = await backendPost(
              port,
              '/cmd',
              {
                name: name,
                params: args.params || {},
                timeoutMs: Number(args.timeout_ms || 120000),
              },
              300000,
            )
            const lt = leasedText(r)
            if (lt) return { text: lt }
            if (!r || r.ok !== true)
              return toolError(
                'backend_error',
                'CMD ' + name + ' 失败 · ' + text(r.error || r.raw || 'unknown'),
                '用 blender_rt_commands 核对命令名与参数',
              )
            const payload = JSON.stringify(r.result)
            const head =
              'CMD ' + name + ' ok · ' + text(r.ms) + 'ms · ' + String(payload.length) + ' chars'
            const newline = String.fromCharCode(10)
            return {
              text:
                head +
                newline +
                (payload.length > 3000 ? payload.slice(0, 3000) + newline + '…(已截断)' : payload),
            }
          },
        }),
      ),
    'dsh-blender-plugin: rt-cmd',
  )
}
