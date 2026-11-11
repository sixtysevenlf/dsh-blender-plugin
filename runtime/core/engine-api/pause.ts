// Shared detection for the add-on's human-pause rejection. Both the
// GatewayChannelError message ("AI 操作已暂停：...") and older transports that
// surface the error code are matched so callers can fall back to read-only
// pause-safe commands instead of treating a pause as a failure.
export function isPauseError(error: unknown): boolean {
  const message = error instanceof Error ? error.message : String(error ?? '')
  return /AI_PAUSED|AI 操作已暂停/.test(message)
}
