import { afterAll, beforeAll, describe, expect, it } from 'vitest'
import { spawn, type ChildProcess } from 'node:child_process'
import http from 'node:http'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import net from 'node:net'
import { pkgPath, PKG_ROOT } from '../../helpers/pkg'

function freePort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const s = net.createServer()
    s.listen(0, '127.0.0.1', () => {
      const port = (s.address() as any).port
      s.close(() => resolve(port))
    })
    s.on('error', reject)
  })
}

async function waitHealth(port: number, timeoutMs = 12000): Promise<void> {
  const started = Date.now()
  for (;;) {
    try {
      const r = await fetch('http://127.0.0.1:' + port + '/health')
      if (r.ok) return
    } catch (e) { /* not up yet */ }
    if (Date.now() - started > timeoutMs) throw new Error('server did not become healthy')
    await new Promise((r) => setTimeout(r, 100))
  }
}

function spawnServer(port: number, workDir: string): ChildProcess {
  return spawn(process.execPath, [pkgPath('lib', 'runtime', 'server.js'), '--port', String(port)], {
    cwd: PKG_ROOT,
    env: {
      ...process.env,
      DSH_BLENDER_HTTP_PORT: String(port),
      DSH_BLENDER_WORKER_PORT: String(port + 1),
      DSH_BLENDER_WORKDIR: workDir,
      DSH_BLENDER_EXE: path.join(workDir, 'no-blender.exe'),
    },
    stdio: ['ignore', 'pipe', 'pipe'],
  })
}

async function stopChild(child: ChildProcess): Promise<void> {
  if (!child || child.exitCode !== null) return
  child.kill('SIGTERM')
  await new Promise<void>((resolve) => {
    const timer = setTimeout(() => { try { child.kill('SIGKILL') } catch (e) {} resolve() }, 5000)
    child.once('exit', () => { clearTimeout(timer); resolve() })
  })
}

describe('live HTTP route contract', () => {
  let workDir: string
  let child: ChildProcess
  let port: number
  let stderr = ''
  let stdout = ''

  beforeAll(async () => {
    workDir = fs.mkdtempSync(path.join(os.tmpdir(), 'dsh-t14-routes-'))
    port = await freePort()
    child = spawnServer(port, workDir)
    child.stderr?.on('data', (d) => { stderr += String(d) })
    child.stdout?.on('data', (d) => { stdout += String(d) })
    await waitHealth(port)
  }, 20000)

  afterAll(async () => {
    await stopChild(child)
    try { fs.rmSync(workDir, { recursive: true, force: true }) } catch (e) {}
  })

  it('advertises exactly the 29 supported routes at startup', () => {
    const line = stdout.split('\n').find((entry) => entry.includes('(routes: '))
    expect(line).toBeTruthy()
    const routes = line!.split('(routes: ')[1].replace(/\)$/, '').trim().split(/\s+/)
    expect(routes).toHaveLength(29)
    expect(routes).toContain('/health')
    expect(routes).toContain('/host/selection/consume')
    expect(routes).not.toContain('/stream.mjpg')
  })

  it('serves health, status, who, and host state as JSON', async () => {
    const health = await (await fetch('http://127.0.0.1:' + port + '/health')).json() as any
    expect(health.ok).toBe(true)
    expect(health.backend.pid).toBe(child.pid)
    expect(health.provenance).toBeTruthy()

    const status = await (await fetch('http://127.0.0.1:' + port + '/status')).json() as any
    expect(typeof status.ok).toBe('boolean')
    expect(status.plugin.version).toMatch(/^\d+\.\d+\.\d+$/)

    const who = await (await fetch('http://127.0.0.1:' + port + '/who')).json() as any
    expect(who.lease.active).toBe(false)
    expect(who.metrics).toBeTruthy()

    const doctorRes = await fetch('http://127.0.0.1:' + port + '/doctor')
    expect(doctorRes.status).toBe(200)
    const doctor = await doctorRes.json() as any
    expect(typeof doctor.ok).toBe('boolean')
    expect(doctor.config.http).toContain(String(port))

    const origin = 'http://127.0.0.1:3456'
    const stateRes = await fetch('http://127.0.0.1:' + port + '/host/state', { headers: { origin } })
    expect(stateRes.status).toBe(200)
    expect(stateRes.headers.get('access-control-allow-origin')).toBe(origin)
    const state = await stateRes.json() as any
    expect(state.ok).toBe(true)
    expect(state.channel).toBeTruthy()
    expect(state.jobs).toBeTruthy()
  })

  it('returns 410 for the removed human viewport routes', async () => {
    for (const p of ['/frame', '/', '/index.html', '/stream.mjpg', '/input']) {
      const r = await fetch('http://127.0.0.1:' + port + p)
      expect(r.status, p).toBe(410)
      expect((await r.json() as any).error, p).toBe('gone')
    }
  })

  it('enforces the write lease and exempts read-only plan ops', async () => {
    const lease = await (await fetch('http://127.0.0.1:' + port + '/lease', {
      method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ holder: 'holder-a' }),
    })).json() as any
    expect(lease.ok).toBe(true)
    expect(lease.lease.active).toBe(true)

    const denied = await fetch('http://127.0.0.1:' + port + '/act', {
      method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ holder: 'holder-b', code: 'x = 1' }),
    })
    expect(denied.status).toBe(409)
    const deniedBody = await denied.json() as any
    expect(deniedBody.error).toBe('leased')
    expect(deniedBody.holder).toBe('holder-a')

    const plan = await fetch('http://127.0.0.1:' + port + '/plan', {
      method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ holder: 'holder-b', op: 'catalog', args: {} }),
    })
    expect(plan.status).toBe(200)
    const planBody = await plan.json() as any
    expect(planBody.ok).toBe(true)

    const released = await (await fetch('http://127.0.0.1:' + port + '/release', {
      method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ holder: 'holder-a' }),
    })).json() as any
    expect(released.ok).toBe(true)
    expect(released.lease.active).toBe(false)
  })

  it('stores, lists, and consumes a native selection event', async () => {
    const put = await fetch('http://127.0.0.1:' + port + '/host/selection', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ selection: { names: ['Cube', 'Camera'], count: 2 }, scene: { name: 'Scene' } }),
    })
    expect(put.status).toBe(200)
    const stored = await put.json() as any
    expect(stored.ok).toBe(true)
    expect(stored.seq).toBeGreaterThan(0)

    const latest = await (await fetch('http://127.0.0.1:' + port + '/host/selection/latest')).json() as any
    expect(latest.ok).toBe(true)
    expect(latest.selection.names).toEqual(['Cube', 'Camera'])

    const consumed = await (await fetch('http://127.0.0.1:' + port + '/host/selection/consume', {
      method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ seq: stored.seq }),
    })).json() as any
    expect(consumed.ok).toBe(true)

    const unknown = await fetch('http://127.0.0.1:' + port + '/host/nope')
    expect(unknown.status).toBe(404)
    expect(unknown.headers.get('access-control-allow-origin')).toBeNull()
  })

  it('leaves no error output for an ordinary shutdown', async () => {
    await stopChild(child)
    expect(stderr).not.toContain('EADDRINUSE')
    expect(stderr).not.toContain('Unhandled')
  })
})

describe('port conflict contract', () => {
  it('exits 0 when a responsive backend already owns the port', async () => {
    const blocker = http.createServer((req, res) => { res.writeHead(200); res.end('ok') })
    await new Promise<void>((resolve) => blocker.listen(0, '127.0.0.1', resolve))
    const port = (blocker.address() as any).port
    const workDir = fs.mkdtempSync(path.join(os.tmpdir(), 'dsh-t14-conflict-'))
    const child = spawnServer(port, workDir)
    let err = ''
    child.stderr?.on('data', (d) => { err += String(d) })
    const code = await new Promise<number | null>((resolve, reject) => {
      const timer = setTimeout(() => { try { child.kill('SIGKILL') } catch (e) {} reject(new Error('port conflict did not exit')) }, 12000)
      child.once('exit', (c) => { clearTimeout(timer); resolve(c) })
    })
    await new Promise<void>((resolve) => blocker.close(() => resolve()))
    try { fs.rmSync(workDir, { recursive: true, force: true }) } catch (e) {}
    expect(code).toBe(0)
    expect(err).toContain('能应答')
  }, 20000)
})
