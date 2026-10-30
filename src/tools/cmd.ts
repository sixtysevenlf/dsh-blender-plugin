import { vTool } from '../contracts/tool.js'
import { ANY_SCHEMA } from './schema-diagnostic.js'
import { T, BACKEND_HINT } from '../constants.js'
import { ensureBackend } from '../host/backend.js'
import { backendPost, leasedText } from '../host/http.js'
import { renderOne } from '../ui/cards.js'

export function registerCmd(ctx: any, port: number): void {
  ctx.effect(
    () =>
      ctx.tools.register(
        vTool({
          name: 'blender_rt_cmd',
          description:
            '直连调用 Blender addon 的任意命令（39 个名字：24 常驻 + 15 集成门控），含 get_world_state_snapshot / drain_human_activity / get_telemetry_consent / set_telemetry_consent / get_addon_info，以及资产类命令（PolyHaven / Sketchfab / Poly Pizza / Hyper3D / Hunyuan3D）。仅 GUI addon 在线时可用；纯无头请用 rt_worker / rt_headless。参数必须匹配 addon 真实签名：get_scene_info 无参、get_object_info 用 name（不是 object_name）。先用 blender_rt_commands 看清单与可用性。',
          parameters: {
            name: { type: 'string', required: true, description: 'addon 命令名，如 get_world_state_snapshot、search_polyhaven_assets' },
            params: { type: 'json', description: '参数对象（可选），如 {"asset_type":"hdris"}' },
            timeout_ms: { type: 'integer', description: '超时毫秒，默认 120000（下载/生成类可调大）' }
          },
          output: { schema: ANY_SCHEMA, render: renderOne },
          isConcurrencySafe: () => false,
          timeoutMs: T.cmd,
          async execute(args: any) {
            if (!(await ensureBackend(port))) return { text: '后端不可用（127.0.0.1:' + String(port) + '）。' + BACKEND_HINT }
            const name = String((args && args.name) || '')
            if (!name) return { text: 'blender_rt_cmd 需要 name 参数' }
            const r = await backendPost(
              port,
              '/cmd',
              { name: name, params: (args && args.params) || {}, timeoutMs: Number((args && args.timeout_ms) || 120000) },
              300000
            )
            const lt = leasedText(r)
            if (lt) return { text: lt }
            if (!r || r.ok !== true) return { text: 'CMD ' + name + ' 失败 · ' + String((r && (r.error || r.raw)) || 'unknown') }
            const payload = JSON.stringify(r.result)
            const head = 'CMD ' + name + ' ok · ' + String(r.ms) + 'ms · ' + String(payload.length) + ' chars'
            return { text: head + '\n' + (payload.length > 3000 ? payload.slice(0, 3000) + '\n…(已截断)' : payload) }
          }
        })
      ),
    '@dsh-external/dsh-blender-plugin: rt-cmd'
  )
}
