import { spawn, type ChildProcessWithoutNullStreams } from 'node:child_process'
import fs from 'node:fs'
import net from 'node:net'
import os from 'node:os'
import path from 'node:path'
import { pkgPath } from './pkg'

// Shared integration harness: a real runtime/server.mjs on an ephemeral port with
// a throw-away workDir, plus a small fetch wrapper. Integration never touches the
// user's live 9877 backend or the fixed 9876 addon port.

export async function freePort(): Promise<number> {
  return await new Promise((resolve, reject) => {
    const server = net.createServer()
    server.once('error', reject)
    server.listen(0, '127.0.0.1', () => {
      const address = server.address() as net.AddressInfo
      server.close(() => resolve(address.port))
    })
  })
}

export interface Backend {
  base: string
  port: number
  workDir: string
  child: ChildProcessWithoutNullStreams
  log: () => string
  post: (route: string, body?: unknown) => Promise<{ status: number, json: any }>
  get: (route: string) => Promise<{ status: number, json: any }>
  stop: () => Promise<void>
}

export async function startBackend(options: {
  env?: Record<string, string>
  workDir?: string
  addonPort?: number
} = {}): Promise<Backend> {
  const port = await freePort()
  const workDir = options.workDir || fs.mkdtempSync(path.join(os.tmpdir(), 'dsh-it-'))
  let log = ''
  const child = spawn(process.execPath, [pkgPath('runtime', 'server.mjs'), '--port', String(port)], {
    cwd: pkgPath(),
    env: Object.assign({}, process.env, {
      DSH_BLENDER_WORKDIR: workDir,
      DSH_BLENDER_HTTP_PORT: String(port),
      DSH_BLENDER_ADDON_HOST: '127.0.0.1',
      ...(options.addonPort ? { DSH_BLENDER_ADDON_PORT: String(options.addonPort) } : {}),
    }, options.env || {}),
    stdio: ['ignore', 'pipe', 'pipe'],
  }) as ChildProcessWithoutNullStreams
  child.stdout?.on('data', (chunk) => { log += chunk.toString() })
  child.stderr?.on('data', (chunk) => { log += chunk.toString() })
  const base = 'http://127.0.0.1:' + String(port)
  const deadline = Date.now() + 15000
  let up = false
  while (Date.now() < deadline) {
    try {
      const response = await fetch(base + '/health')
      if (response.ok) { up = true; break }
    } catch { /* not listening yet */ }
    await new Promise((resolve) => setTimeout(resolve, 100))
  }
  if (!up) {
    child.kill('SIGKILL')
    throw new Error('backend did not become healthy: ' + log.slice(-1000))
  }
  const post = async (route: string, body?: unknown) => {
    const response = await fetch(base + route, {
      method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body || {}),
    })
    return { status: response.status, json: await response.json() }
  }
  const get = async (route: string) => {
    const response = await fetch(base + route)
    return { status: response.status, json: await response.json() }
  }
  return {
    base, port, workDir, child, log: () => log, post, get,
    async stop() {
      if (child.exitCode === null) {
        child.kill('SIGTERM')
        await Promise.race([
          new Promise((resolve) => child.once('exit', resolve)),
          new Promise((resolve) => setTimeout(resolve, 3000)),
        ])
        if (child.exitCode === null) child.kill('SIGKILL')
      }
      try { fs.rmSync(workDir, { recursive: true, force: true }) } catch { /* ignore */ }
    },
  }
}
