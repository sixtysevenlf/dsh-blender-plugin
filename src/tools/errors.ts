/**
 * A8: one error shape for the model-facing tool channel.
 *
 * A tool failure is not just a sentence: the model needs a stable code (so a
 * caller can branch), a clean message (no raw stack from Blender/host), a
 * next_action (so it can recover without another probing turn), and it must
 * survive the DSH lossless-JSON gate (vTool sanitizes every return value).
 */
export interface ToolFailure {
  text: string
  envelope: {
    ok: false
    error: {
      code: string
      message: string
      next_action: string
      detail?: string
    }
  }
}

const SECRET_PATTERNS: Array<[RegExp, string]> = [
  [/\bsk-[A-Za-z0-9_-]{10,}\b/g, 'sk-***'],
  [/\b(Bearer\s+)[A-Za-z0-9._~+/-]{8,}/gi, '$1***'],
  [/((?:api[_-]?key|access[_-]?token|auth[_-]?token|token|secret|password)\s*[:=]\s*)[^\s,;]+/gi, '$1***'],
  [/((?:COMFY_API_KEY|HF_TOKEN|DEEPSEEK_API_KEY|ANTHROPIC_API_KEY)\s*[:=]\s*)[^\s,;]+/g, '$1***']
]

/** Strip stack frames, API keys and bearer/token values from a diagnostic. */
export function redactSecrets(input: unknown): string {
  let text = String(input === undefined || input === null ? '' : input)
  text = text
    .split(/\r?\n/)
    .filter((line) => !/^\s*(?:Traceback \(most recent call last\)|File "|at .+\(.+:\d+:\d+\))/.test(line))
    .join('\n')
  for (const [pattern, replacement] of SECRET_PATTERNS) text = text.replace(pattern, replacement)
  return text.slice(0, 600)
}

/** Host-side cancellation (exec.signal abort) must keep propagating, not become a tool result. */
export function isHostAbort(error: unknown): boolean {
  const text = String((error && (error as any).message) || error || '')
  return /宿主取消了本次等待|exec\.signal.*abort|operation was aborted/i.test(text)
}

/** First readable line of an exception, with secrets/stack removed. */
export function errorLine(error: unknown): string {
  const raw = error && typeof error === 'object' && 'message' in error ? (error as any).message : error
  const first = String(raw === undefined || raw === null ? 'unknown' : raw).split(/\r?\n/)[0] || 'unknown'
  return redactSecrets(first).slice(0, 240)
}

/**
 * Build a lossless tool failure. `text` keeps the human/model channel readable;
 * `envelope.error` is the machine-readable channel used by the A8 contract.
 */
export function toolError(code: string, message: string, nextAction: string, detail?: unknown): ToolFailure {
  const msg = redactSecrets(message).slice(0, 500)
  const next = String(nextAction || '').trim() || 'blender_viewport op=status / op=doctor 查看后端与环境'
  const out: ToolFailure = {
    text: msg + '\ncode: ' + code + '\nnext_action: ' + next,
    envelope: { ok: false, error: { code, message: msg, next_action: next } }
  }
  if (detail !== undefined && detail !== null && String(detail).trim()) out.envelope.error.detail = redactSecrets(detail).slice(0, 400)
  return out
}
