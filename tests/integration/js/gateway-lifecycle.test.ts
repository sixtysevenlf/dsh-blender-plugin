import { spawn } from 'node:child_process'
import fs from 'node:fs'
import { afterEach, describe, expect, it } from 'vitest'
import { createGatewayApp } from '../../../runtime/gateway/app.js'
import type { GatewayApp } from '../../../runtime/gateway/app.js'
import { startGateway, stopGateway } from '../../../runtime/gateway/lifecycle.js'
import {
  gatewayStatePath,
  readGatewayOwner,
  writeGatewayOwner,
} from '../../../runtime/gateway/owner.js'

const apps: GatewayApp[] = []

function make(port = 0): GatewayApp {
  const app = createGatewayApp({ port })
  apps.push(app)
  return app
}

afterEach(async () => {
  for (const app of apps.splice(0)) {
    try {
      stopGateway(app)
    } catch (e) {
      /* ignore */
    }
  }
  await new Promise((resolve) => setTimeout(resolve, 20))
})

describe('Gateway single-host lifecycle', () => {
  it('exposes the single owner through /health', async () => {
    const app = make()
    const started = await startGateway(app, { handleSignals: false })
    expect(started.ok).toBe(true)
    const health = (await (
      await fetch('http://127.0.0.1:' + String(started.port) + '/health')
    ).json()) as any
    expect(health.ok).toBe(true)
    expect(health.gateway).toMatchObject({
      port: started.port,
      pid: process.pid,
      gatewayId: started.owner?.gatewayId,
      hostId: started.owner?.hostId,
    })
    expect(health.gateway.endpoint).toBe('ws://127.0.0.1:' + String(started.port) + '/addon')
  })

  it('writes the owner file on start and removes it on stop', async () => {
    const app = make()
    const started = await startGateway(app, { handleSignals: false })
    expect(started.ok).toBe(true)
    const port = started.port
    expect(port).toBeGreaterThan(0)
    const owner = readGatewayOwner(port)
    expect(owner).toMatchObject({
      pid: process.pid,
      port,
      endpoint: 'ws://127.0.0.1:' + port + '/addon',
    })
    expect(owner?.hostId).toBeTruthy()
    expect(owner?.gatewayId).toBeTruthy()

    stopGateway(app)
    expect(readGatewayOwner(port)).toBeNull()
  })

  it('lets a reloaded host stop the old Gateway and take the port again', async () => {
    const first = make()
    const started = await startGateway(first, { handleSignals: false })
    expect(started.ok).toBe(true)
    const port = started.port
    const oldId = started.owner?.gatewayId
    stopGateway(first)
    expect(readGatewayOwner(port)).toBeNull()

    const second = make(port)
    const restarted = await startGateway(second, { handleSignals: false })
    expect(restarted.ok).toBe(true)
    expect(restarted.port).toBe(port)
    expect(restarted.owner?.gatewayId).not.toBe(oldId)
    const health = (await (
      await fetch('http://127.0.0.1:' + String(port) + '/health')
    ).json()) as any
    expect(health.gateway.gatewayId).toBe(restarted.owner?.gatewayId)
  })

  it('fails closed with GATEWAY_IN_USE for another host within 3s', async () => {
    const first = make()
    const started = await startGateway(first, { handleSignals: false })
    expect(started.ok).toBe(true)
    const port = started.port
    const owner = readGatewayOwner(port)
    expect(owner).not.toBeNull()
    writeGatewayOwner({ ...(owner as NonNullable<typeof owner>), hostId: 'another-dsh-host' })

    const second = make(port)
    const startedAt = Date.now()
    const result = await startGateway(second, { handleSignals: false })
    expect(Date.now() - startedAt).toBeLessThan(3000)
    expect(result.ok).toBe(false)
    expect(result.error).toMatchObject({
      stage: 'config',
      code: 'GATEWAY_IN_USE',
      port,
      evidence: { owner: { hostId: 'another-dsh-host' } },
    })
    // The failed attempt must not replace the original owner's identity.
    expect(readGatewayOwner(port)?.hostId).toBe('another-dsh-host')
  })

  it('ignores an owner file left behind by a dead Gateway', async () => {
    const first = make()
    const started = await startGateway(first, { handleSignals: false })
    expect(started.ok).toBe(true)
    const port = started.port
    stopGateway(first)
    const child = spawn(process.execPath, ['-e', ''], { stdio: 'ignore' })
    await new Promise<void>((resolve) => child.once('exit', () => resolve()))
    const alive = readGatewayOwner(port)
    expect(alive).toBeNull()
    fs.writeFileSync(
      gatewayStatePath(port),
      JSON.stringify({ gatewayId: 'gw-dead', hostId: 'gone-host', pid: child.pid, port }),
      'utf8',
    )
    // The crashed Gateway's file must not turn a plain port conflict into GATEWAY_IN_USE.
    expect(readGatewayOwner(port)).toBeNull()
    fs.rmSync(gatewayStatePath(port), { force: true })
  })

  it('reports port_conflict with the occupying pid/process when no owner file exists', async () => {
    const first = make()
    const started = await startGateway(first, { handleSignals: false })
    expect(started.ok).toBe(true)
    const port = started.port
    stopGateway(first)
    // Re-listen without the owner file to emulate an unrelated process holding the port.
    const holder = make()
    holder.port = port
    await new Promise<void>((resolve, reject) => {
      holder.server.once('error', reject)
      holder.server.listen(port, '127.0.0.1', () => resolve())
    })

    const second = make(port)
    const startedAt = Date.now()
    const result = await startGateway(second, { handleSignals: false })
    expect(Date.now() - startedAt).toBeLessThan(3000)
    expect(result.ok).toBe(false)
    expect(result.error).toMatchObject({ stage: 'config', code: 'port_conflict', port })
    const evidence = result.error?.evidence as
      { probe?: { pid?: number | null; process?: string | null } } | undefined
    expect(evidence?.probe).toBeTruthy()
    expect(
      evidence?.probe?.pid === undefined || evidence.probe.pid === null || evidence.probe.pid > 0,
    ).toBe(true)
    holder.close()
  })
})
