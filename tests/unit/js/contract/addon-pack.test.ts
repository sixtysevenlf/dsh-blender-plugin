import { describe, expect, it } from 'vitest'
import fs from 'node:fs'
import { repoPath } from '../../../helpers/docs'

const read = (relative: string): string => fs.readFileSync(repoPath(relative), 'utf8')

describe('final-state addon pack invariants', () => {
  it('keeps the single command registry counts in source and package metadata', () => {
    const registry = read('addon/dsh_blender_bridge/command_registry.py')
    expect(registry).toContain('REGISTERED_COMMAND_COUNT = 38')
    expect(registry).toContain('WIRE_COMMAND_COUNT = 39')
    const pkg = JSON.parse(read('package.json')) as { version: string; engines?: { node?: string } }
    expect(pkg.version).toBe('3.0.6')
    expect(pkg.engines?.node).toBe('>=24.9.0')
    const init = read('addon/dsh_blender_bridge/__init__.py')
    expect(init).toMatch(/"version": \(2, 0, 0\)/)
    expect(read('addon/dsh_blender_bridge/gateway_schema.py')).toContain(
      'ADDON_PROTOCOL_VERSION = 12',
    )
  })

  it('ships the zip package and no legacy single-file addon entry', () => {
    const zip = repoPath('addon', 'dsh_blender_bridge.zip')
    expect(fs.existsSync(zip)).toBe(true)
    expect(fs.statSync(zip).size).toBeGreaterThan(1000)
    expect(fs.existsSync(repoPath('addon', 'dsh_blender_bridge.py'))).toBe(false)
  })

  it('has no runtime flat shims and no retired host entry files', () => {
    const flat = fs.readdirSync(repoPath('runtime')).filter((f) => f.endsWith('.py'))
    expect(flat).toEqual([])
    expect(fs.existsSync(repoPath('src', 'tui-host.ts'))).toBe(false)
    expect(fs.existsSync(repoPath('runtime', 'execution', 'headless.ts'))).toBe(false)
  })

  it('registers every soft-band function in the function-budget ledger', () => {
    const allow = JSON.parse(read('config/function-budget-allowlist.json')) as {
      version: number
      description: string
      functions: {
        file: string
        name: string
        classification: string
        reason: string
        owner: string
        reviewBy: string
      }[]
      files?: unknown
    }
    expect(allow.version).toBe(1)
    expect(allow.description).toContain('config/exceptions.json')
    expect(allow.files).toBeUndefined()
    expect(allow.functions.length).toBeGreaterThan(0)
    const keys = new Set<string>()
    for (const row of allow.functions) {
      expect(row.file.length).toBeGreaterThan(0)
      expect(row.name.length).toBeGreaterThan(0)
      expect(row.classification).not.toBe('unclassified')
      expect(row.owner.length).toBeGreaterThan(0)
      expect(row.reason.length).toBeGreaterThanOrEqual(16)
      expect(row.reviewBy).toMatch(/^\d{4}-\d{2}-\d{2}$/)
      expect(row.reviewBy >= '2027-01-01').toBe(true)
      keys.add(row.file + '::' + row.name)
    }
    expect(keys.size).toBe(allow.functions.length)
  })
})
