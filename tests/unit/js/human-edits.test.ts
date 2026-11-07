import { describe, expect, it } from 'vitest'
import { SessionRegistry } from '../../../runtime/core/sessions/registry.js'

const caller = { hostId: 'h', dshSessionId: 's', agentId: 'main', role: 'main' as const }

function attached() {
  const registry = new SessionRegistry(() => [{ instanceId: 'inst-1', ready: true }])
  const result = registry.attach(caller)
  if (!result.ok) throw new Error('attach failed')
  return registry
}

describe('human edit conflicts', () => {
  it('blocks an object write the human changed and explains the scope', () => {
    const registry = attached()
    expect(
      registry.applyHumanEdits('inst-1', [{ kind: 'object', name: 'Cube', revision: 3 }]),
    ).toBe(1)
    const write = registry.authorizeWrite(caller, undefined, 'object:Cube')
    expect(write.ok).toBe(false)
    if (write.ok) return
    expect(write.code).toBe('human_modified')
    expect(write.stage).toBe('session')
    expect(write.evidence.changed).toEqual([{ kind: 'object', name: 'Cube', revision: 3 }])
    expect(write.fix).toContain('mark_read')
  })

  it('leaves unrelated scopes writable', () => {
    const registry = attached()
    registry.applyHumanEdits('inst-1', [{ kind: 'object', name: 'Cube' }])
    expect(registry.authorizeWrite(caller, undefined, 'object:Sphere').ok).toBe(true)
    expect(registry.authorizeWrite(caller, undefined, 'prefix:Sphere_').ok).toBe(true)
  })

  it('blocks exclusive writes and collection edits', () => {
    const registry = attached()
    registry.applyHumanEdits('inst-1', [
      { kind: 'object', name: 'Cube' },
      { kind: 'collection', name: 'COL_Lights' },
    ])
    expect(registry.authorizeWrite(caller, undefined, 'exclusive').ok).toBe(false)
    const collection = registry.authorizeWrite(caller, undefined, 'collection:COL_Lights')
    expect(collection.ok).toBe(false)
    if (collection.ok) return
    expect(collection.code).toBe('human_modified')
  })

  it('matches object prefixes and clears only after mark_read', () => {
    const registry = attached()
    registry.applyHumanEdits('inst-1', [
      { kind: 'object', name: 'LGT-Key' },
      { kind: 'object', name: 'Cube' },
    ])
    expect(registry.authorizeWrite(caller, undefined, 'prefix:LGT-').ok).toBe(false)
    const read = registry.markRead(caller, undefined, 'prefix:LGT-')
    expect(read).toMatchObject({ ok: true, cleared: 1 })
    expect(registry.authorizeWrite(caller, undefined, 'prefix:LGT-').ok).toBe(true)
    expect(registry.authorizeWrite(caller, undefined, 'object:Cube').ok).toBe(false)
  })

  it('re-blocks when the human edits again after mark_read', () => {
    const registry = attached()
    registry.applyHumanEdits('inst-1', [{ kind: 'object', name: 'Cube', revision: 1 }])
    expect(registry.markRead(caller, undefined, 'object:Cube').ok).toBe(true)
    expect(registry.authorizeWrite(caller, undefined, 'object:Cube').ok).toBe(true)
    registry.applyHumanEdits('inst-1', [{ kind: 'object', name: 'Cube', revision: 2 }])
    expect(registry.authorizeWrite(caller, undefined, 'object:Cube').ok).toBe(false)
  })

  it('exposes a redacted humanModified view without scene data', () => {
    const registry = attached()
    registry.applyHumanEdits('inst-1', [{ kind: 'object', name: 'Cube', revision: 4 }])
    const view = registry.list()[0] as { humanModified: Record<string, unknown> }
    expect(view.humanModified.objects).toEqual([{ name: 'Cube', revision: 4 }])
    expect(JSON.stringify(view)).not.toContain('scene')
  })
})
