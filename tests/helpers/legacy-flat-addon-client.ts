// Test-only flat TCP client for the 3.0 protocol fixtures.
//
// 3.1 has exactly one production transport: the add-on dials the Gateway over
// WebSocket. This client keeps the flat-protocol contract tests honest without
// leaving a second transport path in runtime/.
import net from 'node:net'
import { resolveProtocol } from '../../runtime/addon-protocol.js'
import type { AddonProtocol, ProtocolDecode, ProtocolInfo } from '../../runtime/addon-protocol.js'
import { errorMessage } from '../../runtime/core/json.js'

interface PendingRequest {
  resolve: (v: unknown) => void
  reject: (e: Error) => void
  timer: NodeJS.Timeout
  post?: (data: unknown) => unknown
  proto: AddonProtocol
}
export class AddonClient {
  addr: { host: string; port: number }
  sock: net.Socket | null
  queue: Promise<unknown>
  buf: string
  pending: PendingRequest | null
  proto: AddonProtocol | null // 首次使用时解析（显式配置 → 直取；auto → 探测一次并缓存）
  protocolInfo: ProtocolInfo | null
  _seq: number
  constructor(addr: { host: string; port: number }) {
    this.addr = addr
    this.sock = null
    this.queue = Promise.resolve()
    this.buf = ''
    this.pending = null
    this.proto = null
    this.protocolInfo = null
    this._seq = 0
  }
  async protocol(): Promise<ProtocolInfo> {
    if (!this.proto) {
      const info = await resolveProtocol({
        host: this.addr.host,
        port: this.addr.port,
        prefer: 'flat',
      })
      this.protocolInfo = info
      this.proto = info.proto
      return info
    }
    return this.protocolInfo as ProtocolInfo
  }
  connect(timeoutMs = 4000): Promise<boolean> {
    return new Promise<boolean>((resolve, reject) => {
      const s = net.connect({ host: this.addr.host, port: this.addr.port })
      const t = setTimeout(() => {
        s.destroy()
        reject(new Error('connect timeout'))
      }, timeoutMs)
      s.once('connect', () => {
        clearTimeout(t)
        this.sock = s
        resolve(true)
      })
      s.once('error', (e) => {
        clearTimeout(t)
        this.sock = null
        reject(e)
      })
      s.on('data', (d: Buffer) => this._onData(d))
      s.on('close', () => {
        this.sock = null
        if (this.pending) {
          this.pending.reject(new Error('socket closed'))
          this.pending = null
        }
      })
    })
  }
  _onData(d: Buffer): void {
    this.buf += d.toString('utf8')
    if (!this.pending) return
    const proto = this.pending.proto
    let r: ProtocolDecode | null
    try {
      r = proto.decode(this.buf.trim())
    } catch (e) {
      return
    } // 未收全 → 继续攒
    this.buf = ''
    const p = this.pending
    this.pending = null
    clearTimeout(p.timer)
    if (!r.ok) {
      p.reject(new Error((r as { message?: string }).message))
      return
    }
    try {
      p.resolve(p.post ? p.post(r.data) : r.data)
    } catch (err) {
      p.reject(err instanceof Error ? err : new Error(errorMessage(err)))
    }
  }
  async ensure(): Promise<void> {
    if (!this.sock) await this.connect()
  }
  send(
    type: string,
    params: Record<string, unknown> = {},
    timeoutMs = 120000,
    _opts?: { noMark?: boolean },
  ): Promise<unknown> {
    const run = async () => {
      const info = await this.protocol()
      const proto = info.proto
      const plan = proto.plan(type, params) // 可能抛：未知命令 / 缺参数
      if (plan.local) return plan.local() // 本地桩（遥测 / 集成 status）不占 socket
      await this.ensure()
      return new Promise<unknown>((resolve, reject) => {
        const timer = setTimeout(() => {
          this.pending = null
          this.buf = ''
          reject(new Error('addon timeout: ' + type))
        }, timeoutMs)
        this.pending = {
          resolve: resolve,
          reject: reject,
          timer: timer,
          post: plan.post,
          proto: proto,
        }
        this.sock?.write(proto.encode(plan.wire))
      })
    }
    const p = this.queue.then(run, run)
    this.queue = p.catch(() => {})
    return p
  }
  close(): void {
    if (this.sock) {
      this.sock.end()
      this.sock = null
    }
  }
}
