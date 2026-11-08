// Gateway owner state: one file per port. The file is diagnostic and gives a
// second DSH host enough identity to fail closed with GATEWAY_IN_USE.
import fs from 'node:fs'
import path from 'node:path'
import { CFG } from '../config.js'

export interface GatewayOwner {
  gatewayId: string
  hostId: string
  pid: number
  endpoint: string
  startedAt: number
  port?: number
}

export function gatewayStatePath(port: number): string {
  return path.join(CFG.workDirWsl, 'gateway-' + String(port) + '.json')
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

/** Exported for tests / host status: read the owner state file of a port. */
export function readGatewayOwner(port: number): GatewayOwner | null {
  try {
    const parsed: unknown = JSON.parse(fs.readFileSync(gatewayStatePath(port), 'utf8'))
    return parsed && typeof parsed === 'object' ? (parsed as GatewayOwner) : null
  } catch (e) {
    return null
  }
}

export function gatewayHostId(): string {
  return String(process.env.DSH_HOST_ID || CFG.holder || 'host-pid-' + String(process.pid))
}
