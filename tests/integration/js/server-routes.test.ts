import { afterAll, beforeAll, describe, expect, it } from 'vitest'
import { spawn } from "node:child_process"
import net from 'node:net'
import { pkgPath } from '../../helpers/pkg'
import { startFakeAddon } from '../../helpers/fake-addon.mjs'

let addon: { port: number; close: () => Promise<void> }
let child: ReturnType<typeof spawn>
let base = ''
let childLog = ''

async function freePort(): Promise<number> {
  return await new Promise((resolve, reject) => {
    const s = net.createServer()
    s.once('error', reject)
    s.listen(0, '127.0.0.1', () => {
      const port = (s.address() as net.AddressInfo).port
      s.close(() => resolve(port))
    })
  })
}

async function waitHealth(url: string, timeoutMs = 10000): Promise<void> {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    try {
      const r = await fetch(url + '/health')
      if (r.ok) return
    } catch {
      // not listening yet
    }
    await new Promise((r) => setTimeout(r, 100))
  }
  throw new Error('backend did not become healthy: ' + childLog.slice(-800))
}

async function post(path: string, body: unknown) {
  return await fetch(base + path, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
  })
}

beforeAll(async () => {
  addon = await startFakeAddon()
  const port = await freePort()
  base = 'http://127.0.0.1:' + String(port)
  child = spawn(process.execPath, [pkgPath('lib', 'runtime', 'server.js'), '--port', String(port)], {
    cwd: pkgPath(),
    env: { ...process.env, DSH_BLENDER_ADDON_HOST: '127.0.0.1', DSH_BLENDER_ADDON_PORT: String(addon.port) },
    stdio: ['ignore', 'pipe', 'pipe'],
  })
  child.stdout?.on('data', (c) => { childLog += c.toString() })
  child.stderr?.on('data', (c) => { childLog += c.toString() })
  await waitHealth(base)
}, 20000)

afterAll(async () => {
  if (child && child.exitCode === null) {
    child.kill('SIGTERM')
    await Promise.race([
      new Promise((r) => child.once('exit', r)),
      new Promise((r) => setTimeout(r, 3000)),
    ])
    if (child.exitCode === null) child.kill('SIGKILL')
  }
  if (addon) await addon.close()
})

describe('backend HTTP routes against a fake addon (T7)', () => {
  it('serves /health, /who and /host/state', async () => {
    const h = await (await fetch(base + '/health')).json() as any
    expect(h.ok).toBe(true)
    expect(h.stats).toBeTruthy()
    expect(h.lease.active).toBe(false)
    expect(h.rtt.frame).toBeTruthy()

    const w = await (await fetch(base + '/who')).json() as any
    expect(w.ok).toBe(true)
    expect(w.config.http).toBe(new URL(base).host)
    expect(w.metrics.inflight).toBe(0)

    const s = await (await fetch(base + '/host/state')).json() as any
    expect(s.ok).toBe(true)
    expect(s.channel.aiPaused).toBe(false)
    expect(s.jobs.running).toBe(0)
  })

  it('keeps gone and 404 semantics', async () => {
    const gone = await fetch(base + '/frame')
    expect(gone.status).toBe(410)
    expect((await gone.json() as any).error).toBe('gone')
    expect((await fetch(base + '/nope')).status).toBe(404)
  })

  it('enforces the write lease and still allows read-only ops', async () => {
    const holder = 'plugin-pid-' + String(child.pid)
    const granted = await post('/lease', { holder })
    expect(granted.status).toBe(200)
    expect((await granted.json() as any).lease.holder).toBe(holder)

    const denied = await post('/cmd', { holder: 'plugin-other', name: 'ping' })
    expect(denied.status).toBe(409)
    expect(await denied.json() as any).toMatchObject({ ok: false, error: 'leased', holder })

    const catalog = await post('/plan', { op: 'catalog', holder: 'plugin-other' })
    expect(catalog.status).toBe(200)
    const cj = await catalog.json() as any
    expect(cj.ok).toBe(true)
    expect(cj.result.ops_count).toBe(228)

    const ok = await post('/cmd', { holder, name: 'ping', params: {} })
    expect(ok.status).toBe(200)
    expect(await ok.json() as any).toMatchObject({ ok: true, result: { pong: true } })

    const renewed = await post('/lease', { holder, renewOnly: true })
    expect((await renewed.json() as any).renewed).toBe(true)

    const released = await post('/release', { holder })
    expect((await released.json() as any).released).toBe(holder)
  })

  it('keeps /host CORS rules exact', async () => {
    const ok = await fetch(base + '/host/state', { method: 'OPTIONS', headers: { origin: 'dsh-app://app' } })
    expect(ok.status).toBe(204)
    expect(ok.headers.get('access-control-allow-origin')).toBe('dsh-app://app')

    const evil = await fetch(base + '/host/state', { method: 'OPTIONS', headers: { origin: 'https://evil.example' } })
    expect(evil.status).toBe(204)
    expect(evil.headers.get('access-control-allow-origin')).toBeNull()
  })

  it('lists jobs read-only without touching Blender', async () => {
    const j = await (await fetch(base + '/host/jobs')).json() as any
    expect(j.ok).toBe(true)
    expect(Array.isArray(j.jobs)).toBe(true)
    // /host/jobs reads the shared on-disk ledger, so other runs may have left
    // finished jobs behind; assert the envelope shape, not an empty machine.
    expect(typeof j.total).toBe('number')
    expect(j.total).toBeGreaterThanOrEqual(j.jobs.length)
  })
})
