import { describe, expect, it } from 'vitest'
import http from 'node:http'
import path from 'node:path'
import { pathToFileURL } from 'node:url'
import { decideStart, probeHttp, probeTcp, waitHttp } from '../../../runtime/backend_probe.js'
import { pkgPath } from '../../helpers/pkg'

// S7 regression: op=start used to trust the child handle only. A live handle on
// a dead port made the backend answer "already running" while the port was not
// listening. TCP and HTTP are probed separately, and the decision table has to
// call that state a stale handle, not "already running".

const listen = async (server: http.Server): Promise<number> => {
  await new Promise<void>((resolve, reject) => {
    server.once('error', reject)
    server.listen(0, '127.0.0.1', resolve)
  })
  return (server.address() as { port: number }).port
}

describe('backend probe and start decision', () => {
  it('probes a live server as true, and a closed port as false', async () => {
    const server = http.createServer((_req, res) => { res.writeHead(200); res.end('ok') })
    const port = await listen(server)
    const url = 'http://127.0.0.1:' + String(port) + '/health'
    expect(await probeHttp(url, 800)).toBe(true)
    expect(await probeTcp('127.0.0.1', port, 500)).toBe(true)
    await new Promise<void>((resolve) => server.close(() => resolve()))
    expect(await probeHttp(url, 800)).toBe(false)
    expect(await probeTcp('127.0.0.1', port, 400)).toBe(false)
  })

  it('calls a listening port already-running, a dead port with a live handle stale-handle, and the rest spawn', () => {
    expect(decideStart({ portUp: true, childAlive: true })).toBe('already-running')
    expect(decideStart({ portUp: false, childAlive: true })).toBe('stale-handle')
    expect(decideStart({ portUp: false, childAlive: false })).toBe('spawn')
  })

  it('waits for a late server and returns false on a pure timeout', async () => {
    const server = http.createServer((_req, res) => { res.writeHead(200); res.end('ok') })
    const port = await new Promise<number>((resolve) => {
      const probe = netPort()
      probe.then(resolve)
    })
    function netPort() {
      return new Promise<number>((resolve) => {
        const tmp = http.createServer()
        tmp.listen(0, '127.0.0.1', () => {
          const p = (tmp.address() as { port: number }).port
          tmp.close(() => resolve(p))
        })
      })
    }
    const url = 'http://127.0.0.1:' + String(port) + '/health'
    setTimeout(() => { server.listen(port, '127.0.0.1') }, 700)
    expect(await waitHttp(url, 6000, 200)).toBe(true)
    await new Promise<void>((resolve) => server.close(() => resolve()))
    expect(await waitHttp(url, 1200, 200)).toBe(false)
  }, 20000)

  it('resolves the runtime module the lib side imports', async () => {
    const mod = await import(pathToFileURL(path.join(pkgPath('lib', 'runtime', 'backend_probe.js'))).href)
    expect(typeof mod.probeHttp).toBe('function')
    expect(typeof mod.decideStart).toBe('function')
  })
})
