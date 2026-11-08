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
import { SessionRegistry } from '../core/sessions/registry.js'
import { JobQueue } from '../core/queue/manager.js'
import { SpawnJobExecutor } from '../core/queue/spawn-executor.js'
import { setSessionRegistry } from '../http/session-gate.js'
import { installPresencePublisher } from './presence-push.js'
import { gatewayInstances } from '../transport/addon-handshake.js'
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
  sessionRegistry: SessionRegistry
  jobQueue: JobQueue
  addonGateway: AddonGateway
  addonClient: GatewayAddonClient
  port: number
  host: string
  routes: string[]
  owner: GatewayOwner | null
  close: () => void
}

/** Forward addon events to the session registry and any external listener. */
function addonEventSink(
  opts: GatewayAppOptions,
  registryRef: { current: SessionRegistry | null },
): (event: Record<string, unknown>) => void {
  return (event) => {
    if (event.type === 'event' && event.name === 'pause' && registryRef.current) {
      registryRef.current.applyHumanPause(
        event.instanceId === null ? undefined : String(event.instanceId),
        event.paused === true,
      )
    }
    if (event.type === 'connection_closed' && registryRef.current) {
      registryRef.current.handleInstanceGone(String(event.instanceId || ''))
    }
    if (event.type === 'event' && event.name === 'human_edit' && registryRef.current) {
      registryRef.current.applyHumanEdits(
        event.instanceId === null ? undefined : String(event.instanceId),
        [
          ...(Array.isArray(event.objects) ? event.objects : []),
          ...(Array.isArray(event.collections) ? event.collections : []),
        ] as Record<string, unknown>[],
      )
    }
    opts.onEvent?.(event)
  }
}

/** Build the Gateway app without listening; tests and the process entry share it. */
export function createGatewayApp(opts: GatewayAppOptions = {}): GatewayApp {
  const port = opts.port ?? GATEWAY_DEFAULT_PORT
  const host = opts.host ?? HOST
  const registryRef: { current: SessionRegistry | null } = { current: null }
  const addonGateway = new AddonGateway({ ...opts, onEvent: addonEventSink(opts, registryRef) })
  const addonClient = new GatewayAddonClient(addonGateway)
  installPresencePublisher(addonClient)
  const engine = createEngine({ addonClient: addonClient as unknown as AddonClient })
  const selectionStore = createSelectionStore()
  const sessionRegistry = new SessionRegistry(() =>
    (gatewayInstances(addonClient) || []).map((x) => ({
      instanceId: x.instanceId,
      kind: x.kind,
      ready: x.ready,
      project: x.project,
    })),
  )
  registryRef.current = sessionRegistry
  setSessionRegistry(sessionRegistry)
  const jobQueue = new JobQueue({ executor: new SpawnJobExecutor() })
  const E: HttpRouteDeps = {
    sessionRegistry,
    jobQueue,
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
    sessionRegistry,
    jobQueue,
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
