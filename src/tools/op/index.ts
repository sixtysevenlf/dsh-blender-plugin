/** blender_op：catalog 算子（plan）+ loop / opt / preset 子面。 */
import { vTool } from '../../contracts/tool.js'
import type { HostContext } from '../../host/context.js'
import { ANY_SCHEMA } from '../schema-diagnostic.js'
import { T } from '../../constants.js'
import { renderOne } from '../../ui/cards.js'
import { executeOp } from './execute.js'

const ACTIONS = ['plan', 'loop', 'opt', 'preset', 'help']

function buildOpTool(ctx: HostContext, port: number) {
  return vTool({
    name: 'blender_op',
    description:
      '【算子】执行 catalog 的 plan 算子（34 family / 228 op；op=catalog 看目录），以及参数搜索 loop、对象精简 opt、配方库 preset。' +
      '执行型 op 需要 ready 的 GUI 会话；catalog / 只读 op / args.dry_run=true 不需要会话，也不会启动 Blender。' +
      '旧工具迁移：blender_rt_plan → action=plan；blender_rt_loop → action=loop；blender_rt_opt → action=opt；blender_rt_preset → action=preset。' +
      '写文件/改场景的算子按 write scope 申请 grant；写类操作需要人类未暂停。',
    parameters: {
      action: { type: 'string', description: ACTIONS.join(' | ') + '（默认 plan）' },
      op: {
        type: 'string',
        description:
          'plan：catalog 算子名（如 audit_mesh / img_digest / shape_solve；op=help 列 family）；loop: start|status|stop|board|export|help|bench；opt: analyze|join|help；preset: list|get|apply|save|delete|export|import|help',
      },
      args: {
        type: 'json',
        description: 'plan/opt/preset 的参数对象（JSON），例如 {"family":"audit"}',
      },
      path: { type: 'string', description: 'plan report / loop export 的输出路径（可选）' },
      attach_images: {
        type: 'boolean',
        description: 'plan：是否把结果里的图附图（默认按 op 决定）',
      },
      session: {
        type: 'string',
        description: 'loop target=session 时的无头 worker 名（默认 dsh-loop）',
      },
      target: {
        type: 'string',
        description: 'loop：gui（默认，GUI 内环）| session（持久无头 worker）',
      },
      spec: {
        type: 'json',
        description: 'loop start：{setup, step, measure, iterations, budget_ms, ...}',
      },
      history: { type: 'integer', description: 'loop status：返回的指标尾迹长度，默认 8' },
      board: { type: 'integer', description: 'loop status：同时返回前 N 个候选，默认 0' },
      limit: { type: 'integer', description: 'loop board：候选条数，默认 10' },
      groups: { type: 'boolean', description: 'loop board：是否包含分组候选（默认 true）' },
      top: { type: 'integer', description: 'loop export：变体数量，默认 1' },
      include_variants: { type: 'boolean', description: 'loop export：是否导出候选表' },
      note: { type: 'string', description: 'loop export / preset save：备注' },
      iterations: { type: 'integer', description: 'loop bench：迭代次数，默认 5000' },
      name: { type: 'string', description: 'preset：配方名（get/apply/delete/save）' },
      kind: { type: 'string', description: 'preset：分类（material/object/scene）' },
      tags: { type: 'string', description: 'preset：标签（逗号分隔）' },
      names: {
        type: 'string',
        description: 'preset export：要导出的配方名（逗号分隔；省略=全部）',
      },
      targets: { type: 'string', description: 'preset apply：MAT:名 / OBJ:名 / SCENE（逗号分隔）' },
      data: { type: 'json', description: 'preset save：点路径 JSON，如 {"inputs.Roughness":0.4}' },
      overwrite: { type: 'boolean', description: 'preset save/import：是否覆盖同名（默认 true）' },
      session_id: {
        type: 'string',
        description: '目标 GUI 会话 id；多实例必填',
      },
      write_scope: {
        type: 'string',
        description:
          'exclusive（默认）| collection:<名> | prefix:<前缀> | object:<名>；只读 op 不需要',
      },
    },
    output: { schema: ANY_SCHEMA, render: renderOne },
    isConcurrencySafe: () => false,
    timeoutMs: T.opt,
    async execute(args: Record<string, unknown>) {
      return executeOp(ctx, port, args)
    },
  })
}

export function registerOp(ctx: HostContext, port: number): void {
  ctx.effect(() => ctx.tools.register(buildOpTool(ctx, port)), 'dsh-blender-plugin: op')
}
