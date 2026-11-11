import { afterEach, describe, expect, it } from 'vitest'
import {
  publishPresence,
  setPresencePublisher,
} from '../../../runtime/core/sessions/presence-publish.js'
import { installPresencePublisher } from '../../../runtime/gateway/presence-push.js'
import type { SessionRecord } from '../../../runtime/core/sessions/types.js'

afterEach(() => setPresencePublisher(null))

const session = (over: Record<string, unknown> = {}): SessionRecord =>
  ({ kind: 'user-gui', instanceId: 'inst-1', paused: false, ...over }) as unknown as SessionRecord

interface SendCall {
  type: string
  params: Record<string, unknown>
  timeoutMs: number | undefined
  opts: unknown
}

function installFakeClient(impl?: (call: SendCall) => Promise<unknown>): SendCall[] {
  const calls: SendCall[] = []
  installPresencePublisher({
    async send(type: string, params: unknown, timeoutMs?: number, opts?: unknown) {
      const call = { type, params: params as Record<string, unknown>, timeoutMs, opts }
      calls.push(call)
      return impl ? impl(call) : null
    },
  } as never)
  return calls
}

describe('3.1 presence delivery', () => {
  it('delivers the snapshot through the pause-safe internal command', () => {
    const calls = installFakeClient()

    publishPresence(session({ paused: true }), [
      { displayName: 'agent-main', view: { viewpoint: 'viewport' } },
    ])

    expect(calls).toHaveLength(1)
    expect(calls[0].type).toBe('get_viewport_screenshot')
    expect(calls[0].timeoutMs).toBe(3000)
    expect(calls[0].opts).toEqual({ instanceId: 'inst-1' })
    expect(JSON.parse(String(calls[0].params.presence))).toEqual({
      instanceId: 'inst-1',
      paused: true,
      agents: [{ displayName: 'agent-main', view: { viewpoint: 'viewport' } }],
    })
  })

  it('forwards a transient notice when the snapshot has one', () => {
    const calls = installFakeClient()

    publishPresence(
      session({ notice: { seq: 7, text: 'SEE full · 整窗口截图，未记录观察位姿', at: 1 } }),
      [],
    )

    expect(JSON.parse(String(calls[0].params.presence))).toMatchObject({
      notice: { seq: 7, text: 'SEE full · 整窗口截图，未记录观察位姿' },
    })
  })

  it('publishes user-gui snapshots and ignores headless sessions', () => {
    const calls = installFakeClient()

    publishPresence(session({ paused: true }), [{ displayName: 'agent-main' }])
    publishPresence(session({ kind: 'headless' }), [{ displayName: 'agent-main' }])

    expect(calls).toHaveLength(1)
    expect(JSON.parse(String(calls[0].params.presence))).toMatchObject({
      instanceId: 'inst-1',
      paused: true,
    })
  })

  it('does not publish before the Gateway installs a transport', () => {
    expect(() => publishPresence(session(), [])).not.toThrow()
  })

  it('swallows add-on delivery failures instead of failing the session', async () => {
    installFakeClient(async () => {
      throw new Error('AI 操作已暂停')
    })
    publishPresence(session(), [])
    await Promise.resolve()
  })
})
