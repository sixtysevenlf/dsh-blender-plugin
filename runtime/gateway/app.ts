// Gateway HTTP app assembly: one loopback server, the existing 29 routes, and
// the /addon WebSocket upgrade. No addon listener, no second port.
import http from 'node:http'
import type { Server } from 'node:http'
import { createEngine, engineProvenance } from '../engine.js'
import { createSelectionStore } from '../selectionstore.js'
import { describeConfig } from '../config.js'
import { HOST, stats, pushRtt, rttView, reqCounts, bump } from '../http/config.js'
import { json, streamJson, readBody, hostJson, hostErrText } from '../http/response.js'
import {
  LEASE,
  leaseView,
  releaseStaleLease,
  leaseAcquire,
  leaseGate,
  isReadOnly,
} from '../http/lease.js'
import { trimJob, clampIntParam } from '../http/jobs.js'
import { createRouter } from '../http/router.js'
import { createRoutes, CONTRACT_ROUTES } from '../http/routes/index.js'
import { AddonGateway, GatewayAddonClient, GATEWAY_DEFAULT_PORT } from '../transport/addon-ws.js'
import type { EngineSurface } from '../core/engine/index.js'
import type { AddonClient } from '../transport/probes.js'
import type { HttpRouteDeps } from '../http/routes/deps.js'
import type { AddonGatewayOptions } from '../transport/addon-ws.js'
import type { GatewayOwner } from './owner.js'

export interface GatewayAppOptions extends AddonGatewayOptions {
  port?: number
  host?: string
}

export interface GatewayApp {
  server: Server
  engine: EngineSurface
  addonGateway: AddonGateway
  addonClient: GatewayAddonClient
  port: number
  host: string
  routes: string[]
  owner: GatewayOwner | null
  close: () => void
}

/** Build the Gateway app without listening; tests and the process entry share it. */
export function createGatewayApp(opts: GatewayAppOptions = {}): GatewayApp {
  const port = opts.port ?? GATEWAY_DEFAULT_PORT
  const host = opts.host ?? HOST
  const addonGateway = new AddonGateway(opts)
  const addonClient = new GatewayAddonClient(addonGateway)
  const engine = createEngine({ addonClient: addonClient as unknown as AddonClient })
  const selectionStore = createSelectionStore()
  const E: HttpRouteDeps = {
    gatewayOwner: () => app.owner,
    engine,
    selectionStore,
    PORT: port,
    HOST: host,
    stats,
    json,
    streamJson,
    readBody,
    pushRtt,
    rttView,
    reqCounts,
    bump,
    LEASE,
    leaseView,
    releaseStaleLease,
    leaseAcquire,
    leaseGate,
    isReadOnly,
    trimJob,
    clampIntParam,
    describeConfig,
    engineProvenance,
    hostJson,
    hostErrText,
  }
  const routes = createRoutes(E)
  const server = http.createServer(createRouter({ HOST: host, PORT: port, bump, routes }))
  addonGateway.attach(server)
  const app: GatewayApp = {
    server,
    engine,
    addonGateway,
    addonClient,
    port,
    host,
    routes: CONTRACT_ROUTES,
    owner: null,
    close: () => {
      try {
        engine.stop()
      } catch (e) {
        /* ignore */
      }
      addonGateway.close()
      try {
        server.close()
      } catch (e) {
        /* ignore */
      }
    },
  }
  return app
}
