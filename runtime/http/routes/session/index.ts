// /session 路由入口（拆分）
import type { HttpRouteDeps } from '../deps.js'
import { createLaunchHandler } from './create.js'
import { createWhoHandler } from './list.js'
import { createLeaseHandler, createReleaseHandler } from './close.js'

export function createSessionRoutes(E: HttpRouteDeps) {
  return {
    launch: createLaunchHandler(E),
    who: createWhoHandler(E),
    lease: createLeaseHandler(E),
    release: createReleaseHandler(E),
  }
}
