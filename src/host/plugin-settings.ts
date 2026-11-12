/**
 * 插件设置（3.2）——host 侧配置的规范化、校验与进程环境序列化。
 *
 * 宿主把插件配置卡里的值作为 `config` 传给 `apply()`。网关是子进程，只能从环境变量
 * 拿到这份配置，所以这里负责：
 *   1. 从任意（可能降级/残缺）输入里读出合法值；
 *   2. 校验跨字段约束（backend=opencv 只支持 cpu；venv 必须是绝对路径…）；
 *   3. 序列化成稳定的 JSON 写进 `DSH_BLENDER_PLUGIN_CONFIG`。
 *
 * 本模块不 import runtime/：host 与 backend 是两份编译产物，只靠环境变量与 HTTP 通信。
 * 也不承载秘密：真正的模型/解释器路径是普通配置，密钥属于宿主凭据系统，不在这里。
 */
import * as path from 'node:path'

/** 插件设置环境变量：网关子进程启动时继承，是 host → runtime 的唯一配置通道。 */
export const PLUGIN_SETTINGS_ENV = 'DSH_BLENDER_PLUGIN_CONFIG'

/** DNN 实验性能力说明链接（文档随包发布）。 */
export const DNN_DOC_URL =
  'https://github.com/sixtysevenlf/dsh-blender-plugin/blob/main/docs/vision-custom-backend.md'

export type DnnBackend = 'opencv' | 'onnxruntime'
export type DnnDevice = 'cpu' | 'auto' | 'gpu'

export interface RenderCacheSettings {
  maxEntries: number
  maxBytes: number
  ttlDays: number
}

export interface PluginSettings {
  features: { dnn: boolean }
  cache: { render: RenderCacheSettings }
  vision: {
    python: string
    venv: string
    model: string
    dnn: { backend: DnnBackend; device: DnnDevice }
  }
}

/** 字节数按 1024 进制；这里用二进制 GiB/MiB，和队列/缓存实现保持一致。 */
const MIB = 1024 * 1024
const GIB = 1024 * MIB

const DEFAULT_SETTINGS: PluginSettings = {
  features: { dnn: false },
  cache: {
    render: {
      maxEntries: 8,
      maxBytes: 4 * GIB,
      ttlDays: 7,
    },
  },
  vision: {
    python: '',
    venv: '',
    model: 'yolov5n',
    dnn: { backend: 'opencv', device: 'cpu' },
  },
}

export const DEFAULT_PLUGIN_SETTINGS: Readonly<PluginSettings> = Object.freeze(DEFAULT_SETTINGS)

export const LIMITS = Object.freeze({
  cacheMaxEntries: { min: 1, max: 32 },
  cacheMaxBytes: { min: 512 * MIB, max: 16 * GIB },
  cacheTtlDays: { min: 1, max: 30 },
  modelId: /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/,
})

const DNN_BACKENDS: readonly DnnBackend[] = ['opencv', 'onnxruntime']
const DNN_DEVICES: readonly DnnDevice[] = ['cpu', 'auto', 'gpu']

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function readRecord(value: unknown): Record<string, unknown> {
  return isRecord(value) ? value : {}
}

function readBoolean(value: unknown, fallback: boolean): boolean {
  return typeof value === 'boolean' ? value : fallback
}

function readString(value: unknown, fallback: string): string {
  return typeof value === 'string' ? value : fallback
}

/**
 * One rejected cache.render.* value, structurally identical to the Gateway's
 * RenderCacheLimitIssue (runtime/core/queue/render-cache-key.ts) so doctor and
 * the host report the same field/value/range/default tuple. src/ and runtime/
 * are separate builds, so the shape is mirrored here instead of imported.
 */
export interface RenderCacheLimitIssue {
  field: string
  value: unknown
  allowed: string
  min: number
  max: number
  reason: string
  used: number
}

interface CacheRule {
  key: keyof RenderCacheSettings
  min: number
  max: number
  fallback: number
}

const CACHE_RULES: readonly CacheRule[] = [
  {
    key: 'maxEntries',
    min: LIMITS.cacheMaxEntries.min,
    max: LIMITS.cacheMaxEntries.max,
    fallback: DEFAULT_PLUGIN_SETTINGS.cache.render.maxEntries,
  },
  {
    key: 'maxBytes',
    min: LIMITS.cacheMaxBytes.min,
    max: LIMITS.cacheMaxBytes.max,
    fallback: DEFAULT_PLUGIN_SETTINGS.cache.render.maxBytes,
  },
  {
    key: 'ttlDays',
    min: LIMITS.cacheTtlDays.min,
    max: LIMITS.cacheTtlDays.max,
    fallback: DEFAULT_PLUGIN_SETTINGS.cache.render.ttlDays,
  },
]

/**
 * Never clamp: an invalid value becomes the documented default AND an issue, so
 * doctor sees exactly what the user wrote and DSH settings behave like a config
 * file with the same invalid value.
 */
function normalizeCacheLimit(
  value: unknown,
  rule: CacheRule,
  issues: RenderCacheLimitIssue[],
): number {
  if (value === undefined || value === null) return rule.fallback
  const isInteger = typeof value === 'number' && Number.isInteger(value)
  if (isInteger && value >= rule.min && value <= rule.max) return value
  issues.push({
    field: 'cache.render.' + rule.key,
    value,
    allowed: '整数 ' + String(rule.min) + '..' + String(rule.max),
    min: rule.min,
    max: rule.max,
    reason: isInteger ? 'out-of-range' : 'not-an-integer',
    used: rule.fallback,
  })
  return rule.fallback
}

function readEnum<T extends string>(value: unknown, allowed: readonly T[], fallback: T): T {
  return typeof value === 'string' && (allowed as readonly string[]).includes(value)
    ? (value as T)
    : fallback
}

function isAbsolutePath(value: string): boolean {
  return path.isAbsolute(value) || path.win32.isAbsolute(value)
}

/**
 * 容错读取配置：类型不对/缺字段回落默认，非法数值回落**文档默认值**并记录 issue
 * （不夹取、不四舍五入）。语义上非法但类型合法的值（backend=opencv + device=gpu、
 * 相对 venv 路径、坏 model id）故意保留下来，交给 {@link validatePluginSettings}
 * 报告，而不是静默改掉用户的输入。
 */
export interface PluginSettingsResolution {
  settings: PluginSettings
  issues: RenderCacheLimitIssue[]
}

export function resolvePluginSettings(input: unknown): PluginSettingsResolution {
  const root = readRecord(input)
  const features = readRecord(root.features)
  const cache = readRecord(root.cache)
  const render = readRecord(cache.render)
  const vision = readRecord(root.vision)
  const dnn = readRecord(vision.dnn)
  const issues: RenderCacheLimitIssue[] = []
  const cacheRender = {} as RenderCacheSettings
  for (const rule of CACHE_RULES) {
    cacheRender[rule.key] = normalizeCacheLimit(render[rule.key], rule, issues)
  }

  return {
    settings: {
      features: {
        dnn: readBoolean(features.dnn, DEFAULT_PLUGIN_SETTINGS.features.dnn),
      },
      cache: { render: cacheRender },
      vision: {
        python: readString(vision.python, DEFAULT_PLUGIN_SETTINGS.vision.python),
        venv: readString(vision.venv, DEFAULT_PLUGIN_SETTINGS.vision.venv),
        model: readString(vision.model, DEFAULT_PLUGIN_SETTINGS.vision.model),
        dnn: {
          backend: readEnum(dnn.backend, DNN_BACKENDS, DEFAULT_PLUGIN_SETTINGS.vision.dnn.backend),
          device: readEnum(dnn.device, DNN_DEVICES, DEFAULT_PLUGIN_SETTINGS.vision.dnn.device),
        },
      },
    },
    issues,
  }
}

export function normalizePluginSettings(input: unknown): PluginSettings {
  return resolvePluginSettings(input).settings
}

export interface SettingsValidation {
  ok: boolean
  errors: string[]
  warnings: string[]
}

/** 跨字段校验：只报告，不修改。errors 非空时 apply 会回落到修复后的安全值。 */
export function validatePluginSettings(settings: PluginSettings): SettingsValidation {
  const errors: string[] = []
  const warnings: string[] = []
  const { dnn } = settings.vision
  const { venv } = settings.vision

  if (dnn.backend === 'opencv' && dnn.device !== 'cpu') {
    errors.push('vision.dnn.backend=opencv 只支持 device=cpu；GPU 推理需要 onnxruntime 且自备 venv')
  }
  if (venv !== '' && !isAbsolutePath(venv)) {
    errors.push('vision.venv 必须是绝对路径（空字符串表示使用托管 venv）')
  }
  if (!LIMITS.modelId.test(settings.vision.model)) {
    errors.push('vision.model 只能是 1-64 位字母/数字/点/下划线/连字符，且以字母或数字开头')
  }
  if (dnn.backend === 'onnxruntime') {
    warnings.push(
      'vision.dnn.backend=onnxruntime 需要自备 vision.venv 并安装 onnxruntime（插件不自动安装）',
    )
    if (venv === '') {
      warnings.push(
        '未设置 vision.venv：onnxruntime 后端会回落到托管 venv，其中通常没有 onnxruntime',
      )
    }
  }

  return { ok: errors.length === 0, errors, warnings }
}

export interface SettingsRepair {
  settings: PluginSettings
  repairs: string[]
}

/** 把 validate 不通过的值修成安全值：只动非法字段，其余保留。 */
export function repairPluginSettings(settings: PluginSettings): SettingsRepair {
  const repairs: string[] = []
  const next = normalizePluginSettings(settings)

  if (next.vision.dnn.backend === 'opencv' && next.vision.dnn.device !== 'cpu') {
    repairs.push(
      `vision.dnn.device=${next.vision.dnn.device} → cpu（backend=opencv 不支持非 CPU 设备）`,
    )
    next.vision.dnn.device = 'cpu'
  }
  if (next.vision.venv !== '' && !isAbsolutePath(next.vision.venv)) {
    repairs.push(`vision.venv=${next.vision.venv} → 空（必须是绝对路径）`)
    next.vision.venv = ''
  }
  if (!LIMITS.modelId.test(next.vision.model)) {
    repairs.push(`vision.model=${next.vision.model} → ${DEFAULT_PLUGIN_SETTINGS.vision.model}`)
    next.vision.model = DEFAULT_PLUGIN_SETTINGS.vision.model
  }

  return { settings: next, repairs }
}

/**
 * Only keys whose value differs from the documented default are emitted. The
 * Gateway merges this layer over the home config, so serializing untouched
 * defaults would shadow a user's home file and report "source: plugin" for a
 * value the user never set.
 */
function diffSettings(settings: PluginSettings): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  if (settings.features.dnn !== DEFAULT_PLUGIN_SETTINGS.features.dnn) {
    out.features = { dnn: settings.features.dnn }
  }
  const render: Record<string, unknown> = {}
  for (const rule of CACHE_RULES) {
    if (settings.cache.render[rule.key] !== rule.fallback) {
      render[rule.key] = settings.cache.render[rule.key]
    }
  }
  if (Object.keys(render).length > 0) out.cache = { render }
  const vision: Record<string, unknown> = {}
  for (const key of ['python', 'venv', 'model'] as const) {
    if (settings.vision[key] !== DEFAULT_PLUGIN_SETTINGS.vision[key]) {
      vision[key] = settings.vision[key]
    }
  }
  const dnn: Record<string, unknown> = {}
  for (const key of ['backend', 'device'] as const) {
    if (settings.vision.dnn[key] !== DEFAULT_PLUGIN_SETTINGS.vision.dnn[key]) {
      dnn[key] = settings.vision.dnn[key]
    }
  }
  if (Object.keys(dnn).length > 0) vision.dnn = dnn
  if (Object.keys(vision).length > 0) out.vision = vision
  return out
}

/** 稳定键序的 JSON（网关按字段读取）；只写非默认值，默认值靠缺失表达。 */
export function serializePluginSettings(settings: PluginSettings): string {
  return JSON.stringify(diffSettings(normalizePluginSettings(settings)))
}

export interface PluginSettingsEnvPayload {
  json: string
  issues: RenderCacheLimitIssue[]
}

/**
 * Build the DSH_BLENDER_PLUGIN_CONFIG payload. Invalid cache.render.* values
 * are passed through RAW on purpose: the Gateway's normalizeRenderCacheLimits()
 * falls back to the documented default and reports the issue in doctor, which is
 * exactly how the same invalid value behaves in a config file. All other values
 * are normalized (semantic repair happens in the caller).
 */
export function pluginSettingsEnvPayload(
  input: unknown,
  settings?: PluginSettings,
): PluginSettingsEnvPayload {
  const resolved = resolvePluginSettings(input)
  const target = normalizePluginSettings(settings ?? resolved.settings)
  const diff = diffSettings(target)
  if (resolved.issues.length > 0) {
    const rawRender = readRecord(readRecord(readRecord(input).cache).render)
    const cache = isRecord(diff.cache) ? diff.cache : {}
    const render = isRecord(cache.render) ? cache.render : {}
    for (const issue of resolved.issues) {
      const key = issue.field.slice('cache.render.'.length)
      render[key] = rawRender[key]
    }
    cache.render = render
    diff.cache = cache
  }
  return { json: JSON.stringify(diff), issues: resolved.issues }
}

/** Write the payload into the current environment; returns the reported issues. */
export function applyPluginSettingsEnv(
  input: unknown,
  settings?: PluginSettings,
  env: NodeJS.ProcessEnv = process.env,
): RenderCacheLimitIssue[] {
  const payload = pluginSettingsEnvPayload(input, settings)
  env[PLUGIN_SETTINGS_ENV] = payload.json
  return payload.issues
}

/** 把设置写进当前进程环境；网关子进程继承。 */
export function applySettingsEnv(
  settings: PluginSettings,
  env: NodeJS.ProcessEnv = process.env,
): void {
  env[PLUGIN_SETTINGS_ENV] = serializePluginSettings(settings)
}

/** 从环境变量读回（测试与网关侧诊断用）；缺失/坏 JSON 返回 null。 */
export function readPluginSettingsFromEnv(
  raw: string | undefined = process.env[PLUGIN_SETTINGS_ENV],
): PluginSettings | null {
  if (raw === undefined || raw.trim() === '') return null
  try {
    const parsed: unknown = JSON.parse(raw)
    if (!isRecord(parsed)) return null
    return normalizePluginSettings(parsed)
  } catch {
    return null
  }
}
