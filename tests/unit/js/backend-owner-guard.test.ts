import http from 'node:http'
import type { AddressInfo } from 'node:net'
import { afterEach, describe, expect, it } from 'vitest'
import {
  checkGatewayOwnership,
  ensureBackend,
  ensureBackendDetailed,
  getLastBackendFailure,
} from '../../../src/host/backend/index.js'
import { hostIdentity } from '../../../src/host/backend/host-id.js'
import { getChild } from '../../../src/host/backend/spawn.js'

const servers: http.Server[] = []

afterEach(async () => {
  for (const server of servers.splice(0)) {
    await new Promise<void>((resolve) => server.close(() => resolve()))
  }
})

function serve(body: unknown): Promise<number> {
  return new Promise((resolve, reject) => {
    const server = http.createServer((_req, res) => {
      res.writeHead(200, { 'content-type': 'application/json' })
      res.end(JSON.stringify(body))
    })
    servers.push(server)
    server.once('error', reject)
    server.listen(0, '127.0.0.1', () => {
      resolve((server.address() as AddressInfo).port)
    })
  })
}

const owner = (hostId: string): Record<string, unknown> => ({
  gatewayId: 'gw-other',
  hostId,
  pid: 999,
  endpoint: 'ws://127.0.0.1:39877/addon',
  startedAt: 1,
  port: 39877,
})

describe('host attach checks the Gateway owner hostId', () => {
  it('fails closed with a structured GATEWAY_IN_USE for another host', async () => {
    const port = await serve({ ok: true, gateway: owner('another-dsh-host') })
    const result = await ensureBackendDetailed(port)
    expect(result.ok).toBe(false)
    expect(result.failure).toMatchObject({
      stage: 'config',
      code: 'GATEWAY_IN_USE',
      evidence: { port, owner: { hostId: 'another-dsh-host' } },
    })
    expect(result.failure?.root_cause).toContain('another-dsh-host')
    expect(result.failure?.fallback).toBeTruthy()
    expect(getLastBackendFailure()?.code).toBe('GATEWAY_IN_USE')
    expect(await ensureBackend(port)).toBe(false)
    expect(getChild()).toBeNull()
  })

  it('attaches when the Gateway owner is this host', async () => {
    const port = await serve({ ok: true, gateway: owner(hostIdentity()) })
    const result = await ensureBackendDetailed(port)
    expect(result).toEqual({ ok: true, adopted: true })
    expect(getLastBackendFailure()).toBeNull()
    expect(await ensureBackend(port)).toBe(true)
    expect(getChild()).toBeNull()
  })

  it('keeps compatibility with a /health response that has no owner', async () => {
    const port = await serve({ ok: true })
    const result = await ensureBackendDetailed(port)
    expect(result.ok).toBe(true)
    expect(getLastBackendFailure()).toBeNull()
    expect(await ensureBackend(port)).toBe(true)
  })

  it('exposes the pure ownership check', () => {
    expect(checkGatewayOwnership({ ok: true }, 'host-a')).toBeNull()
    expect(checkGatewayOwnership({ ok: true, gateway: owner('host-a') }, 'host-a')).toBeNull()
    const failure = checkGatewayOwnership({ ok: true, gateway: owner('host-b') }, 'host-a')
    expect(failure).toMatchObject({ code: 'GATEWAY_IN_USE', stage: 'config' })
  })

  it('keeps a stable identity across plugin reloads when DSH_HOST_ID is absent', () => {
    const first = hostIdentity({} as NodeJS.ProcessEnv)
    const second = hostIdentity({} as NodeJS.ProcessEnv)
    expect(first).toBe(second)
    expect(first).toMatch(/^host-[0-9a-f]{12}$/)
    expect(first).not.toContain(String(process.pid))
    expect(hostIdentity({ DSH_HOST_ID: 'injected-host' } as NodeJS.ProcessEnv)).toBe(
      'injected-host',
    )
  })

  it('reuses a Gateway started by a previous plugin load', async () => {
    const port = await serve({ ok: true, gateway: owner(hostIdentity()) })
    expect(await ensureBackendDetailed(port)).toEqual({ ok: true, adopted: true })
    // A reloaded plugin instance re-runs the same check against the same owner.
    expect(await ensureBackendDetailed(port)).toEqual({ ok: true, adopted: true })
    expect(getLastBackendFailure()).toBeNull()
    expect(getChild()).toBeNull()
  })
})
