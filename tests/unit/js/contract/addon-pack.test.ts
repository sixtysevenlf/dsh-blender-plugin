import { describe, expect, it } from 'vitest'
import fs from 'node:fs'
import { repoPath } from '../../../helpers/docs'

const read = (relative: string): string => fs.readFileSync(repoPath(relative), 'utf8')

describe('F4.5 final-state addon pack invariants', () => {
  it('keeps the single command registry counts in source and package metadata', () => {
    const registry = read('addon/dsh_blender_bridge/command_registry.py')
    expect(registry).toContain('REGISTERED_COMMAND_COUNT = 38')
    expect(registry).toContain('WIRE_COMMAND_COUNT = 39')
    const pkg = JSON.parse(read('package.json')) as { version: string; engines?: { node?: string } }
    expect(pkg.version).toBe('3.0.2')
    expect(pkg.engines?.node).toBe('>=24.9.0')
    const init = read('addon/dsh_blender_bridge/__init__.py')
    expect(init).toMatch(/"version": \(1, 11\)/)
    expect(read('addon/dsh_blender_bridge/protocol.py')).toContain('ADDON_PROTOCOL_VERSION = 11')
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

  it('keeps the function-budget ledger empty at the final state', () => {
    const allow = JSON.parse(read('config/function-budget-allowlist.json')) as {
      functions: unknown[]
      files: unknown[]
    }
    expect(allow.functions).toEqual([])
    expect(allow.files).toEqual([])
  })
})
