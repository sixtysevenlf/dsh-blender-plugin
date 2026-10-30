/**
 * Minimal HTTP stand-in for the local backend (127.0.0.1:9877).
 *
 * Tests plug the real plugin into this server on an ephemeral port. No Blender,
 * no addon, no fixed port. Handlers are mutable so a test can simulate 409s or
 * a hanging request without touching the product code.
 */
import http from 'node:http'

export const TINY_PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAFgwJ/lVf1WQAAAABJRU5ErkJggg==',
  'base64',
)

const json = (body, status = 200, headers = {}) => ({
  status,
  headers: { 'content-type': 'application/json', ...headers },
  body: Buffer.from(JSON.stringify(body)),
})

function defaults(key, body) {
  if (key === 'GET /health') return json({ ok: true, stats: {}, lease: { active: false } })
  if (key === 'GET /status') {
    return json({
      ok: true,
      region: { window: null, views: [] },
      plugin: { version: '2.6.2', runtime: { hash: 'fake' } },
      runs: [],
      stats: {},
      ledger: { writable: true, workDir: { wsl: '/tmp' } },
    })
  }
  if (key === 'GET /doctor') return json({ ok: true, kind: 'ok' })
  if (key === 'POST /launch') return json({ ok: true, already: true })
  if (key === 'POST /act') return json({ ok: true, ms: 4, mainThreadMs: 2, stdout: 'fake stdout' })
  if (key === 'GET /frame.png') {
    return { status: 200, headers: { 'content-type': 'image/png', 'cache-control': 'no-store' }, body: TINY_PNG }
  }
  if (key === 'POST /view') {
    return {
      status: 200,
      headers: {
        'content-type': 'image/png',
        'x-dsh-view': encodeURIComponent(JSON.stringify({ mode: 'viewport', width: 560, height: 315 })),
      },
      body: TINY_PNG,
    }
  }
  if (key === 'GET /commands') {
    return json({
      ok: true,
      scene: 'Scene',
      file: null,
      total: 39,
      disabled: ['search_polyhaven_assets'],
      integrations: { polyhaven: { enabled: false, message: 'off' }, sketchfab: { enabled: true } },
      available: ['ping', 'get_scene_info'],
    })
  }
  if (key === 'POST /cmd') return json({ ok: true, ms: 4, result: { command: body.name } })
  if (key === 'POST /loop') return json({ ok: true, op: body.op, result: { op: body.op } })
  if (key === 'POST /perf') return json({ ok: true, op: body.op, result: { op: body.op } })
  if (key === 'POST /opt') return json({ ok: true, op: body.op, result: { op: body.op } })
  if (key === 'POST /headless') {
    return json({ ok: true, ms: 8, result: { status: 'done', resultJson: { fake: true }, shots: [] } })
  }
  if (key === 'POST /plan') return json({ ok: true, ms: 5, result: { ok: true, verdict: 'pass', summary: 'fake plan' } })
  if (key === 'POST /worker') {
    if (body.op === 'see') {
      return json({
        ok: true,
        result: { ok: true, png_b64: TINY_PNG.toString('base64'), ms: 3, width: 1, height: 1, bytes: TINY_PNG.length },
      })
    }
    if (body.op === 'exec') return json({ ok: true, result: { ok: true, ms: 3, result: 'fake exec' } })
    if (body.op === 'list') return json({ ok: true, result: { ok: true, workers: [] } })
    return json({ ok: true, result: { ok: true, name: body.name || 'default', alive: true, ready: true, pid: 1, port: 1 } })
  }
  if (key === 'POST /txn') return json({ ok: true, result: { snapshots: [], marks: [], snapshot_dir: '/tmp/fake' } })
  if (key === 'POST /preset') return json({ ok: true, result: { count: 0, presets: [], dir: '/tmp/fake' } })
  if (key === 'POST /job') {
    if (body.op === 'list') return json({ ok: true, jobs: [{ id: 'job-1', status: 'done', artifacts: [], ms: 7 }] })
    if (body.op === 'start') return json({ ok: true, job: { id: 'job-1', pid: 1, logDir: '/tmp/fake', status: 'running' } })
    return json({ ok: true, job: { id: 'job-1', status: 'done', resultJson: { ok: true }, artifacts: [] } })
  }
  if (key === 'GET /who') return json({ ok: true, lease: { active: false }, metrics: {}, stats: {} })
  if (key === 'POST /lease') {
    return json({ ok: true, lease: { active: true, holder: body.holder, expiresInMs: 600000, renewals: 0 } })
  }
  if (key === 'POST /release') return json({ ok: true, released: body.holder || null, lease: { active: false } })
  return json({ ok: true, fake: key, op: body && body.op })
}

export async function startFakeBackend(options = {}) {
  const requests = []
  const sockets = new Set()
  const handlers = new Map(Object.entries(options.handlers || {}))
  const hang = new Set(options.hang || [])

  const server = http.createServer(async (req, res) => {
    const url = new URL(req.url, 'http://127.0.0.1')
    const key = req.method + ' ' + url.pathname
    const chunks = []
    for await (const chunk of req) chunks.push(chunk)
    const raw = Buffer.concat(chunks).toString('utf8')
    let body = {}
    try { body = raw ? JSON.parse(raw) : {} } catch { body = { __raw: raw } }
    requests.push({ method: req.method, path: url.pathname, search: url.search, body })

    if (hang.has(key)) return
    const handler = handlers.get(key)
    let out
    try { out = handler ? await handler(body, req) : defaults(key, body) } catch (e) {
      out = json({ ok: false, error: String((e && e.message) || e) }, 500)
    }
    if (out && out.hang) return
    if (!out) out = json({ ok: false, error: 'no response' }, 500)
    res.writeHead(out.status || 200, out.headers || {})
    res.end(out.body === undefined ? Buffer.alloc(0) : out.body)
  })
  server.on('connection', (s) => { sockets.add(s); s.on('close', () => sockets.delete(s)) })

  await new Promise((resolve, reject) => {
    server.once('error', reject)
    server.listen(0, '127.0.0.1', resolve)
  })
  const addr = server.address()
  if (!addr || typeof addr === 'string') throw new Error('fake backend did not bind a TCP port')

  return {
    port: addr.port,
    requests,
    handlers,
    hang,
    setHandler(key, fn) { if (fn) handlers.set(key, fn); else handlers.delete(key) },
    hangOn(key) { hang.add(key) },
    unhang(key) { hang.delete(key) },
    async close() {
      for (const s of sockets) s.destroy()
      sockets.clear()
      await new Promise((resolve) => server.close(resolve))
    },
  }
}

/** Reserve then release a port so the caller can test "nothing is listening". */
export async function closedPort() {
  const srv = http.createServer(() => {})
  await new Promise((resolve) => srv.listen(0, '127.0.0.1', resolve))
  const addr = srv.address()
  const port = addr && typeof addr === 'object' ? addr.port : 0
  await new Promise((resolve) => srv.close(resolve))
  return port
}
