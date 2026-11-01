/** blender_rt_headless 工具声明与注册（schema/参数描述是模型契约，逐字保持）。 */
import { vTool } from '../../contracts/tool.js'
import { ANY_SCHEMA } from '../schema-diagnostic.js'
import { IS_WIN, T } from '../../constants.js'
import { renderStructured } from '../../ui/cards.js'
import { executeHeadless } from './execute.js'
import type { HostContext } from '../../host/context.js'

function buildHeadlessTool(ctx: HostContext, port: number) {
  return vTool({
    name: 'blender_rt_headless',
    description:
      '【无头 Blender · 备用】独立进程跑脚本（blender -b）：GUI 不可用/批处理/长活用；不占 GUI、不动场景。' +
      (IS_WIN ? '⚠ 给 Windows 路径（D:\\x\\y.py）；' : '⚠ cwd 在 UNC 下，WSL 路径要带开头 /；') +
      'script_file 自动给 __file__ + sys.path。' +
      '批量几何 / 校验 / 渲染 / 不需要"人在回路看视口"的活都先走这里；"看一眼"用 rt_do/rt_see。' +
      'print("HEADLESS " + json.dumps(obj)) 回传（单行 JSON）。' +
      '默认 factory-startup + EEVEE 前导（engine="cycles"|"keep"|"none" 可改）。' +
      '**>100 s 或长渲染：as_job=true**（rt_job 收）；等待窗口到点回 promoted jobId，结果仍可收。' +
      'attach_images 可回图；shots 多视角；preload 后直调 K.dsh_*_api。' +
      '参数细节见 catalog(tool:rt_headless)。',
    parameters: {
      script: {
        type: 'string',
        description: 'Python 源码（注入 K + bpy/math/mathutils/Vector）',
      },
      script_file: {
        type: 'string',
        description: '直接跑一个 .py 文件（Windows/WSL 路径都行）；比 script= 省事',
      },
      file: {
        type: 'string',
        description: '要打开的 .blend（回执带 inputFile{size,mtime,md5}）；传 .py 会自动当脚本',
      },
      outdir: {
        type: 'string',
        description: '产物目录（WSL 路径也收）；跑完列出新文件，并追加到脚本 sys.argv',
      },
      as_job: {
        type: 'boolean',
        description: 'true = 立刻后台化成作业（预期 >100 s 的活推荐直接用）',
      },
      wait_s: { type: 'number', description: 'as_job 之后最多再等几秒，到点回 jobId' },
      timeout_ms: {
        type: 'integer',
        description: '子进程上限，默认 180000，上限 1800000（到点 SIGKILL）',
      },
      engine: {
        type: 'string',
        description: 'eevee（默认）| cycles | keep | none（省 1–1.5 s）',
      },
      preload: {
        type: 'string',
        description: '预载 runtime 模块（逗号分隔，如 "audit,qc,qc_render"）→ K.dsh_*_api',
      },
      shots: {
        type: 'json',
        description: '多视角出图 [{name,from,look_at,lens,res,samples}]',
      },
      out_json: {
        type: 'string',
        description: '结构化结果落盘路径（>4KB 也会自动落 results/）',
      },
      env: { type: 'json', description: '子进程环境变量 {KEY:"VALUE"}' },
      args: {
        type: 'string',
        description: '追加到 -- 之后的命令行参数（脚本里读 sys.argv / K.args）',
      },
      workdir: { type: 'string', description: '脚本内 chdir + sys.path 首位' },
      factory_startup: {
        type: 'boolean',
        description: '默认 true；false = 用用户启动文件与偏好',
      },
      use_user_config: {
        type: 'boolean',
        description: '透传 BLENDER_USER_CONFIG/SCRIPTS 给无头进程',
      },
      bootstrap: { type: 'boolean', description: '默认 true = 注入持久内核 K' },
      gpu: { type: 'string', description: '仅 cycles：auto | true（必须 GPU）| false' },
      include_noise: { type: 'boolean', description: '产物清单含 __pycache__/*.blend1' },
      attach_images: { type: 'json', description: 'shots 附图：true=3 张，"sheet"=拼图' },
      hard_exit: {
        type: 'boolean',
        description:
          'done 后 os._exit(0) 跳过退出拆解；默认 true 规避 5.2.3 退出 AV，false=旧行为（atexit/handlers 照常）',
      },
    },
    output: { schema: ANY_SCHEMA, render: renderStructured },
    isConcurrencySafe: () => false,
    timeoutMs: T.headless,
    async execute(args: Record<string, unknown>) {
      return executeHeadless(args, ctx, port)
    },
  })
}

export function registerHeadless(ctx: HostContext, port: number): void {
  ctx.effect(
    () => ctx.tools.register(buildHeadlessTool(ctx, port)),
    'dsh-blender-plugin: rt-headless',
  )
}
