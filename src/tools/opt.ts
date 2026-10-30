import { vTool } from '../contracts/tool.js'
import { ANY_SCHEMA } from './schema-diagnostic.js'
import { T, BACKEND_HINT } from '../constants.js'
import { ensureBackend } from '../host/backend.js'
import { backendPost, jsonPayload, leasedText } from '../host/http.js'
import { renderOne } from '../ui/cards.js'

export function registerOpt(ctx: any, port: number): void {
  ctx.effect(
    () =>
      ctx.tools.register(
        vTool({
          name: 'blender_rt_opt',
          description:
            '【对象精简】降 Cycles 每轮场景同步成本（实测 ≈1.4 ms/对象）。op=analyze 列出可安全合并的分组与预计节省；op=join 执行合并（**默认 dry_run=true 只报告**；dry_run=false 才真合并，强烈建议同时给 save_before=<.blend 绝对路径> 先存回退点）。合并规则：同集合 / 同材质 / 同父级 / 无修改器 / 无动画 / 无形态键 / 无自定义属性 / 无实例 / 非库链接。合并后**几何零损失**（面数与顶点数不变），合并对象名为 `<前缀>_MERGED_<材质>`。',
          parameters: {
            op: { type: 'string', required: true, description: 'analyze | join | help' },
            args: { type: 'json', description: 'join{dry_run=true, save_before="D:/.../xxx_before_join.blend"}' }
          },
          output: { schema: ANY_SCHEMA, render: renderOne },
          isConcurrencySafe: () => false,
          timeoutMs: T.opt,
          async execute(args: any) {
            if (!(await ensureBackend(port))) return { text: '后端不可用（127.0.0.1:' + String(port) + '）。' + BACKEND_HINT }
            const op = String((args && args.op) || 'analyze')
            const oargs = jsonPayload(args && args.args)
            const r = await backendPost(
              port,
              '/opt',
              { op: op === 'join' ? 'opt_join' : 'opt_analyze', args: Object.keys(oargs).length ? oargs : undefined },
              600000
            )
            const lt = leasedText(r)
            if (lt) return { text: lt }
            if (!r || r.ok !== true) return { text: 'OPT ' + op + ' 失败 · ' + String((r && (r.error || r.raw)) || 'unknown') }
            return { text: 'OPT ' + op + ' ' + JSON.stringify(r.result) }
          }
        })
      ),
    '@dsh-external/dsh-blender-plugin: rt-opt'
  )
}
