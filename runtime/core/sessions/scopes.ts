// Scope declarations for 3.1 GUI sessions.
//
// A write declares what it mutates: the whole scene (exclusive), a Blender
// collection, an object-name prefix, or one stable object identity. Grants are
// checked against those declarations; readers never need one.

export type ScopeKind = 'exclusive' | 'collection' | 'prefix' | 'object'

export interface ScopeSpec {
  kind: ScopeKind
  value?: string
}

const KINDS: ScopeKind[] = ['exclusive', 'collection', 'prefix', 'object']

/** Parse one scope token ("exclusive", "collection:COL_Lights", "prefix:LGT-", "object:<uid>"). */
export function parseScope(value: unknown): ScopeSpec | null {
  if (value && typeof value === 'object' && !Array.isArray(value)) {
    const rec = value as Record<string, unknown>
    const kind = String(rec.kind || '')
    if ((KINDS as string[]).includes(kind)) {
      const text = rec.value === undefined || rec.value === null ? '' : String(rec.value).trim()
      if (kind === 'exclusive') return { kind: 'exclusive' }
      if (!text) return null
      return { kind: kind as ScopeKind, value: text }
    }
    return null
  }
  const raw = String(value === undefined || value === null ? '' : value).trim()
  if (!raw) return null
  const at = raw.indexOf(':')
  if (at > 0) {
    const kind = raw.slice(0, at)
    const rest = raw.slice(at + 1).trim()
    if (kind === 'exclusive') return { kind: 'exclusive' }
    if ((KINDS as string[]).includes(kind) && rest) return { kind: kind as ScopeKind, value: rest }
    return null
  }
  if (raw === 'exclusive') return { kind: 'exclusive' }
  // A bare name is treated as a collection, matching the legacy lease spelling.
  return { kind: 'collection', value: raw }
}

export function parseScopes(value: unknown): ScopeSpec[] {
  const list = Array.isArray(value) ? value : [value]
  const out: ScopeSpec[] = []
  for (const item of list) {
    const parsed = parseScope(item)
    if (parsed) out.push(parsed)
  }
  return out
}

/** True when two scope declarations may touch the same scene state. */
export function scopesOverlap(left: ScopeSpec, right: ScopeSpec): boolean {
  if (left.kind === 'exclusive' || right.kind === 'exclusive') return true
  if (left.kind !== right.kind) {
    // collection/object/prefix target different identity spaces; only an exact
    // textual match is treated as overlap across kinds.
    return !!left.value && left.value === right.value
  }
  const a = String(left.value || '')
  const b = String(right.value || '')
  if (left.kind === 'prefix') return a.startsWith(b) || b.startsWith(a)
  return a === b
}

export function anyOverlap(left: ScopeSpec[], right: ScopeSpec[]): boolean {
  for (const a of left) for (const b of right) if (scopesOverlap(a, b)) return true
  return false
}

/** True when a grant scope covers a write scope (an exclusive grant covers everything). */
export function scopeCovers(grant: ScopeSpec, write: ScopeSpec): boolean {
  if (grant.kind === 'exclusive') return true
  if (write.kind === 'exclusive') return false
  if (grant.kind !== write.kind) return false
  const a = String(grant.value || '')
  const b = String(write.value || '')
  if (grant.kind === 'prefix') return b.startsWith(a)
  return a === b
}

export function scopesCover(granted: ScopeSpec[], writes: ScopeSpec[]): boolean {
  return writes.every((w) => granted.some((g) => scopeCovers(g, w)))
}
