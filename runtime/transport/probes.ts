// Gateway transport seam: the contract the engine and HTTP routes rely on.
//
// 3.1 has exactly one production transport: the add-on dials the Gateway over
// WebSocket and the Gateway routes requests by instanceId. The flat TCP client
// that 3.0.x used is a protocol fixture only; it lives in
// tests/helpers/legacy-flat-addon-client.ts and is not part of runtime/.

export interface AddonClient {
  ensure(): Promise<void>
  send(
    type: string,
    params?: Record<string, unknown>,
    timeoutMs?: number,
    opts?: { instanceId?: string; noMark?: boolean },
  ): Promise<unknown>
  close(): void
}
