import { afterAll, beforeAll, describe, expect, it } from 'vitest'
import { startBackend, type Backend } from '../../helpers/backend'

// v2.6 feedback #7: "who writes where" is a second dimension next to "who holds
// the lease". A single collection-only allow-list forced lights into an asset
// collection and an assembler had to move them by hand. The lease must carry a
// normalised two-dimension scope and expose it in /who and the 409 receipt.

let backend: Backend

beforeAll(async () => { backend = await startBackend({ env: { DSH_BLENDER_HOLDER: 'scope-it' } }) })
afterAll(async () => { await backend.stop() })

describe('write lease scope (two dimensions)', () => {
  it('stores a null scope when the caller does not declare one', async () => {
    const response = await backend.post('/lease', { holder: 'scope-A', ttlMs: 60000 })
    expect(response.json.ok).toBe(true)
    expect(response.json.lease.scope).toBeNull()
  })

  it('normalises collection: and prefix: declarations', async () => {
    const response = await backend.post('/lease', { holder: 'scope-A', ttlMs: 60000, scope: 'collection:COL_Lights, prefix:LGT-' })
    const scope = (response.json.lease && response.json.lease.scope) || []
    expect(scope).toHaveLength(2)
    expect(scope[0]).toMatchObject({ kind: 'collection', value: 'COL_Lights' })
    expect(scope[1]).toMatchObject({ kind: 'prefix', value: 'LGT-' })
  })

  it('exposes the scope in /who', async () => {
    const who = await backend.get('/who')
    expect(who.json.lease.scope.length).toBe(2)
  })

  it('blocks another holder with 409 and echoes holderScope', async () => {
    const response = await backend.post('/lease', { holder: 'scope-B' })
    expect(response.status).toBe(409)
    expect(response.json.error).toBe('leased')
    expect(Array.isArray(response.json.holderScope)).toBe(true)
  })

  it('infers collection / prefix from bare tokens on a forced takeover', async () => {
    const response = await backend.post('/lease', { holder: 'scope-B', force: true, scope: 'COL_VFX,GEO-GZ-*' })
    const scope = (response.json.lease && response.json.lease.scope) || []
    expect(scope[0]).toMatchObject({ kind: 'collection', value: 'COL_VFX' })
    expect(scope[1]).toMatchObject({ kind: 'prefix', value: 'GEO-GZ-' })
    await backend.post('/release', { holder: 'scope-B' })
  })
})
