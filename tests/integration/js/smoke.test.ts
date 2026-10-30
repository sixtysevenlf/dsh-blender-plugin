import { describe, expect, it } from 'vitest'
import { wslPathShared } from '../../../runtime/config.js'

describe('integration test harness', () => {
  it('can import a real runtime module without starting Blender', () => {
    const result = wslPathShared(process.cwd())
    expect(result).toHaveProperty('shared')
    expect(typeof result.shared).toBe('boolean')
  })
})
