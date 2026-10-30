import { describe, expect, it } from 'vitest'
import fs from 'node:fs'
import vm from 'node:vm'
import * as assert from 'node:assert/strict'
import { pkgPath } from '../../../helpers/pkg'

// Regression for optional Cordis services. v2.3 removed the DSH-side viewport
// panel and the sidebar pane; the client bundle now only registers the selection
// dock and the "@" mention source. Both are optional: the plugin must load when
// inputTriggers is absent, register the dock unconditionally (slots is its hard
// dependency), and register the mention source once the service appears.

const BUNDLE = pkgPath('lib', 'client.js')

function loadModule() {
  let entry: any
  const window = { __ModuleLoader__: { load(value: any) { entry = value } } }
  const sandbox = vm.createContext({ window, console: { warn() {}, info() {} } })
  vm.runInContext(fs.readFileSync(BUNDLE, 'utf8'), sandbox, { timeout: 2000 })
  return entry.factory((name: string) => {
    assert.equal(name, 'react')
    return {}
  })
}

function makeHost(withTriggers: boolean) {
  const docks: any[] = []
  const sources: any[] = []
  const effects: Array<{ callback: () => any; name?: string }> = []
  const triggers = { registerSource(source: any) { sources.push(source); return () => {} } }
  const ctx: any = {
    get: () => undefined,
    effect(callback: () => any, name?: string) { effects.push({ callback, name }); return () => {} },
    inject(names: string[], callback: (scope: any) => void) {
      assert.equal(names.length, 1)
      assert.equal(names[0], 'inputTriggers')
      if (!withTriggers) return
      callback({
        get: (name: string) => (name === 'inputTriggers' ? triggers : undefined),
        effect(callback: () => any) { return callback() },
      })
    },
    slots: {
      inject(name: string, callback: () => any) { assert.equal(name, 'conversation.input.dock'); return callback() },
      register(options: any, component: any) { docks.push({ options, component }); return () => {} },
    },
  }
  return { ctx, docks, sources, effects }
}

describe('optional client services', () => {
  it('declares slots as the only hard dependency and keeps no sidebar pane', () => {
    const mod = loadModule()
    expect(mod.inject).toEqual(['slots'])
    const source = fs.readFileSync(BUNDLE, 'utf8')
    expect(source).toContain('conversation.input.dock')
    expect(source).toContain('blender-selection-bridge')
    expect(source).not.toContain('conversation.view')
    expect(source).not.toContain('sidebar.right.pane.tab')
  })

  it('registers the selection dock without the inputTriggers service', () => {
    const mod = loadModule()
    const host = makeHost(false)
    expect(() => mod.apply(host.ctx)).not.toThrow()
    expect(host.docks).toHaveLength(1)
    expect(host.docks[0].options).toMatchObject({ name: 'conversation.input.dock', id: 'blender-selection-bridge', order: 90 })
    expect(host.docks[0].options.inject).toBeTypeOf('function')
    expect(host.docks[0].component).toBeTruthy()
    expect(host.sources).toEqual([])
  })

  it('registers the @ mention source when inputTriggers arrives', () => {
    const mod = loadModule()
    const host = makeHost(true)
    mod.apply(host.ctx)
    expect(host.sources).toHaveLength(1)
    expect(host.sources[0]).toMatchObject({ trigger: '@', name: 'blender' })
    expect(host.sources[0].candidates).toBeTypeOf('function')
    expect(host.sources[0].onPick).toBeTypeOf('function')
    expect(host.sources[0].codec).toBeTruthy()
  })

  it('applies once and resets through its dispose effect', () => {
    const mod = loadModule()
    const host = makeHost(false)
    mod.apply(host.ctx)
    mod.apply(host.ctx)
    expect(host.docks).toHaveLength(1)
    const reset = host.effects.find((effect) => String(effect.name).includes('reset apply guard'))
    expect(reset).toBeTruthy()
    reset!.callback()()
    mod.apply(host.ctx)
    expect(host.docks).toHaveLength(2)
  })
})
