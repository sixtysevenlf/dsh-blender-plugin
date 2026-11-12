// Gateway owner state: one file per port. The file is diagnostic and gives a
// second DSH host enough identity to fail closed with GATEWAY_IN_USE.
import fs from 'node:fs'
import path from 'node:path'
import { CFG } from '../config.js'
import { homePath } from '../home.js'

export interface GatewayOwner {
  gatewayId: string
  hostId: string
  pid: number
  endpoint: string
  startedAt: number
  port?: number
}

export function gatewayStatePath(port: number): string {
  // v3.2: diagnostic state lives under the plug-in home (state/), never in the
  // package directory or the OS temp dir.
  return homePath('state', 'gateway-' + String(port) + '.json')
}

export function writeGatewayOwner(owner: GatewayOwner): void {
  const p = gatewayStatePath(owner.port ?? 0)
  const tmp = p + '.tmp'
  try {
    fs.mkdirSync(path.dirname(p), { recursive: true })
    fs.writeFileSync(tmp, JSON.stringify(owner), 'utf8')
    fs.renameSync(tmp, p)
  } catch (e) {
    /* state file is diagnostic; not fatal */
  }
}

export function removeGatewayOwner(port: number): void {
  try {
    fs.unlinkSync(gatewayStatePath(port))
  } catch (e) {
    /* ignore */
  }
}

/** A recorded pid that no longer exists means the state file is stale. */
function ownerAlive(pid: unknown): boolean {
  const value = Number(pid)
  if (!Number.isInteger(value) || value <= 0) return false
  try {
    process.kill(value, 0)
    return true
  } catch (e) {
    // EPERM means the process exists but belongs to someone else.
    return !!e && (e as NodeJS.ErrnoException).code === 'EPERM'
  }
}

/**
 * Exported for tests / host status: read the owner state file of a port.
 * A file left behind by a crashed Gateway is ignored, otherwise a later Gateway
 * on that port would fail closed with GATEWAY_IN_USE instead of port_conflict.
 */
export function readGatewayOwner(port: number): GatewayOwner | null {
  try {
    const parsed: unknown = JSON.parse(fs.readFileSync(gatewayStatePath(port), 'utf8'))
    if (!parsed || typeof parsed !== 'object') return null
    const owner = parsed as GatewayOwner
    return ownerAlive(owner.pid) ? owner : null
  } catch (e) {
    return null
  }
}

export function gatewayHostId(): string {
  return String(process.env.DSH_HOST_ID || CFG.holder || 'host-pid-' + String(process.pid))
}
