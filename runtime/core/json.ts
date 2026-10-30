/**
 * 边界解析（Phase R）——JSON / 环境变量 / 子进程输出在类型上都是 unknown。
 *
 * 规矩：只在边界把 unknown 收窄成具体类型，业务代码不得用 `as SomeType` 把边界数据吞掉；
 * 也不得用显式 any。需要新字段时先在这里加守卫/读取函数，再在模块里声明接口。
 */

/** JSON 对象（排除 null 与数组）。 */
export type JsonRecord = Record<string, unknown>

export function isRecord(v: unknown): v is JsonRecord {
  return typeof v === 'object' && v !== null && !Array.isArray(v)
}

export function isArray(v: unknown): v is unknown[] {
  return Array.isArray(v)
}

export function isString(v: unknown): v is string {
  return typeof v === 'string'
}

export function isNumber(v: unknown): v is number {
  return typeof v === 'number' && Number.isFinite(v)
}

export function isBoolean(v: unknown): v is boolean {
  return typeof v === 'boolean'
}

/** 任意值 → string；null/undefined 返回 undefined（不编造空串）。 */
export function readString(v: unknown): string | undefined {
  if (v === undefined || v === null) return undefined
  return typeof v === 'string' ? v : String(v)
}

/** 任意值 → 有限数字；无法转换或非正数时返回 fallback（配置项语义）。 */
export function readPositiveNumber(v: unknown, fallback: number): number {
  const n = typeof v === 'number' ? v : Number(v)
  return Number.isFinite(n) && n > 0 ? n : fallback
}

/** 任意值 → 有限数字；无法转换返回 fallback。 */
export function readNumber(v: unknown, fallback: number): number {
  const n = typeof v === 'number' ? v : Number(v)
  return Number.isFinite(n) ? n : fallback
}

/** 错误对象/字符串 → 可读消息（等价于旧代码的 String((e && e.message) || e)）。 */
export function errorMessage(e: unknown): string {
  if (isRecord(e)) {
    const msg = e.message
    if (msg !== undefined && msg !== null && msg !== '') return String(msg)
  }
  return String(e)
}

/** 错误对象/字符串 → 错误码（Node 系统错误带 code）。 */
export function errorCode(e: unknown): string | undefined {
  return isRecord(e) && typeof e.code === 'string' ? e.code : undefined
}

/** 错误对象上的自定义扩展字段（老代码的 e.diagnosis / e.hint / e.code 等动态边界）。 */
export function errorField(e: unknown, key: string): unknown {
  return isRecord(e) ? e[key] : undefined
}

/** 错误对象/字符串 → 详情（有 stack 用 stack，否则消息；/router 的 500 回执语义）。 */
export function errorDetail(e: unknown): string {
  if (isRecord(e) && typeof e.stack === 'string' && e.stack) return e.stack
  return errorMessage(e)
}

/** 任意值 → 普通记录（非对象时给空对象，用于可选配置片段）。 */
export function readRecord(v: unknown): JsonRecord {
  return isRecord(v) ? v : {}
}
