import { describe, expect, it } from 'vitest'
import { createEngineMetrics, installAddonSend } from '../../../runtime/core/engine/lifecycle.js'
import type { AddonClient } from '../../../runtime/transport/probes.js'

interface SendCall {
  type: string
  params: Record<string, unknown> | undefined
  timeoutMs: number | undefined
  opts: { instanceId?: string; noMark?: boolean } | undefined
}

function fakeAddon(calls: SendCall[]): AddonClient {
  return {
    async ensure() {},
    async send(type, params, timeoutMs, opts) {
      calls.push({ type, params, timeoutMs, opts })
      return { ok: true }
    },
    close() {},
  }
}

const ready = () => ({ present: false, stale: true, ageMs: null, info: null })

describe('installAddonSend', () => {
  it('forwards the per-instance routing options to the underlying client', async () => {
    const calls: SendCall[] = []
    const addon = fakeAddon(calls)
    installAddonSend(addon, createEngineMetrics(), ready)

    await addon.send('execute_code', { code: 'print(1)' }, 1234, { instanceId: 'vgui-abc' })

    expect(calls).toHaveLength(1)
    expect(calls[0].type).toBe('execute_code')
    expect(calls[0].params).toEqual({ code: 'print(1)' })
    expect(calls[0].timeoutMs).toBe(1234)
    expect(calls[0].opts).toEqual({ instanceId: 'vgui-abc' })
  })
})
