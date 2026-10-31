import { isRecord } from '../contracts/dynamic.js'

export type Json = Record<string, unknown>

const HTTP_TIMEOUT_MS = 1500

export function base(port: number): string {
  return 'http://127.0.0.1:' + String(port)
}

export function withTimeout(ms: number, outer?: AbortSignal): AbortSignal {
  const own = AbortSignal.timeout(ms)
  return outer && typeof AbortSignal.any === 'function' ? AbortSignal.any([own, outer]) : own
}

export async function getJson(
  port: number,
  path: string,
  signal?: AbortSignal,
  timeoutMs = HTTP_TIMEOUT_MS,
): Promise<Json | null> {
  try {
    const r = await fetch(base(port) + path, {
      cache: 'no-store',
      signal: withTimeout(timeoutMs, signal),
    })
    const body: unknown = await r.json()
    return isRecord(body) ? body : null
  } catch {
    return null
  }
}

export async function postJson(port: number, path: string, body: unknown): Promise<Json | null> {
  try {
    const r = await fetch(base(port) + path, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(body),
      signal: withTimeout(HTTP_TIMEOUT_MS),
    })
    const parsed: unknown = await r.json()
    return isRecord(parsed) ? parsed : null
  } catch {
    return null
  }
}
