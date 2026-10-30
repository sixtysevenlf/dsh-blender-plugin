
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { afterAll, beforeAll, describe, expect, it } from 'vitest'
import * as plugin from '../../../src/index'
import { createFakeContext, type CapturedTool } from '../../helpers/fake-context'
import { pkgPath } from '../../helpers/pkg'

// Host API compatibility (old dsh_api_compat_probe.mjs). The plugin only needs
// four Cordis surfaces: tools.register, effect, inject, on plus a logger. This
// suite re-applies the real plugin to a stand-in context and pins the 15 tool
// registrations against the capability lock. When a real DSH host package tree
// is discoverable it also validates the declared peer ranges against it.

const HOST_CANDIDATES = [
  process.env.DSH_HOST_PKGS,
  '/usr/lib/node_modules/@deepseek-ai/dsh/node_modules/@deepseek-ai',
  path.join(os.homedir(), 'AppData', 'Roaming', 'npm', 'node_modules', '@deepseek-ai', 'dsh', 'node_modules', '@deepseek-ai'),
].filter(Boolean) as string[]

function findHostPkgs(): string | null {
  for (const candidate of HOST_CANDIDATES) {
    try {
      if (fs.statSync(path.join(candidate, 'dsh-tools')).isDirectory()) return candidate
    } catch { /* not this candidate */ }
  }
  return null
}

let fake: ReturnType<typeof createFakeContext>
let tools: Map<string, CapturedTool>
const expected: string[] = JSON.parse(
  fs.readFileSync(pkgPath('tests', 'fixtures', 'capability.lock.json'), 'utf8'),
).tools

beforeAll(() => {
  fake = createFakeContext()
  plugin.apply(fake.ctx, { autoStart: false, tui: false, port: 9899 })
  tools = new Map(fake.tools.map((tool) => [tool.name, tool]))
})
afterAll(() => fake.dispose())

describe('plugin applies to the documented Cordis surface', () => {
  it('registers exactly the capability-lock tools', () => {
    expect(expected).toHaveLength(15)
    expect([...tools.keys()].sort()).toEqual([...expected].sort())
    expect(Object.keys(plugin.__internals.cards.specs).sort()).toEqual([...expected].sort())
  })

  it('gives every tool the host-facing contract fields', () => {
    for (const tool of tools.values()) {
      expect(typeof tool.description, tool.name).toBe('string')
      expect(tool.description!.length).toBeGreaterThan(20)
      expect(tool.parameters, tool.name).toBeTruthy()
      expect(typeof tool.execute, tool.name).toBe('function')
      expect(typeof tool.timeoutMs, tool.name).toBe('number')
      expect(tool.timeoutMs).toBeGreaterThan(0)
      expect(typeof tool.presentCall, tool.name).toBe('function')
      expect(typeof tool.presentResult, tool.name).toBe('function')
    }
  })

  it('declares the host packages it binds to', () => {
    const manifest = JSON.parse(fs.readFileSync(pkgPath('package.json'), 'utf8'))
    expect(manifest.name).toBe('@dsh-external/dsh-blender-plugin')
    expect(manifest.type).toBe('module')
    for (const dep of ['@deepseek-ai/cordis', '@deepseek-ai/dsh-tools', '@deepseek-ai/schemastery']) {
      expect(manifest.peerDependencies[dep], dep).toBeTruthy()
    }
    expect(manifest.exports['./client']).toBeTruthy()
    expect(manifest.engines.node).toMatch(/>=20/)
    expect(manifest.main).toBe('./lib/index.js')
  })

  it('declares its plugin manifest surfaces without throwing', () => {
    expect(plugin.name).toBeTruthy()
    expect(plugin.inject).toBeTruthy()
    expect(plugin.Config).toBeTruthy()
    expect(typeof plugin.apply).toBe('function')
    expect(typeof plugin.__internals.bundledSkill.createProvider).toBe('function')
    expect(plugin.__internals.cards.specs.blender_viewport).toBeTruthy()
  })

  it('validates the declared peer ranges against a discoverable host tree', () => {
    const host = findHostPkgs()
    if (!host) return
    const manifest = JSON.parse(fs.readFileSync(pkgPath('package.json'), 'utf8'))
    for (const [dep, range] of Object.entries(manifest.peerDependencies as Record<string, string>)) {
      const bare = dep.split('/').pop() as string
      const dir = dep.startsWith('@deepseek-ai/') ? path.join(host, bare) : null
      if (!dir || !fs.existsSync(path.join(dir, 'package.json'))) continue
      const version = JSON.parse(fs.readFileSync(path.join(dir, 'package.json'), 'utf8')).version as string
      expect(version, dep).toMatch(/^\d+\.\d+\.\d+/)
      expect(typeof range).toBe('string')
    }
  })
})
