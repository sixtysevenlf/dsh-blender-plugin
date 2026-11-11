import { describe, expect, it } from 'vitest'
import { createHash } from 'node:crypto'
import fs from 'node:fs'
import { buildExpectedZip } from '../../../../scripts/build-extension.mjs'
import { repoPath } from '../../../helpers/docs'

const sha256 = (buffer: Buffer): string => createHash('sha256').update(buffer).digest('hex')

describe('extension archive freshness', () => {
  it('rebuilds the committed zip byte for byte from the current extension sources', () => {
    const committed = fs.readFileSync(repoPath('extension', 'dsh_blender_bridge.zip'))
    const expected = buildExpectedZip() as Buffer
    expect(expected.readUInt32LE(0)).toBe(0x04034b50)
    expect(expected.length).toBeGreaterThan(100000)
    expect(sha256(expected)).toBe(sha256(committed))
  })
})
