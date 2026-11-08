import { spawn, type ChildProcessWithoutNullStreams } from 'node:child_process'
import fs from 'node:fs'
import net from 'node:net'
import os from 'node:os'
import path from 'node:path'
import { pkgPath } from './pkg'

// Shared integration harness: a real lib/runtime/gateway.js on an ephemeral port with
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
  post: (
    route: string,
    body?: unknown,
    headers?: Record<string, string>,
  ) => Promise<{ status: number; json: any }>
  get: (route: string, headers?: Record<string, string>) => Promise<{ status: number; json: any }>
  stop: () => Promise<void>
}

export async function startBackend(
  options: {
    env?: Record<string, string>
    workDir?: string
    addonPort?: number
  } = {},
  _attempt = 0,
): Promise<Backend> {
  const port = await freePort()
  const workDir = options.workDir || fs.mkdtempSync(path.join(os.tmpdir(), 'dsh-it-'))
  let log = ''
  const child = spawn(
    process.execPath,
    [pkgPath('lib', 'runtime', 'gateway.js'), '--port', String(port)],
    {
      cwd: pkgPath(),
      env: Object.assign(
        {},
        process.env,
        {
          DSH_BLENDER_WORKDIR: workDir,
          DSH_BLENDER_GATEWAY_PORT: String(port),
          DSH_BLENDER_HTTP_PORT: String(port),
          DSH_BLENDER_ADDON_HOST: '127.0.0.1',
          ...(options.addonPort ? { DSH_BLENDER_ADDON_PORT: String(options.addonPort) } : {}),
        },
        options.env || {},
      ),
      stdio: ['ignore', 'pipe', 'pipe'],
    },
  ) as unknown as ChildProcessWithoutNullStreams
  child.stdout?.on('data', (chunk) => {
    log += chunk.toString()
  })
  child.stderr?.on('data', (chunk) => {
    log += chunk.toString()
  })
  const base = 'http://127.0.0.1:' + String(port)
  const deadline = Date.now() + 15000
  let up = false
  let healthDetail = ''
  while (Date.now() < deadline) {
    // 子进程已退出（端口被抢 / 启动崩溃）就没必要等满 15s。
    if (child.exitCode !== null) break
    try {
      const response = await fetch(base + '/health')
      if (response.ok) {
        // 必须确认是本插件的 JSON 回执：freePort() 先关探测 socket 再 spawn，
        // 并行 worker 可能拿到同一个 ephemeral 端口——抢输的那个会「健康检查」到
        // 另一个测试的纯文本 'ok' 服务器，然后在断言处炸成 JSON.parse('ok')。
        const body = (await response.json().catch(() => null)) as Record<string, unknown> | null
        if (body && body.ok === true) {
          up = true
          break
        }
        healthDetail = 'health 不是后端 JSON：' + JSON.stringify(body).slice(0, 80)
      }
    } catch (e) {
      healthDetail = String(e)
    }
    await new Promise((resolve) => setTimeout(resolve, 100))
  }
  if (!up) {
    child.kill('SIGKILL')
    // 端口竞争是 ephemeral 分配的固有小概率事件：换一个端口重试，而不是把
    // 别人服务器的响应当成自己的后端（那会变成与代码无关的 flaky）。
    if (_attempt < 2) {
      await new Promise((resolve) => setTimeout(resolve, 100))
      return startBackend(options, _attempt + 1)
    }
    throw new Error(
      'backend did not become healthy: ' +
        log.slice(-1000) +
        (healthDetail ? ' (' + healthDetail + ')' : ''),
    )
  }
  const post = async (route: string, body?: unknown, headers?: Record<string, string>) => {
    const response = await fetch(base + route, {
      method: 'POST',
      headers: { 'content-type': 'application/json', ...(headers || {}) },
      body: JSON.stringify(body || {}),
    })
    return { status: response.status, json: await response.json() }
  }
  const get = async (route: string, headers?: Record<string, string>) => {
    const response = await fetch(base + route, { headers: headers || {} })
    return { status: response.status, json: await response.json() }
  }
  return {
    base,
    port,
    workDir,
    child,
    log: () => log,
    post,
    get,
    async stop() {
      if (child.exitCode === null) {
        child.kill('SIGTERM')
        await Promise.race([
          new Promise((resolve) => child.once('exit', resolve)),
          new Promise((resolve) => setTimeout(resolve, 3000)),
        ])
        if (child.exitCode === null) child.kill('SIGKILL')
      }
      try {
        fs.rmSync(workDir, { recursive: true, force: true })
      } catch {
        /* ignore */
      }
    },
  }
}
