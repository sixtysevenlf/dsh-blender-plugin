/**
 * Read-only op parity: the host tool and the Gateway catalog must agree on which
 * ops run without a GUI session.
 *
 * src/tools/op/read-only.ts is a host-side copy of the Gateway whitelist so
 * blender_op can skip the session gate before talking to the Gateway.
 * runtime/catalog/data.ts is the source of truth: /plan reads are allowed for
 * PLAN_READ_ONLY_OPS ∪ PLAN_HOST_LOCAL_OPS, and this test fails when either side
 * drifts.
 */
import { describe, expect, it } from 'vitest'
import {
  PLAN_CATALOG,
  PLAN_HOST_LOCAL_OPS,
  PLAN_READ_ONLY_OPS,
} from '../../../../runtime/catalog/data.js'
import { isReadOnly } from '../../../../runtime/http/lease.js'
import { VISION_OPS } from '../../../../runtime/vision/dispatch.js'
import { READ_ONLY_OPS } from '../../../../src/tools/op/read-only.js'

describe('read-only op parity', () => {
  it('mirrors the catalog read-only whitelist union the host-local ops', () => {
    const expected = [...PLAN_READ_ONLY_OPS, ...PLAN_HOST_LOCAL_OPS].sort()
    expect([...READ_ONLY_OPS].sort()).toEqual(expected)
  })

  it('registers exactly the vision family as host-local', () => {
    expect([...PLAN_HOST_LOCAL_OPS].sort()).toEqual([...VISION_OPS].sort())
    expect(PLAN_HOST_LOCAL_OPS).toHaveLength(8)
    for (const op of VISION_OPS) expect(PLAN_READ_ONLY_OPS).not.toContain(op)
  })

  it('lets the Gateway /plan read path accept every host-local op', () => {
    for (const op of PLAN_HOST_LOCAL_OPS) expect(isReadOnly('/plan', op)).toBe(true)
    for (const op of VISION_OPS) expect(isReadOnly('/plan', op)).toBe(true)
    expect(isReadOnly('/plan', 'animation_report')).toBe(false)
  })

  it('declares the vision family as host-local with no write scope', () => {
    const family = PLAN_CATALOG.find((entry) => entry.f === 'vision')
    expect(family).toBeTruthy()
    expect(family?.requires_session).toBe(false)
    expect(family?.write_scope).toBe('none')
    expect(family?.prefix).toBe('vision_')
    const names = (family?.ops ?? []).map((op) => 'vision_' + op)
    expect(names.sort()).toEqual([...VISION_OPS].sort())
  })

  it('has no duplicate or stray vision entries in the host list', () => {
    expect(new Set(READ_ONLY_OPS).size).toBe(READ_ONLY_OPS.length)
    const visionNames = READ_ONLY_OPS.filter((name) => name.startsWith('vision_'))
    expect([...visionNames].sort()).toEqual([...VISION_OPS].sort())
  })
})
