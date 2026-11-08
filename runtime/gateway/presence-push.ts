// Deliver presence snapshots to the add-on through the existing internal
// execute_code transport. The add-on package root differs between a source
// install and a Blender extension, so the snippet resolves it from sys.modules.
import { setPresencePublisher } from '../core/sessions/presence-publish.js'
import type { PresencePush } from '../core/sessions/presence-publish.js'
import type { GatewayAddonClient } from '../transport/addon-ws.js'

export function presenceCode(push: PresencePush): string {
  const payload = JSON.stringify({
    instanceId: push.instanceId,
    paused: push.paused,
    agents: push.agents,
  })
  return [
    'import sys, importlib, json',
    'pkg = next((n for n in sys.modules if n == "dsh_blender_bridge" or n.endswith(".dsh_blender_bridge")), None)',
    'if pkg:',
    '    importlib.import_module(pkg + ".presence_draw").set_presence(json.loads(' +
      JSON.stringify(payload) +
      '))',
  ].join('\n')
}

export function installPresencePublisher(client: GatewayAddonClient): void {
  setPresencePublisher((push) => {
    void client
      .send('execute_code', { code: presenceCode(push) }, 3000, { instanceId: push.instanceId })
      .catch(() => {
        /* presence is best-effort; a disconnected add-on is not an error */
      })
  })
}
