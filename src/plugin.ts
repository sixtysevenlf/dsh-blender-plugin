/**
 * dsh-blender-plugin —— host 侧生命周期与工具面装配。
 *
 * 这里只做装配：配置 schema / 看护 / 六个模型工具注册 / 测试接缝。
 * 具体实现按层拆在 contracts/ host/ tools/ plan/ headless/ utils/ ui/。
 */
import { PLUGIN_VERSION } from './constants.js'
import { z, schemaResolve } from './contracts/tool.js'
import { losslessSanitize } from './contracts/lossless.js'
import { asRecord } from './contracts/dynamic.js'
import { resolveHttpPort } from './host/backend/index.js'
import { registerSchemaDiagnostic } from './tools/schema-diagnostic.js'
import { registerLifecycle } from './plugin-lifecycle.js'
import { registerToolSurface, registerTuiSurface } from './plugin-surfaces.js'
import {
  configureVirtualGuiPermission,
  registerVirtualGuiPermissionRpc,
} from './host/virtual-gui-permission.js'
import { ensureGpuDiyAsset, ensureHostHomeLayout } from './host/dnn-home.js'
import { errorLine, redactSecrets, toolError } from './tools/errors.js'
import {
  BUNDLED_SKILL_DIR,
  BUNDLED_SKILL_PROVIDER,
  BUNDLED_SKILL_RANK,
  createBundledSkillProvider,
  parseSkillFrontmatter,
  readBundledSkill,
  registerBundledSkill,
} from './host/skill.js'
import { frameDedupe, resetFrameCache } from './utils/frame-cache.js'
import {
  HEADLESS_WAIT_MS,
  headlessReceipt,
  promotedReceipt,
  receiptEnvelope,
} from './headless/receipts/index.js'
import { CARD_SPECS, cardPresenters } from './ui/cards.js'
import {
  DNN_DOC_URL,
  applyPluginSettingsEnv,
  normalizePluginSettings,
  repairPluginSettings,
  validatePluginSettings,
} from './host/plugin-settings.js'
import type { HostContext } from './host/context.js'

export const name = 'dsh-blender-plugin'
export const inject = ['tools']

export interface Config {
  port: number
  autoStart: boolean
  /** v2.6.1：dsh-TUI 宿主半边（选中 chip / 页脚字段 / @ 提及 / /blender 命令）；只在有对应接缝的宿主生效 */
  tui: boolean
  /** 3.1：默认是否允许 agent 启动 virtual-gui；运行时可在 Plugins → Blender 配置卡或 Presence 面板开关 */
  allowVirtualGui?: boolean
  /** 3.2：实验性 DNN（默认关闭）；打开后 vision_detect 才可用 */
  features?: { dnn?: boolean }
  /** 3.2：render pass 缓存上限（条目数 / 字节数 / 保留天数） */
  cache?: {
    render?: { maxEntries?: number; maxBytes?: number; ttlDays?: number }
  }
  /** 3.2：vision worker 解释器与模型选择；空字符串表示自动发现 */
  vision?: {
    python?: string
    venv?: string
    model?: string
    dnn?: { backend?: 'opencv' | 'onnxruntime'; device?: 'cpu' | 'auto' | 'gpu' }
  }
}

// v1.0.1：z 不可用时（降级模式）不给 schema —— 宿主不会因为这里再抛一次而整体失败。
export const Config = z
  ? z.object({
      port: z.natural().default(resolveHttpPort()),
      autoStart: z.boolean().default(true),
      tui: z.boolean().default(true),
      allowVirtualGui: z.boolean().default(false),
      features: z.object({
        dnn: z
          .boolean()
          .default(false)
          .experimental()
          .description('实验性：启用 OpenCV DNN 目标检测（默认关闭）'),
      }),
      cache: z.object({
        render: z.object({
          maxEntries: z
            .natural()
            .min(1)
            .max(32)
            .default(8)
            .description('render pass 缓存最多保留的条目数（1-32）'),
          maxBytes: z
            .natural()
            .min(512 * 1024 * 1024)
            .max(16 * 1024 * 1024 * 1024)
            .default(4 * 1024 * 1024 * 1024)
            .description('render pass 缓存总字节上限（512MiB-16GiB）'),
          ttlDays: z
            .natural()
            .min(1)
            .max(30)
            .default(7)
            .description('render pass 缓存保留天数（1-30）'),
        }),
      }),
      vision: z
        .object({
          python: z
            .string()
            .default('')
            .description('自定义 Python 解释器绝对路径；空字符串表示自动发现'),
          venv: z
            .string()
            .default('')
            .description('自定义 venv 绝对路径；非空时跳过托管 venv 安装'),
          model: z
            .string()
            .default('yolov5n')
            .pattern(/^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/)
            .description('vision.models.yml 里的模型 id（默认 yolov5n）'),
          dnn: z
            .object({
              backend: z
                .union(['opencv', 'onnxruntime'])
                .default('opencv')
                .description('opencv 开箱可用；onnxruntime 需自备 venv（GPU 见文档）'),
              device: z
                .union(['cpu', 'auto', 'gpu'])
                .default('cpu')
                .description('opencv 后端只支持 cpu；auto/gpu 需要 onnxruntime'),
            })
            .link(DNN_DOC_URL),
        })
        .description('OpenCV vision worker 配置；GPU/自定义后端见文档'),
    })
  : undefined

/** 把插件配置规范化、校验、修复后写进网关的环境变量（降级模式也要写）。 */
function applyPluginSettings(input: unknown): void {
  const normalized = normalizePluginSettings(input)
  const check = validatePluginSettings(normalized)
  const warnIssues = (issues: readonly unknown[]): void => {
    for (const issue of issues) {
      console.warn(
        '[dsh-blender] 插件设置 cache.render 非法，已回落文档默认值：' + JSON.stringify(issue),
      )
    }
  }
  if (check.ok) {
    warnIssues(applyPluginSettingsEnv(input))
    if (check.warnings.length > 0) {
      console.warn('[dsh-blender] 插件设置提示：' + check.warnings.join('；'))
    }
    return
  }
  const repaired = repairPluginSettings(normalized)
  console.warn(
    '[dsh-blender] 插件设置非法，已回落安全值：' +
      check.errors.join('；') +
      (repaired.repairs.length > 0 ? '；' + repaired.repairs.join('；') : ''),
  )
  warnIssues(applyPluginSettingsEnv(input, repaired.settings))
}

export function apply(ctx: HostContext, config: Config): void {
  // v2.2.0：随包 skill 自动注册（可选能力；失败不影响工具面）。
  // 放在 degraded 分支之前 —— 即使 schemastery 解析失败、工具降级，技能内容也仍然可用。
  registerBundledSkill(ctx)
  // 3.2：即使 schemastery 不可用也要把用户设置交给网关子进程。
  applyPluginSettings(config)
  // home 是唯一持久数据根：先在 apply() 里幂等创建。失败时带结构化
  // home_unwritable（doctor 的 config.home.layout 读同一结果），需要写盘的
  // 子系统各自跳过，而不是逐个炸。
  const homeLayout = ensureHostHomeLayout()
  if (!homeLayout.ok) {
    console.warn('[dsh-blender] home_unwritable: ' + JSON.stringify(homeLayout.error))
  }

  // v1.0.1（issue #10）：解析失败 ⇒ 降级但不消失（见 registerSchemaDiagnostic）。
  if (!z) {
    registerSchemaDiagnostic(ctx)
    return
  }

  // 防御性读配置：schema 正常时宿主会补默认值，但降级/宿主差异都不该让这里再抛。
  const cfgIn = asRecord(config)
  const port =
    Number.isFinite(Number(cfgIn.port)) && Number(cfgIn.port) > 0
      ? Number(cfgIn.port)
      : resolveHttpPort()
  const autoStart = cfgIn.autoStart === undefined ? true : !!cfgIn.autoStart
  // 3.1：virtual-gui 默认不允许；面板会话内开关优先，插件重载回到配置默认。
  configureVirtualGuiPermission(cfgIn.allowVirtualGui === true)
  // 3.7：模型目录缺失时放一份中英双语 GPU-DIY 快捷说明；已存在则绝不覆盖。
  // 失败只影响可发现性，不阻断插件激活；home 不可写时跳过写盘。
  if (homeLayout.ok) ensureGpuDiyAsset()
  registerVirtualGuiPermissionRpc(ctx, port)

  // 注册顺序：TUI → 看护/后端 effects → 六个模型工具。
  // Cordis 逆序释放 effects，先注册 TUI/生命周期、后注册工具，卸载时才会先释放工具再杀后端。
  registerTuiSurface(ctx, port, cfgIn)
  registerLifecycle(ctx, port, autoStart)
  registerToolSurface(ctx, port)
}

/* ────────────────────────────────────────────────────────────────────────────
 * v0.9.4：**测试接缝**。tests/lossless_guard.mjs 直接 import 本模块，就能拿到
 *   ① 真实回执构造器（receiptEnvelope / headlessReceipt / promotedReceipt）
 *   ② 出口消毒器（losslessSanitize）
 * 然后用**宿主的真校验器**（dsh-util-values 的 snapshotJsonValue）断言：
 * "本插件任何一个回执都过得了 lossless-JSON 门"。
 * 这是把「返回通道」从"靠人记得别写 undefined"变成"机器每次回归都验"的唯一办法。
 * ──────────────────────────────────────────────────────────────────────────── */
export const __internals = {
  frameDedupe: frameDedupe,
  resetFrameCache: resetFrameCache,
  losslessSanitize: losslessSanitize,
  toolError: toolError,
  redactSecrets: redactSecrets,
  errorLine: errorLine,
  receiptEnvelope: receiptEnvelope,
  headlessReceipt: headlessReceipt,
  promotedReceipt: promotedReceipt,
  HEADLESS_WAIT_MS: HEADLESS_WAIT_MS,
  PLUGIN_VERSION: PLUGIN_VERSION,
  schemaResolve: schemaResolve,
  bundledSkill: {
    dir: BUNDLED_SKILL_DIR,
    provider: BUNDLED_SKILL_PROVIDER,
    rank: BUNDLED_SKILL_RANK,
    read: readBundledSkill,
    createProvider: createBundledSkillProvider,
    register: registerBundledSkill,
    parseFrontmatter: parseSkillFrontmatter,
  },
  cards: { specs: CARD_SPECS, presenters: cardPresenters },
}
