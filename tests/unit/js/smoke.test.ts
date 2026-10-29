import { describe, expect, it } from 'vitest'

describe('unit test harness', () => {
  it('runs without Blender, backend or DSH host', () => {
    expect(1 + 1).toBe(2)
  })
})
