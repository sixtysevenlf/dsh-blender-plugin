
import http from 'node:http'

// Tiny HTTP stand-in for the TUI client half. The real bridge only talks to the
// local backend over 127.0.0.1, so integration tests can drive every branch with
// a scripted backend instead of a real one.

export interface FakeRequest {
  method: string
  path: string
  body: any
}

export interface FakeHost {
  port: number
  base: string
  requests: FakeRequest[]
  close: () => Promise<void>
}

export type FakeHandler = (request: FakeRequest) => { status?: number, json: unknown } | undefined

export async function startFakeHost(handler: FakeHandler): Promise<FakeHost> {
  const requests: FakeRequest[] = []
  const server = http.createServer((req, res) => {
    let raw = ''
    req.on('data', (chunk) => { raw += chunk.toString('utf8') })
    req.on('end', () => {
      let body: any = null
      try { body = raw ? JSON.parse(raw) : null } catch { body = raw }
      const request: FakeRequest = { method: String(req.method), path: String(req.url), body }
      requests.push(request)
      let out: { status?: number, json: unknown } | undefined
      try {
        out = handler(request)
      } catch (error) {
        out = { status: 500, json: { ok: false, error: String(error) } }
      }
      if (!out) {
        res.writeHead(404, { 'content-type': 'application/json' })
        res.end(JSON.stringify({ ok: false, error: 'not found' }))
        return
      }
      res.writeHead(out.status || 200, { 'content-type': 'application/json' })
      res.end(JSON.stringify(out.json))
    })
  })
  await new Promise<void>((resolve, reject) => {
    server.once('error', reject)
    server.listen(0, '127.0.0.1', resolve)
  })
  const address = server.address() as { port: number }
  return {
    port: address.port,
    base: 'http://127.0.0.1:' + String(address.port),
    requests,
    async close() {
      await new Promise<void>((resolve) => server.close(() => resolve()))
    },
  }
}
