// Deliver presence snapshots to the add-on through the pause-safe internal
// get_viewport_screenshot command. Presence used to ride execute_code, which the
// add-on rejects during a human pause: presence must stay available then.
import { setPresencePublisher } from '../core/sessions/presence-publish.js'
import type { PresencePush } from '../core/sessions/presence-publish.js'
import type { GatewayAddonClient } from '../transport/addon-ws.js'

function presencePayload(push: PresencePush): string {
  return JSON.stringify({
    instanceId: push.instanceId,
    paused: push.paused,
    agents: push.agents,
    ...(push.notice ? { notice: push.notice } : {}),
  })
}

export function installPresencePublisher(client: GatewayAddonClient): void {
  setPresencePublisher((push) => {
    void client
      .send('get_viewport_screenshot', { presence: presencePayload(push) }, 3000, {
        instanceId: push.instanceId,
      })
      .catch(() => {
        /* presence is best-effort; a disconnected add-on is not an error */
      })
  })
}
