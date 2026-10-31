/**
 * Narrow helpers for lossless-JSON boundaries.
 *
 * Host/backend payloads are dynamic: they come from JSON.parse, an HTTP body or
 * another process and cannot be trusted to match a hand-written interface.
 * These helpers keep that boundary explicit (unknown in, checked value out)
 * instead of spreading any through the call sites.
 */
export type UnknownRecord = Record<string, unknown>

export function isRecord(value: unknown): value is UnknownRecord {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

/** A record view of a dynamic value; non-objects become an empty record. */
export function asRecord(value: unknown): UnknownRecord {
  return isRecord(value) ? value : {}
}

/** Read an object property without asserting the value's shape. */
export function get(value: unknown, key: string): unknown {
  return asRecord(value)[key]
}

/** A nested record; missing/non-object values become an empty record. */
export function getRecord(value: unknown, key: string): UnknownRecord {
  return asRecord(asRecord(value)[key])
}

/** An array view of a dynamic value; non-arrays become an empty array. */
export function asArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : []
}

/** String coercion that keeps the historical String(v ?? '') call-site behaviour. */
export function text(value: unknown): string {
  return value === undefined || value === null ? '' : String(value)
}

/** String coercion with a null result for missing values (for lossless envelopes). */
export function optionalText(value: unknown): string | null {
  return value === undefined || value === null ? null : String(value)
}

/** A finite number or undefined. */
export function numberOrUndefined(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isFinite(value) ? value : undefined
}

/** The message of an Error-like value, without assuming the thrown value is an Error. */
export function errorMessage(value: unknown): string {
  if (isRecord(value) && typeof value.message === 'string') return value.message
  return text(value)
}
