import { afterEach, describe, expect, it } from 'vitest'
import {
  publishPresence,
  setPresencePublisher,
} from '../../../runtime/core/sessions/presence-publish.js'
import type { PresencePush } from '../../../runtime/core/sessions/presence-publish.js'
import { presenceCode } from '../../../runtime/gateway/presence-push.js'
import type { SessionRecord } from '../../../runtime/core/sessions/types.js'

afterEach(() => setPresencePublisher(null))

const session = (over: Record<string, unknown> = {}): SessionRecord =>
  ({ kind: 'user-gui', instanceId: 'inst-1', paused: false, ...over }) as unknown as SessionRecord

describe('3.1 presence delivery', () => {
  it('embeds the snapshot in add-on code that resolves the extension package', () => {
    const push: PresencePush = {
      instanceId: 'inst-1',
      paused: true,
      agents: [{ displayName: 'agent-main', view: { viewpoint: 'viewport' } }],
    }
    const code = presenceCode(push)
    expect(code).toContain('presence_draw')
    expect(code).toContain('set_presence')
    expect(code).toContain('import sys, importlib, json')
    expect(code).toContain(JSON.stringify(JSON.stringify(push)))
  })

  it('publishes user-gui snapshots and ignores headless sessions', () => {
    const pushes: PresencePush[] = []
    setPresencePublisher((push) => pushes.push(push))
    publishPresence(session({ paused: true }), [{ displayName: 'agent-main' }])
    publishPresence(session({ kind: 'headless' }), [{ displayName: 'agent-main' }])
    expect(pushes).toEqual([
      { instanceId: 'inst-1', agents: [{ displayName: 'agent-main' }], paused: true },
    ])
  })

  it('does not publish before the Gateway installs a transport', () => {
    expect(() => publishPresence(session(), [])).not.toThrow()
  })
})
