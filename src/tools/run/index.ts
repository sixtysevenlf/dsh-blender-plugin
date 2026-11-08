/** blender_run：唯一作业入口（render / headless 有界队列）。 */
import { vTool } from '../../contracts/tool.js'
import type { HostContext } from '../../host/context.js'
import { ANY_SCHEMA } from '../schema-diagnostic.js'
import { T } from '../../constants.js'
import { renderOne } from '../../ui/cards.js'
import { executeRun } from './execute.js'

const ACTIONS = ['submit', 'status', 'wait', 'collect', 'cancel', 'list', 'perf', 'tune', 'help']

function buildRunTool(ctx: HostContext, port: number) {
  return vTool({
    name: 'blender_run',
    description:
      '【作业 / 渲染】唯一作业入口：submit 立即返回 jobId（无同步模式），之后 status/wait/collect/cancel/list/perf。' +
      'kind=render 是声明式最终渲染（相机、帧区间、分辨率、采样、引擎、输出文件；不接受 script；支持相机动画）；kind=headless 是 fresh blender -b 跑脚本（script/script_file/file/outdir/timeout_ms/preload/args/env...）。' +
      '队列有界：pending 64、GPU 1、CPU 2；满队列返回 QUEUE_FULL。wait 默认 120s、上限 600s，超时不取消作业。' +
      'collect 取日志与产物；cancel 幂等并清进程树。action=tune 保留旧 Cycles 渲染分析（status/analyze/apply/revert）。' +
      '旧工具迁移：blender_rt_headless / blender_rt_job → action=submit（kind=headless / render）；blender_rt_perf → action=tune。',
    parameters: {
      action: {
        type: 'string',
        description: ACTIONS.join(' | ') + '（默认：带 kind 则 submit，否则 list）',
      },
      kind: { type: 'string', description: 'submit：render（声明式渲染）| headless（跑脚本）' },
      id: { type: 'string', description: 'status/wait/collect/cancel 的 jobId' },
      priority: {
        type: 'integer',
        description: 'submit：队列优先级（-100..100，默认 0；越大越优先）',
      },
      timeout_ms: {
        type: 'integer',
        description:
          'wait：等待上限毫秒（默认 120000，最大 600000，超时不取消）；headless：进程上限',
      },
      tail: { type: 'integer', description: 'collect：日志尾部字符数，默认 4000' },
      state: {
        type: 'string',
        description: 'list：按状态过滤 queued/assigned/running/completed/failed/canceled',
      },
      owner: { type: 'string', description: 'list：按 DSH session id 过滤' },
      file: { type: 'string', description: 'render：.blend 路径；headless：可选 .blend' },
      output: { type: 'string', description: 'render：输出文件模板（如 //render/frame_####）' },
      outdir: { type: 'string', description: 'render/headless：输出目录' },
      frames: {
        type: 'json',
        description: 'render：帧区间 [start,end]、"1-10" 或单帧 5；给了区间自动加 -a（相机动画）',
      },
      resolution: { type: 'json', description: 'render：[width,height]' },
      samples: {
        type: 'integer',
        description: 'render：采样数（Cycles samples / EEVEE taa_render_samples）',
      },
      engine: {
        type: 'string',
        description: 'render/headless：渲染引擎（CYCLES / BLENDER_EEVEE_NEXT / BLENDER_WORKBENCH）',
      },
      format: { type: 'string', description: 'render：图片格式（PNG/JPEG/OPEN_EXR…）' },
      camera: { type: 'string', description: 'render：camera=true 用场景相机，或相机对象名' },
      script: { type: 'string', description: 'headless：Python 脚本内容（--python 临时文件）' },
      script_file: { type: 'string', description: 'headless：磁盘上的 .py 脚本路径' },
      preload: { type: 'string', description: 'headless：预载模块（逗号分隔）' },
      factory_startup: { type: 'boolean', description: 'headless：--factory-startup（默认 true）' },
      bootstrap: { type: 'boolean', description: 'headless：是否注入持久内核 K（默认 true）' },
      workdir: { type: 'string', description: 'headless：chdir + sys.path 首位' },
      include_noise: { type: 'boolean', description: 'headless：产物清单是否含噪音文件' },
      out_json: { type: 'string', description: 'headless：结构化结果落盘路径' },
      args: { type: 'string', description: 'headless：追加到 -- 之后的命令行参数；tune：参数对象' },
      env: { type: 'json', description: 'headless：子进程环境变量 {KEY:"VALUE"}' },
      op: {
        type: 'string',
        description: 'tune：status | analyze | apply | revert | help（默认 status）',
      },
    },
    output: { schema: ANY_SCHEMA, render: renderOne },
    isConcurrencySafe: () => false,
    timeoutMs: T.headless,
    async execute(args: Record<string, unknown>) {
      return executeRun(port, args)
    },
  })
}

export function registerRun(ctx: HostContext, port: number): void {
  ctx.effect(() => ctx.tools.register(buildRunTool(ctx, port)), 'dsh-blender-plugin: run')
}
