/**
 * A tiny stand-in for the Blender addon's flat JSON socket protocol.
 *
 * It listens on an ephemeral port (never 9876), so integration tests can exercise
 * the real AddonClient handshake / success / failure / timeout / disconnect paths
 * without starting Blender.
 */
import net from 'node:net'

const DEFAULT_DELAY_MS = 250

function defaultBehavior(type, params) {
  if (type === 'ping') return { status: 'success', result: { pong: true, server: 'fake-addon' } }
  if (type === 'get_addon_info') {
    return { status: 'success', result: { name: 'DSH Blender Bridge', version: '1.8.0', protocol: 11, paused: false } }
  }
  if (type === 'fail') return { status: 'error', message: 'fake addon failure' }
  if (type === 'paused') return { status: 'error', message: 'AI 操作已暂停（fake addon）' }
  if (type === 'slow') return { status: 'success', result: { slow: true, params } }
  return { status: 'success', result: { type, params: params || {} } }
}

/**
 * @param {{ behavior?: (type: string, params: object) => object | Promise<object> }} [options]
 * @returns {Promise<{port:number, close:()=>Promise<void>, sockets:Set<net.Socket>}>}
 */
export async function startFakeAddon(options = {}) {
  const behavior = options.behavior || defaultBehavior
  const sockets = new Set()

  const server = net.createServer((socket) => {
    sockets.add(socket)
    socket.on('close', () => sockets.delete(socket))
    socket.on('error', () => {})
    let buf = ''
    socket.on('data', (chunk) => {
      buf += chunk.toString('utf8')
      let idx = buf.indexOf('\n')
      while (idx >= 0) {
        const line = buf.slice(0, idx).trim()
        buf = buf.slice(idx + 1)
        idx = buf.indexOf('\n')
        if (line) void handleLine(socket, line)
      }
      // Tolerate clients that send a bare JSON object without a newline.
      if (buf.trim().startsWith('{')) {
        try {
          const obj = JSON.parse(buf)
          buf = ''
          void handleLine(socket, JSON.stringify(obj))
        } catch {
          // incomplete JSON: wait for more bytes
        }
      }
    })
  })

  async function handleLine(socket, line) {
    let req
    try {
      req = JSON.parse(line)
    } catch {
      socket.end(JSON.stringify({ status: 'error', message: 'bad json' }) + '\n')
      return
    }
    const type = String((req && req.type) || '')
    const params = (req && req.params) || {}
    if (type === 'disconnect') {
      socket.destroy()
      return
    }
    if (type === 'hang') return
    const out = await behavior(type, params)
    if (out && out.action === 'close') {
      socket.destroy()
      return
    }
    socket.write(JSON.stringify(out) + '\n')
  }

  await new Promise((resolve, reject) => {
    server.once('error', reject)
    server.listen(0, '127.0.0.1', resolve)
  })
  const addr = server.address()
  if (!addr || typeof addr === 'string') throw new Error('fake addon did not bind a TCP port')

  return {
    port: addr.port,
    sockets,
    async close() {
      for (const s of sockets) s.destroy()
      sockets.clear()
      await new Promise((resolve) => server.close(resolve))
    },
  }
}

export { DEFAULT_DELAY_MS }
