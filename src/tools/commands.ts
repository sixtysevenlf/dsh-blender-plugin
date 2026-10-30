import { vTool } from '../contracts/tool.js'
import { ANY_SCHEMA } from './schema-diagnostic.js'
import { T, BACKEND_HINT } from '../constants.js'
import { ensureBackend } from '../host/backend.js'
import { backendJson } from '../host/http.js'
import { renderOne } from '../ui/cards.js'

export function registerCommands(ctx: any, port: number): void {
  ctx.effect(
    () =>
      ctx.tools.register(
        vTool({
          name: 'blender_rt_commands',
          description:
            '列出直连通道当前可用的 addon 命令（19 常驻 + 15 集成门控）与 5 个集成的真实状态（开关是否打开、是否缺 API key）。调 blender_rt_cmd 之前先用它。',
          parameters: {},
          output: { schema: ANY_SCHEMA, render: renderOne },
          isConcurrencySafe: () => true,
          timeoutMs: T.commands,
          async execute() {
            if (!(await ensureBackend(port))) return { text: '后端不可用（127.0.0.1:' + String(port) + '）。' + BACKEND_HINT }
            const r = await backendJson(port, '/commands', 120000)
            if (!r || r.ok !== true) return { text: 'COMMANDS 失败 · ' + String((r && (r.error || r.raw)) || 'unknown') }
            const lines: string[] = []
            lines.push('场景 ' + String(r.scene) + (r.file ? ' · 文件 ' + String(r.file) : ' · 未保存文件'))
            lines.push('可用命令 ' + String(r.total) + ' 条；被集成开关挡住 ' + String((r.disabled || []).length) + ' 条')
            lines.push(
              '集成：' +
                Object.keys(r.integrations || {})
                  .map((k: string) => k + '=' + (r.integrations[k].enabled ? 'on' : 'off'))
                  .join(' · ')
            )
            for (const k of Object.keys(r.integrations || {})) {
              const it = r.integrations[k]
              if (!it.enabled && it.message) lines.push('  [' + k + '] ' + it.message)
            }
            if ((r.disabled || []).length) lines.push('被挡住：' + r.disabled.join(', '))
            lines.push('可用：' + (r.available || []).join(', '))
            return { text: lines.join('\n') }
          }
        })
      ),
    '@dsh-external/dsh-blender-plugin: rt-commands'
  )
}
