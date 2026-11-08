import { vTool } from '../../contracts/tool.js'
import { ANY_SCHEMA } from '../schema-diagnostic.js'
import { T } from '../../constants.js'
import type { HostContext } from '../../host/context.js'
import { executeLoop } from './execute.js'
import { renderOne } from '../../ui/cards.js'

export function registerLoop(ctx: HostContext, port: number): void {
  ctx.effect(
    () =>
      ctx.tools.register(
        vTool({
          name: 'blender_rt_loop',
          description:
            '【内环搜索】把"要试几十~几千组参数"的活从模型回合搬进 Blender 主线程（bpy.app.timers，160 tick/s，迭代/时间双上限 + 急停）：你只写目标与区间，机器去搜。' +
            '**任务形状对上了就用**：对齐 / 拟合 / 反推尺寸 / 参数扫描 —— 判据能写成一个数（轮廓 IoU、包围盒尺寸误差、剖面差，或你自写的 score），候选几十~几千组。只试 ≤5 组 → 用 rt_do 自己循环更省事。' +
            '**GUI 走 timers（160 tick/s）；无头请传 target="session"**（走 worker 的 dsh_loop_sync 阻塞循环，不依赖 timers；需 worker 已 start 且 preload 带 runner，工具会自动起）。' +
            'setup 只跑一次，step/measure 共享 ns（预置 bpy/K/random/np/i/frac/penalize/anneal/record）；measure 必须给 ns["score"]（参数写 ns["params"]）；现成目标函数 K.dsh_measure = {silhouette_iou, aabb_err, profile_err}。' +
            '节奏：op=start → status → board（候选表）→ export（best 落可复现脚本，setup 里定义 apply(p) 才会自动重放）。' +
            '**内环只优化你写的目标函数：收敛后必须换另一条通路复核 + rt_see 看一眼**（防 Goodhart）；跑完场景停在最后一次迭代的参数上，不是 best。' +
            'spec 字段、ns 契约与可照抄模板见 blender_rt_plan(op="catalog", args={tool:"rt_loop"})。',
          parameters: {
            op: {
              type: 'string',
              required: true,
              description: 'start | status | stop | board | export | help | bench',
            },
            target: {
              type: 'string',
              description:
                'gui（默认，timers）| session（无头 worker 同步阻塞，推荐 headless 主路径）',
            },
            session: {
              type: 'string',
              description: 'target=session 时的 worker 实例名（默认 dsh-loop）',
            },
            spec: {
              type: 'json',
              description:
                'op=start 的规格：{setup, step, measure, iterations, budget_ms, interval, measure_every, minimize, top_k, group_key, redraw_every}',
            },
            history: { type: 'integer', description: 'op=status 返回的指标尾迹长度，默认 8' },
            board: {
              type: 'integer',
              description: 'op=status 时同时返回前 N 个候选（默认 0=不返回）',
            },
            limit: { type: 'integer', description: 'op=board 的候选条数，默认 10' },
            groups: { type: 'boolean', description: 'op=board 是否包含分组候选（默认 true）' },
            path: {
              type: 'string',
              description:
                'op=export 写出脚本的路径（宿主可见路径，如 /tmp/xxx.py 或 D:\\out\\xxx.py），省略=只回文本',
            },
            top: { type: 'integer', description: 'op=export 的变体数量（配 include_variants）' },
            include_variants: { type: 'boolean', description: 'op=export 是否把候选表一起导出' },
            note: { type: 'string', description: 'op=export 的备注（写进脚本头部）' },
            iterations: { type: 'integer', description: 'op=bench 的迭代次数' },
          },
          output: { schema: ANY_SCHEMA, render: renderOne },
          isConcurrencySafe: () => false,
          timeoutMs: T.loop,
          async execute(args: Record<string, unknown>) {
            return executeLoop(port, args)
          },
        }),
      ),
    'dsh-blender-plugin: rt-loop',
  )
}
