import { vTool } from '../contracts/tool.js'
import { ANY_SCHEMA } from './schema-diagnostic.js'
import { T, BACKEND_HINT } from '../constants.js'
import { ensureBackend } from '../host/backend/index.js'
import { backendJson } from '../host/http.js'
import { renderOne } from '../ui/cards.js'
import { toolError } from './errors.js'
import { asArray, asRecord, text } from '../contracts/dynamic.js'
import type { HostContext } from '../host/context.js'

export function registerCommands(ctx: HostContext, port: number): void {
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
            if (!(await ensureBackend(port)))
              return toolError(
                'backend_unreachable',
                '后端不可用（127.0.0.1:' + String(port) + '）',
                BACKEND_HINT,
              )
            const r = await backendJson(port, '/commands', 120000)
            if (!r || r.ok !== true)
              return toolError(
                'backend_error',
                'COMMANDS 失败 · ' + text(r.error || r.raw || 'unknown'),
                '先 blender_viewport op=status 确认后端与 addon',
              )
            const disabled = asArray(r.disabled)
            const integrations = asRecord(r.integrations)
            const available = asArray(r.available)
            const lines: string[] = []
            lines.push(
              '场景 ' + text(r.scene) + (r.file ? ' · 文件 ' + text(r.file) : ' · 未保存文件'),
            )
            lines.push(
              '可用命令 ' +
                text(r.total) +
                ' 条；被集成开关挡住 ' +
                String(disabled.length) +
                ' 条',
            )
            lines.push(
              '集成：' +
                Object.keys(integrations)
                  .map((k: string) => {
                    const it = asRecord(integrations[k])
                    return k + '=' + (it.enabled ? 'on' : 'off')
                  })
                  .join(' · '),
            )
            for (const k of Object.keys(integrations)) {
              const it = asRecord(integrations[k])
              if (!it.enabled && it.message) lines.push('  [' + k + '] ' + text(it.message))
            }
            if (disabled.length) lines.push('被挡住：' + disabled.map(text).join(', '))
            lines.push('可用：' + available.map(text).join(', '))
            return { text: lines.join(String.fromCharCode(10)) }
          },
        }),
      ),
    'dsh-blender-plugin: rt-commands',
  )
}
