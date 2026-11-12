// 3.1 Gateway process entry: the only HTTP/WebSocket server in the topology.
// The addon dials this process; host tools keep talking to the 29 HTTP routes.
import { parsePortArg } from './ports.js'
import { CFG } from './config.js'
import { portConflict } from './gateway/lifecycle.js'

const argv = process.argv.slice(2)
const port = parsePortArg(argv) ?? Number(process.env.DSH_BLENDER_GATEWAY_PORT || CFG.gatewayPort)
// Fail closed before loading the app: building the engine/catalog on a bound port
// burned seconds and pushed the exit past the port-conflict budget. The app
// modules stay dynamic so this check runs on a bare process.
const conflict = portConflict(port)
if (conflict) {
  console.error(JSON.stringify(conflict))
  process.exit(1)
}
const { createGatewayApp } = await import('./gateway/app.js')
const { startGateway } = await import('./gateway/lifecycle.js')
const app = createGatewayApp({ port })
const result = await startGateway(app)
if (!result.ok) {
  console.error(JSON.stringify(result.error))
  process.exit(1)
}
// 首次安装（下载解释器/venv/pip）只在 Gateway 起来后后台跑：不阻塞监听，也不影响其他工具。
void (app.engine.vision?.ensureInstalled?.() ?? Promise.resolve()).catch((e: unknown) => {
  console.error(
    '[dsh-blender-gateway] vision install failed: ' +
      String(e && (e as Error).message ? (e as Error).message : e),
  )
})
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
