// AddonClient-compatible facade used by the engine over the Gateway WebSocket.
import type { AddonHandshakeError, AddonInstanceView } from './addon-handshake.js'
import type { AddonGateway } from './addon-ws.js'

export class GatewayAddonClient {
  private readonly gateway: AddonGateway
  readonly protocolInfo = {
    proto: { id: 'ws', label: 'Gateway WebSocket (protocol 12)', panelHint: '' },
    from: 'gateway',
  }
  constructor(gateway: AddonGateway) {
    this.gateway = gateway
  }
  async ensure(): Promise<void> {
    await this.gateway.ensure()
  }
  async protocol(): Promise<typeof this.protocolInfo> {
    return this.protocolInfo
  }
  send(
    type: string,
    params: Record<string, unknown> = {},
    timeoutMs = 120000,
    opts: { instanceId?: string; noMark?: boolean } = {},
  ): Promise<unknown> {
    return this.gateway.send(type, params, timeoutMs, { instanceId: opts.instanceId })
  }
  close(): void {
    this.gateway.close()
  }
  get instances(): AddonInstanceView[] {
    return this.gateway.list()
  }
  get lastHandshakeError(): AddonHandshakeError | null {
    return this.gateway.lastHandshakeError
  }
}
