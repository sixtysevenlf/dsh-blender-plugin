/**
 * Vision worker wire protocol — newline-delimited JSON over the child's stdin/stdout.
 *
 * Gateway 与 worker 的唯一通道：请求 `{id, op, args, configRevision?, config?}`，响应
 * `{id, ok, result|error, configRevision?}`。这里集中定义分帧、帧上限、错误形状与
 * 协议错误码，host/worker 两侧都按同一套字段说话（worker 侧见 worker/protocol.py）。
 *
 * `config` 是每个 worker-backed 请求都携带的生效配置切片 `{revision, model}`：
 * 握手帧用它初始化上下文，之后的每个请求都用它覆盖 `model`（design「DNN 热应用」：
 * `vision.model` 只改模型、不重启 worker；worker 必须按请求里的 model 合并上下文，
 * 缓存键因此随 revision/model 变化）。字段缺失等价于「沿用握手时的值」。
 * `configRevision` 只是回执/诊断用的 revision 副本，不携带 model。
 *
 * 约束（worker 设计）：
 *   - 帧以 \n 分隔；单帧上限 8 MiB，超过即协议错误（不是截断、不是丢弃）。
 *   - 每次请求 30 秒超时；超时/崩溃/协议错乱由 manager 负责结束 worker 并按有界次数重启。
 *   - worker 不下载模型、不发起网络请求；协议里没有下载/安装类 op。
 */
import { errorMessage, isRecord } from '../core/json.js'

/** worker 协议版本；握手时两侧必须一致。 */
export const PROTOCOL_VERSION = 1
/** 单帧字节上限（8 MiB）。 */
export const MAX_FRAME_BYTES = 8 * 1024 * 1024
/** 单次请求超时（30 秒）。 */
export const REQUEST_TIMEOUT_MS = 30_000
/** 空闲回收窗口（10 分钟）。 */
export const IDLE_REAP_MS = 10 * 60_000
/** 换 worker 时优雅关闭的等待窗口（2 秒；超时杀进程树）。 */
export const SHUTDOWN_GRACE_MS = 2_000
/** 崩溃/超时后的重启预算：5 分钟内最多 3 次。 */
export const RESTART_WINDOW_MS = 5 * 60_000
export const MAX_RESTARTS_PER_WINDOW = 3

const STAGES = ['config', 'vision', 'input'] as const
/** 失败阶段：安装/设置（config）、worker 协议/崩溃（vision）、调用输入（input）。 */
export type VisionStage = (typeof STAGES)[number]

export interface VisionErrorPayload {
  code: string
  stage: VisionStage
  message: string
  fix?: string
  retryable: boolean
  details?: Record<string, unknown>
}

function isStage(value: unknown): value is VisionStage {
  return typeof value === 'string' && (STAGES as readonly string[]).includes(value)
}

/** 结构化视觉失败：携带 stage/code/fix/retryable，回执不折成笼统 backend_error。 */
export class VisionError extends Error {
  readonly code: string
  readonly stage: VisionStage
  readonly fix?: string
  readonly retryable: boolean
  readonly details?: Record<string, unknown>

  constructor(payload: VisionErrorPayload) {
    if (!isStage(payload?.stage)) {
      throw new Error('VisionError.stage must be one of ' + STAGES.join(' | '))
    }
    if (typeof payload.code !== 'string' || !payload.code) {
      throw new Error('VisionError.code must be a non-empty string')
    }
    super(String(payload.message || payload.code))
    this.name = 'VisionError'
    this.code = payload.code
    this.stage = payload.stage
    this.fix = payload.fix
    this.retryable = payload.retryable === true
    this.details = payload.details
  }

  static from(e: unknown): VisionError {
    if (e instanceof VisionError) return e
    return new VisionError({
      code: 'vision_worker_failed',
      stage: 'vision',
      message: errorMessage(e),
      retryable: true,
    })
  }

  toPayload(): VisionErrorPayload {
    const payload: VisionErrorPayload = {
      code: this.code,
      stage: this.stage,
      message: this.message,
      retryable: this.retryable,
    }
    if (this.fix !== undefined) payload.fix = this.fix
    if (this.details !== undefined) payload.details = this.details
    return payload
  }
}

/** worker 回执 → 结构化错误；字段缺失/非法一律视为协议错误，不猜。 */
export function toVisionErrorPayload(raw: unknown): VisionErrorPayload {
  if (!isRecord(raw)) {
    throw new VisionError({
      code: 'vision_protocol_error',
      stage: 'vision',
      message: 'worker error payload must be a JSON object',
      retryable: true,
    })
  }
  const stage = raw.stage
  const code = raw.code
  if (!isStage(stage)) {
    throw new VisionError({
      code: 'vision_protocol_error',
      stage: 'vision',
      message: 'worker error payload has an invalid stage: ' + String(stage),
      retryable: true,
    })
  }
  if (typeof code !== 'string' || !code) {
    throw new VisionError({
      code: 'vision_protocol_error',
      stage: 'vision',
      message: 'worker error payload has no code',
      retryable: true,
    })
  }
  const payload: VisionErrorPayload = {
    code,
    stage,
    message: typeof raw.message === 'string' && raw.message ? raw.message : code,
    retryable: raw.retryable === true,
  }
  if (typeof raw.fix === 'string' && raw.fix) payload.fix = raw.fix
  if (isRecord(raw.details)) payload.details = raw.details
  return payload
}

/** 序列化一帧（含结尾换行）；超过 8 MiB 直接报协议错误，不截断。 */
export function encodeFrame(value: unknown): Buffer {
  const text = JSON.stringify(value)
  if (typeof text !== 'string') {
    throw new VisionError({
      code: 'vision_protocol_error',
      stage: 'vision',
      message: 'frame is not JSON serializable',
      retryable: false,
    })
  }
  const bytes = Buffer.byteLength(text, 'utf8') + 1
  if (bytes > MAX_FRAME_BYTES) {
    throw new VisionError({
      code: 'vision_protocol_error',
      stage: 'vision',
      message: 'frame exceeds ' + String(MAX_FRAME_BYTES) + ' bytes',
      retryable: true,
      details: { bytes, limit: MAX_FRAME_BYTES },
    })
  }
  return Buffer.from(text + '\n', 'utf8')
}

export interface JsonLinesDecoderOptions {
  maxFrameBytes?: number
}

/**
 * 有界的 JSON-lines 分帧器。
 *
 * 只认「一行 = 一个 JSON 对象」：多个帧可以粘在一个 chunk 里，一个帧也可以跨 chunk；
 * 累计字节超过上限（还没等到换行）或解析失败时抛 `vision_protocol_error`，并清空缓冲
 * ——错误后的字节流不可信，交给 manager 结束 worker，而不是继续猜测。
 */
export class JsonLinesDecoder {
  private readonly maxFrameBytes: number
  private chunks: Buffer[] = []
  private size = 0

  constructor(opts: JsonLinesDecoderOptions = {}) {
    this.maxFrameBytes = opts.maxFrameBytes ?? MAX_FRAME_BYTES
  }

  /** 当前尚未组成完整帧的字节数（测试/诊断用）。 */
  get pendingBytes(): number {
    return this.size
  }

  reset(): void {
    this.chunks = []
    this.size = 0
  }

  push(chunk: Buffer | string): Record<string, unknown>[] {
    const buf = typeof chunk === 'string' ? Buffer.from(chunk, 'utf8') : chunk
    const frames: Record<string, unknown>[] = []
    let start = 0
    for (let i = 0; i < buf.length; i++) {
      if (buf[i] !== 0x0a) continue
      const head = buf.subarray(start, i)
      const line = this.size > 0 ? Buffer.concat([...this.chunks, head]) : head
      // 上限按「去掉结尾换行/回车后的行字节数」判定：完整行也不能绕过分帧上限。
      const lineBytes = line.length - (line.length > 0 && line[line.length - 1] === 0x0d ? 1 : 0)
      const text = line.toString('utf8').replace(/\r$/, '')
      start = i + 1
      this.chunks = []
      this.size = 0
      if (text.trim() && lineBytes > this.maxFrameBytes) {
        throw new VisionError({
          code: 'vision_protocol_error',
          stage: 'vision',
          message: 'frame exceeds ' + String(this.maxFrameBytes) + ' bytes',
          retryable: true,
          details: { bytes: lineBytes, limit: this.maxFrameBytes },
        })
      }
      if (!text.trim()) continue
      frames.push(this.parseLine(text))
    }
    if (start < buf.length) {
      const rest = buf.subarray(start)
      this.size += rest.length
      if (this.size > this.maxFrameBytes) {
        const bytes = this.size
        this.reset()
        throw new VisionError({
          code: 'vision_protocol_error',
          stage: 'vision',
          message: 'frame exceeds ' + String(this.maxFrameBytes) + ' bytes before newline',
          retryable: true,
          details: { bytes, limit: this.maxFrameBytes },
        })
      }
      this.chunks.push(Buffer.from(rest))
    }
    return frames
  }

  private parseLine(text: string): Record<string, unknown> {
    let parsed: unknown
    try {
      parsed = JSON.parse(text)
    } catch (e) {
      this.reset()
      throw new VisionError({
        code: 'vision_protocol_error',
        stage: 'vision',
        message: 'worker frame is not valid JSON: ' + errorMessage(e).slice(0, 160),
        retryable: true,
      })
    }
    if (!isRecord(parsed)) {
      this.reset()
      throw new VisionError({
        code: 'vision_protocol_error',
        stage: 'vision',
        message:
          'worker frame must be a JSON object, got ' +
          (Array.isArray(parsed) ? 'array' : typeof parsed),
        retryable: true,
      })
    }
    return parsed
  }
}

/** vision op 失败回执的公共部分：pass.ts 与 dispatch.ts 共用，避免两份漂移的失败形状。 */
export interface VisionFailureReceipt {
  ok: false
  op: string
  stage: VisionStage
  code: string
  message: string
  error: string
  root_cause: string
  fix: string
  retryable: boolean
  evidence: Record<string, unknown>
}

/** 每个 worker-backed 请求都携带的生效配置切片（design「DNN 热应用」）。 */
export interface VisionRequestConfig {
  revision: number
  model: string
}

export interface VisionRequestFrame {
  id: string
  op: string
  args: Record<string, unknown>
  configRevision?: number
  /** 生效 revision + model；worker 用它覆盖上下文里的 model（不重启 worker）。 */
  config?: VisionRequestConfig
}

export interface VisionResponseFrame {
  id?: string
  ok?: boolean
  result?: unknown
  error?: unknown
  configRevision?: number
}
