import { beforeEach, describe, expect, it } from 'vitest'
import {
  configureVirtualGuiPermission,
  handleVirtualGuiPermission,
  registerVirtualGuiPermissionRpc,
  setVirtualGuiAllowed,
  virtualGuiAllowed,
  virtualGuiPermissionView,
} from '../../../src/host/virtual-gui-permission.js'

const OWNER = { principal: { kind: 'owner' } }
const BROWSER_GUEST = { principal: { kind: 'guest' } }

beforeEach(() => {
  configureVirtualGuiPermission(false)
})

describe('host virtual-gui permission', () => {
  it('defaults to denied and records the config source', () => {
    expect(virtualGuiAllowed()).toBe(false)
    expect(virtualGuiPermissionView()).toEqual({
      allowed: false,
      source: 'config',
      updatedAt: null,
    })
  })

  it('lets the human owner flip the switch at runtime', async () => {
    const out = await handleVirtualGuiPermission(
      'permission',
      { op: 'set', allow: true },
      null,
      OWNER,
    )
    expect(out.ok).toBe(true)
    if (!out.ok) return
    expect(out.value.allowed).toBe(true)
    expect(out.value.source).toBe('runtime')
    expect(virtualGuiAllowed()).toBe(true)

    const status = await handleVirtualGuiPermission('permission', { op: 'status' }, null, OWNER)
    expect(status.ok).toBe(true)
    if (!status.ok) return
    expect(status.value.allowed).toBe(true)
  })

  it('rejects a non-owner browser peer', async () => {
    const out = await handleVirtualGuiPermission(
      'permission',
      { op: 'set', allow: true },
      null,
      BROWSER_GUEST,
    )
    expect(out.ok).toBe(false)
    if (out.ok) return
    expect(out.error.code).toBe('forbidden')
    expect(virtualGuiAllowed()).toBe(false)
  })

  it('resets to the configured default on plugin reload', () => {
    setVirtualGuiAllowed(true)
    configureVirtualGuiPermission(false)
    expect(virtualGuiAllowed()).toBe(false)
    expect(virtualGuiPermissionView().source).toBe('config')
  })

  it('registers the permission endpoint on the host connection RPC', async () => {
    const registered: { channel?: string; handler?: unknown } = {}
    const ctx = {
      inject(names: string[], callback: (scope: unknown) => void) {
        expect(names).toEqual(['connection'])
        callback({
          get: () => ({
            rpc: {
              handle(channel: string, handler: unknown) {
                registered.channel = channel
                registered.handler = handler
              },
            },
          }),
        })
      },
      logger: { warn() {} },
    }
    registerVirtualGuiPermissionRpc(ctx as never, 0)
    expect(registered.channel).toBe('/dsh-blender')
    expect(registered.handler).toBeTypeOf('function')
    const handler = registered.handler as (
      endpoint: string,
      payload: unknown,
      signal: unknown,
      peer: unknown,
    ) => Promise<{ ok: boolean; value?: { allowed: boolean }; error?: { code: string } }>
    const out = await handler('permission', { op: 'status' }, null, OWNER)
    expect(out.ok).toBe(true)
    expect(out.value).toEqual({ allowed: false, source: 'config', updatedAt: null })
    const panel = await handler('virtual-gui', { op: 'status' }, null, OWNER)
    expect(panel.ok).toBe(false)
    expect(panel.error?.code).toBe('backend_unavailable')
  })

  it('validates the endpoint, op and allow type', async () => {
    const wrongEndpoint = await handleVirtualGuiPermission('other', {}, null, OWNER)
    expect(wrongEndpoint.ok ? '' : wrongEndpoint.error.code).toBe('not_found')
    const wrongOp = await handleVirtualGuiPermission('permission', { op: 'toggle' }, null, OWNER)
    expect(wrongOp.ok ? '' : wrongOp.error.code).toBe('invalid_params')
    const wrongType = await handleVirtualGuiPermission(
      'permission',
      { op: 'set', allow: 'yes' },
      null,
      OWNER,
    )
    expect(wrongType.ok ? '' : wrongType.error.code).toBe('invalid_params')
  })
})
