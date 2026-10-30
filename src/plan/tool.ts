import { vTool } from '../contracts/tool.js'
import { ANY_SCHEMA } from '../tools/schema-diagnostic.js'
import { BACKEND_HINT, PLUGIN_VERSION, T } from '../constants.js'
import { ensureBackend } from '../host/backend.js'
import { backendPost, jsonPayload, leasedText } from '../host/http.js'
import { renderStructured } from '../ui/cards.js'
import { finishPlanResponse, planViaWorker } from './headless-target.js'

export function registerPlan(ctx: any, port: number): void {
  ctx.effect(
    () =>
      ctx.tools.register(
        vTool({
          name: 'blender_rt_plan',
          description:
            '[v' +
            PLUGIN_VERSION +
            '] 【判定 / 验收 / 导出 / 造型】34 个 family · 228 个 op；有 op 不许自写等价几何/验收。' +
            '**不确定用哪个就先问目录：op="catalog"**（本地直出，不占 Blender 往返；含"什么时候用 / 别用 / 最小骨架"）；' +
            '单族展开 op="catalog", args={family:"audit"}；算子细节用各 family 自己的 <family>_help。' +
            '常用：audit_* · qc_render_views|qc_compare · sculpt_* · fix_* · uv_* · print_report · sweep_* · ' +
            'deliver_* · motion_* · generator_save|run（save 用 **code=**）· material_* · render_lock|render_status · render_state|render_wait · ' +
            'asm_*（装配扫描/manifest/merge/gate）· blockout_* · img_digest|probe|measure|calib · ' +
            'gate_preflight|checkpoint|deliver · ' +
            'visualreport_report · shapesolve_solve · surface_loft|sweep|patch|validate。' +
            '契约层判据见 docs/假设驱动建模-cookbook.md；写 op 过租约，只读 op 豁免。' +
            '传 session="auto" 或无头 worker 名时按 family 自动起 worker 同步执行（不依赖 GUI）；首次约 2 s，之后复用。' +
            'op 名拼错回 did_you_mean（带骨架）；参数名写错回"不认识的参数"。',

          parameters: {
            op: { type: 'string', required: true, description: '契约 op 或 plan_<op>（见工具描述；op=help / plan_help 出速查）' },
            args: { type: 'json', description: 'op 的参数对象，例如 {"cid":"joint","err":184,"tolerance":220,"identifiable":["dy"]}' },
            path: { type: 'string', description: 'op=report 时的输出路径（Markdown）；省略则只回文本' },
            attach_images: { type: 'boolean', description: '产物图附图回模型（≤2 张；img_digest/qc_compare 默认开）' },
            session: { type: 'string', description: '无头 worker 会话；"auto"=自动起' }
          },
          output: { schema: ANY_SCHEMA, render: renderStructured },
          isConcurrencySafe: () => false,
          timeoutMs: T.plan,
          async execute(argsIn: any) {
            if (!(await ensureBackend(port))) return { text: '后端不可用（127.0.0.1:' + String(port) + '）。' + BACKEND_HINT }
            const op = String((argsIn && argsIn.op) || 'status')
            const payload: any = jsonPayload(argsIn && argsIn.args)
            if (
              !Object.keys(payload).length &&
              argsIn &&
              typeof argsIn.args === 'string' &&
              argsIn.args.trim() &&
              argsIn.args.trim() !== '{}'
            ) {
              return { text: 'PLAN ' + op + ' 失败 · args 不是合法 JSON 对象：' + String(argsIn.args).slice(0, 200) }
            }
            if (op === 'report' && argsIn && argsIn.path) payload.path = String(argsIn.path)
            const sessRaw = argsIn && argsIn.session ? String(argsIn.session) : ''
            if (sessRaw) {
              const pr = await planViaWorker(ctx, port, op, payload, sessRaw === 'auto' ? undefined : sessRaw)
              if (!pr || pr.ok !== true) {
                return {
                  text:
                    'PLAN ' +
                    op +
                    ' 无头执行失败 · ' +
                    String((pr && pr.error) || 'unknown') +
                    (pr && pr.hint ? String.fromCharCode(10) + String(pr.hint) : '') +
                    String.fromCharCode(10) +
                    '提示：先 blender_rt_worker(op="start", name="…", preload="…")，或传 session="auto"'
                }
              }
              return await finishPlanResponse(ctx, op, pr.res, argsIn)
            }
            const r = await backendPost(port, '/plan', { op: op, args: payload }, 300000)
            const lt = leasedText(r)
            if (lt) return { text: lt }
            if (!r || r.ok !== true) return { text: 'PLAN ' + op + ' 失败 · ' + String((r && (r.error || r.raw)) || 'unknown') }
            return await finishPlanResponse(ctx, op, r.result, argsIn)
          }
        })
      ),
    '@dsh-external/dsh-blender-plugin: rt-plan'
  )
}
