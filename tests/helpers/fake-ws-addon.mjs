/**
 * Fake Blender addon for the 3.1 Gateway transport: dials ws://<gateway>/addon,
 * performs the protocol-12 handshake, then answers `{type,params,requestId}`
 * requests with the legacy flat envelope `{status:'success'|'error', result|message, requestId}`.
 *
 * Node >= 24 ships a global WebSocket client, so the helper needs no `ws` dependency.
 */
const DEFAULT_DELAY_MS = 250

function defaultBehavior(type, params) {
  if (type === 'ping') return { status: 'success', result: { pong: true, server: 'fake-ws-addon' } }
  if (type === 'get_addon_info') {
    return {
      status: 'success',
      result: { name: 'DSH Blender Bridge', version: '2.0.0', protocol: 12, paused: false },
    }
  }
  if (type === 'fail') return { status: 'error', message: 'fake addon failure' }
  if (type === 'paused') return { status: 'error', message: 'AI 操作已暂停（fake ws addon）' }
  if (type === 'slow') return { status: 'success', result: { slow: true, params } }
  return { status: 'success', result: { type, params: params || {} } }
}

/**
 * @param {{
 *   gatewayPort: number,
 *   instanceId?: string,
 *   kind?: string,
 *   version?: string,
 *   protocol?: number,
 *   project?: string,
 *   capabilities?: string[],
 *   behavior?: (type: string, params: object) => object | Promise<object>,
 * }} options
 */
export async function startFakeWsAddon(options) {
  const gatewayPort = Number(options.gatewayPort)
  if (!gatewayPort) throw new Error('startFakeWsAddon requires gatewayPort')
  const behavior = options.behavior || defaultBehavior
  const instanceId =
    options.instanceId || 'fake-ws-' + String(process.pid) + '-' + String(Date.now())
  const ws = new WebSocket('ws://127.0.0.1:' + String(gatewayPort) + '/addon')

  let closed = false
  let readyResolve
  let readyReject
  const ready = new Promise((resolve, reject) => {
    readyResolve = resolve
    readyReject = reject
  })
  const timer = setInterval(() => {
    if (ws.readyState === WebSocket.OPEN) {
      try {
        ws.send(JSON.stringify({ type: 'heartbeat' }))
      } catch (e) {
        /* ignore */
      }
    }
  }, 1000)
  timer.unref?.()

  ws.addEventListener('open', () => {
    ws.send(
      JSON.stringify({
        type: 'handshake',
        instanceId,
        kind: options.kind || 'user-gui',
        version: options.version || '2.0.0',
        protocol: options.protocol ?? 12,
        project: options.project || '',
        capabilities: options.capabilities || ['viewport.capture', 'blender.ops'],
      }),
    )
  })
  ws.addEventListener('message', async (event) => {
    let msg
    try {
      msg = JSON.parse(String(event.data))
    } catch (e) {
      return
    }
    if (msg.type === 'handshake_ok') {
      readyResolve({ instanceId, epoch: msg.epoch })
      return
    }
    if (msg.type === 'handshake_error') {
      readyReject(new Error('handshake rejected: ' + JSON.stringify(msg)))
      return
    }
    if (msg.type === 'protocol_error') return
    if (msg.type === 'heartbeat' || msg.type === 'pong') return
    if (typeof msg.requestId !== 'string') return
    let out
    try {
      out = await behavior(String(msg.type || ''), msg.params || {})
    } catch (e) {
      out = { status: 'error', message: String((e && e.message) || e) }
    }
    if (out && out.action === 'close') {
      try {
        ws.close()
      } catch (e) {
        /* ignore */
      }
      return
    }
    if (out && out.action === 'hang') return
    try {
      ws.send(JSON.stringify({ ...out, requestId: msg.requestId }))
    } catch (e) {
      /* socket closed */
    }
  })
  ws.addEventListener('error', () => {
    if (!closed) readyReject(new Error('fake ws addon socket error'))
  })
  ws.addEventListener('close', () => {
    clearInterval(timer)
  })

  return {
    instanceId,
    ready,
    sendEvent(name, payload = {}) {
      if (ws.readyState !== WebSocket.OPEN) throw new Error('fake ws addon is not connected')
      ws.send(JSON.stringify({ type: 'event', name, ...payload }))
    },
    get closed() {
      return closed || ws.readyState === WebSocket.CLOSED
    },
    async close() {
      closed = true
      clearInterval(timer)
      try {
        ws.close()
      } catch (e) {
        /* ignore */
      }
      await new Promise((resolve) => {
        if (ws.readyState === WebSocket.CLOSED) resolve()
        else {
          ws.addEventListener('close', () => resolve(), { once: true })
          setTimeout(resolve, 1000)
        }
      })
    },
  }
}

export { DEFAULT_DELAY_MS }
