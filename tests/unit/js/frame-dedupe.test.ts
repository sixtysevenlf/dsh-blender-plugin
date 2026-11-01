import { beforeEach, describe, expect, it } from 'vitest'
import * as plugin from '../../../lib/index.js'

// P0-2 frame dedupe: only same key + same hash counts as a duplicate; force
// always releases; channels never suppress each other; empty hash never dedupes
// (better one extra frame than a silently dropped image).

const { frameDedupe, resetFrameCache } = plugin as any

describe('frame dedupe', () => {
  beforeEach(() => resetFrameCache())

  it('dedupes only identical key + hash and counts repeats', () => {
    expect(frameDedupe('viewport:560', 'h1')).toMatchObject({ dup: false, repeats: 0 })
    expect(frameDedupe('viewport:560', 'h1')).toMatchObject({ dup: true, repeats: 1 })
    expect(frameDedupe('viewport:560', 'h1').repeats).toBe(2)
  })

  it('lets force through and resets the counter', () => {
    frameDedupe('viewport:560', 'h1')
    expect(frameDedupe('viewport:560', 'h1', true)).toMatchObject({ dup: false, repeats: 0 })
    expect(frameDedupe('viewport:560', 'h1').repeats).toBe(1)
  })

  it('keeps channels independent', () => {
    frameDedupe('viewport:560', 'h1')
    expect(frameDedupe('do-see:560', 'h1').dup).toBe(false)
    expect(frameDedupe('view:[996,560,[7,-7,5],[0,0,1]]', 'h1').dup).toBe(false)
    expect(frameDedupe('area:0', 'h1').dup).toBe(false)
    expect(frameDedupe('viewport:560', 'h2').dup).toBe(false)
  })

  it('never dedupes an empty hash', () => {
    expect(frameDedupe('viewport:420', '').dup).toBe(false)
    expect(frameDedupe('viewport:420', '').dup).toBe(false)
  })

  it('keeps working with many keys', () => {
    for (let i = 0; i < 60; i += 1) frameDedupe('k' + i, 'same')
    expect(frameDedupe('k59', 'same').dup).toBe(true)
  })
})
