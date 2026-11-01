/** blender_rt_job 工具声明与注册（schema/参数描述是模型契约，逐字保持）。 */
import { vTool } from '../../contracts/tool.js'
import { ANY_SCHEMA } from '../schema-diagnostic.js'
import { T } from '../../constants.js'
import { renderStructured } from '../../ui/cards.js'
import { executeJob } from './execute.js'
import type { HostContext } from '../../host/context.js'

function buildJobTool(port: number) {
  return vTool({
    name: 'blender_rt_job',
    description:
      '【作业层】长活后台化：op=start 立刻返回 jobId（不占客户端连接、不会被工具超时掐断）；status / collect / **wait** / kill / list。' +
      '**wait 一次拿到结构化结果（默认 120 s/次，上限 600 s）—— 别再连发 status**（会撞宿主的重复调用检测）。' +
      '与 headless 的分工：预期 <100 s 用 headless 直接拿结果；更长或已知要跑很久 → headless 的 as_job=true，或本工具 op=start。' +
      'run 与 job **同一 id 空间**：headless 的 runId（run-…）也能用 status/collect/wait/kill 收；日志落 <outdir>/jobs/<id>/。' +
      '参数细节见 blender_rt_plan(op="catalog", args={tool:"rt_job"})。',
    parameters: {
      op: {
        type: 'string',
        required: true,
        description: 'start | status | collect | wait | kill | list',
      },
      id: {
        type: 'string',
        description: 'status/collect/wait/kill 的 id（job-… 或 headless 的 run-…）',
      },
      script: {
        type: 'string',
        description: 'Python 源码（与 headless 同一套前导与结果契约）',
      },
      script_file: { type: 'string', description: '直接跑 .py 文件（Windows/WSL 路径都收）' },
      args: {
        type: 'string',
        description: '命令行参数（空格分隔或数组）；脚本里读 sys.argv / K.args',
      },
      env: { type: 'json', description: '额外环境变量 {KEY:"VALUE"}' },
      file: {
        type: 'string',
        description: '要打开的 .blend（回执带 inputFile{size,mtime,md5}）',
      },
      outdir: {
        type: 'string',
        description: '产物目录（WSL 路径也收）；日志在其 jobs/<id>/ 下',
      },
      timeout_ms: {
        type: 'integer',
        description:
          '作业上限，默认 3600000，上限 24 h；wait 时是本次等待上限（默认 120000，上限 600000）',
      },
      engine: { type: 'string', description: 'eevee（默认）/ cycles / keep' },
      preload: { type: 'string', description: '预载 runtime 模块（逗号分隔）→ K.dsh_*_api' },
      factory_startup: { type: 'boolean', description: '默认 true = --factory-startup' },
      bootstrap: { type: 'boolean', description: '默认 true = 注入持久内核 K' },
      workdir: { type: 'string', description: '脚本内 chdir + sys.path 首位' },
      include_noise: {
        type: 'boolean',
        description: '产物清单是否含噪音文件（默认 false 过滤）',
      },
      out_json: { type: 'string', description: '结构化结果落盘路径（>4KB 自动落 results/）' },
      tail: {
        type: 'integer',
        description: 'collect/wait 的 stdout/stderr 尾部字符数（默认 4000）',
      },
    },
    output: { schema: ANY_SCHEMA, render: renderStructured },
    isConcurrencySafe: () => true,
    timeoutMs: T.job,
    async execute(args: Record<string, unknown>) {
      return executeJob(args, port)
    },
  })
}

export function registerJob(ctx: HostContext, port: number): void {
  ctx.effect(
    () => ctx.tools.register(buildJobTool(port)),
    '@dsh-external/dsh-blender-plugin: rt-job',
  )
}
