// 3.1 Gateway process entry: the only HTTP/WebSocket server in the topology.
// The addon dials this process; host tools keep talking to the 29 HTTP routes.
import { parsePortArg } from './ports.js'
import { CFG } from './config.js'
import { createGatewayApp } from './gateway/app.js'
import { startGateway } from './gateway/lifecycle.js'

const argv = process.argv.slice(2)
const port = parsePortArg(argv) ?? Number(process.env.DSH_BLENDER_GATEWAY_PORT || CFG.gatewayPort)
const app = createGatewayApp({ port })
const result = await startGateway(app)
if (!result.ok) {
  console.error(JSON.stringify(result.error))
  process.exit(1)
}
console.log(
  '[dsh-blender-gateway] http://' +
    app.host +
    ':' +
    String(app.port) +
    '/  (routes: ' +
    app.routes.join(' ') +
    ')',
)
console.log(
  '[dsh-blender-gateway] addon control endpoint: ws://' +
    app.host +
    ':' +
    String(app.port) +
    '/addon',
)
