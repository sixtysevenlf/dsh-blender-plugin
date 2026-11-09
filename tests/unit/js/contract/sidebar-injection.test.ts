import { describe, expect, it } from 'vitest'
import fs from 'node:fs'
import vm from 'node:vm'
import * as assert from 'node:assert/strict'
import { pkgPath } from '../../../helpers/pkg'

// Regression for optional Cordis services. The client half registers the
// selection dock, the agent-presence right-sidebar tab and the "@" mention source.
// `slots`, `sidebarRight` and `sidebarRightTabs` are hard dependencies;
// inputTriggers and commandUi stay optional.

const BUNDLE = pkgPath('lib', 'client.js')

function loadModule() {
  let entry: any
  const window = {
    __ModuleLoader__: {
      load(value: any) {
        entry = value
      },
    },
  }
  const sandbox = vm.createContext({ window, console: { warn() {}, info() {} } })
  vm.runInContext(fs.readFileSync(BUNDLE, 'utf8'), sandbox, { timeout: 2000 })
  return entry.factory((name: string) => {
    assert.equal(name, 'react')
    return {}
  })
}

function makeHost(withTriggers: boolean, withCommandUi = false) {
  const panels: Array<{ slot: string; options: any; component: any }> = []
  const sources: any[] = []
  const effects: Array<{ callback: () => any; name?: string }> = []
  const commands: any[] = []
  const selectedPanels: string[] = []
  const tabDefs: any[] = []
  const openedTabs: Array<{ kind: string; options?: any }> = []
  const commandUi = {
    register(contribution: any) {
      commands.push(contribution)
      return () => {}
    },
  }
  const triggers = {
    registerSource(source: any) {
      sources.push(source)
      return () => {}
    },
  }
  const sidebarRightTabs = {
    register(definition: any) {
      tabDefs.push(definition)
      return () => {}
    },
  }
  const sidebarRight = {
    openTab(kind: string, options?: any) {
      openedTabs.push({ kind, options })
      return { kind }
    },
  }
  const ctx: any = {
    get: () => undefined,
    effect(callback: () => any, name?: string) {
      effects.push({ callback, name })
      return () => {}
    },
    inject(names: string[], callback: (scope: any) => void) {
      assert.equal(names.length, 1)
      const name = names[0]
      if (name === 'inputTriggers') {
        if (!withTriggers) return
        callback({
          get: (key: string) => (key === 'inputTriggers' ? triggers : undefined),
          effect(callback: () => any) {
            return callback()
          },
        })
        return
      }
      if (name === 'commandUi') {
        if (!withCommandUi) return
        callback({
          get: (key: string) => (key === 'commandUi' ? commandUi : undefined),
          effect(callback: () => any) {
            return callback()
          },
        })
        return
      }
      assert.fail('unexpected optional service: ' + name)
    },
    slots: {
      inject(name: string, callback: () => any) {
        assert.ok(
          name === 'conversation.input.dock' || name === 'sidebar.right.pane.tab',
          'unexpected slot: ' + name,
        )
        return callback()
      },
      register(options: any, component: any) {
        panels.push({ slot: options.name, options, component })
        return () => {}
      },
    },
  }
  ctx.layout = { selectPanel: (id: string) => selectedPanels.push(id) }
  ctx.sidebarRight = sidebarRight
  ctx.sidebarRightTabs = sidebarRightTabs
  return { ctx, panels, sources, effects, commands, selectedPanels, tabDefs, openedTabs }
}

describe('optional client services', () => {
  it('declares the right-sidebar services and registers a presence tab type', () => {
    const mod = loadModule()
    expect(mod.inject).toEqual(['slots', 'sidebarRight', 'sidebarRightTabs'])
    const source = fs.readFileSync(BUNDLE, 'utf8')
    expect(source).toContain('conversation.input.dock')
    expect(source).toContain('blender-selection-bridge')
    expect(source).toContain('blender-presence')
    expect(source).toContain('sidebar.right.pane.tab')
    expect(source).not.toContain('conversation.view')
    expect(source).not.toContain('sidebar.panellist')
  })

  it('registers the selection dock and right-sidebar presence body without optional services', () => {
    const mod = loadModule()
    const host = makeHost(false)
    expect(() => mod.apply(host.ctx)).not.toThrow()

    const dock = host.panels.find((panel) => panel.options.id === 'blender-selection-bridge')
    expect(dock).toBeTruthy()
    expect(dock!.options).toMatchObject({
      name: 'conversation.input.dock',
      id: 'blender-selection-bridge',
      order: 90,
    })
    expect(dock!.options.inject).toBeTypeOf('function')
    expect(dock!.component).toBeTruthy()

    const body = host.panels.find((panel) => panel.slot === 'sidebar.right.pane.tab')
    expect(body).toBeTruthy()
    expect(body!.options.key).toBe('blender-presence')
    expect(body!.component).toBeTruthy()
    expect(host.panels.some((panel) => panel.slot === 'main')).toBe(false)
    expect(host.panels.some((panel) => panel.slot === 'sidebar.panellist')).toBe(false)

    const tabEffect = host.effects.find((effect) =>
      String(effect.name).includes('presence right tab type'),
    )
    expect(tabEffect).toBeTruthy()
    tabEffect!.callback()
    expect(host.tabDefs).toHaveLength(1)
    const definition = host.tabDefs[0]
    expect(definition).toMatchObject({
      id: 'blender-presence',
      kind: 'blender-presence',
      priority: 'extension',
    })
    expect(definition.title()).toBe('Blender Presence')
    expect(definition.guide).toHaveLength(1)
    expect(definition.guide[0]).toMatchObject({ id: 'presence', order: 40 })
    expect(definition.guide[0].icon).toBeTypeOf('function')
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

  it('registers the /blender-presence action and opens the right-sidebar tab without a prompt', () => {
    const mod = loadModule()
    const host = makeHost(true, true)
    mod.apply(host.ctx)
    const command = host.commands.find((item) => item.name === 'blender-presence')
    expect(command).toBeTruthy()
    expect(command.ui.kind).toBe('action')
    command.ui.run({ sessionId: 's' })
    expect(host.openedTabs).toEqual([{ kind: 'blender-presence', options: undefined }])
    expect(host.selectedPanels).toEqual([])
  })

  it('projects Gateway presence rows for the panel', () => {
    const mod = loadModule()
    const rows = mod.__selftest.presenceRowsFromState({
      presence: {
        sessions: [
          {
            id: 'user-gui:inst-1',
            kind: 'user-gui',
            instanceId: 'inst-1',
            agents: [
              {
                id: 'a1b2c3d4e5f6',
                shortId: 'A' + '1',
                displayName: 'agent-main',
                color: '#4C8DFF',
                lastActivity: 'see:viewport',
                lastActivityAt: 1760000000000,
                view: { viewpoint: 'viewport' },
              },
            ],
          },
        ],
      },
    })
    expect(rows).toHaveLength(1)
    expect(rows[0]).toMatchObject({
      sessionId: 'user-gui:inst-1',
      instanceId: 'inst-1',
      shortId: 'A' + '1',
      id: 'a1b2c3d4e5f6',
      displayName: 'agent-main',
      activity: 'see:viewport',
      viewpoint: 'viewport',
    })
    expect(mod.__selftest.formatPresenceTime(0)).toBe('—')
    expect(mod.__selftest.formatPresenceTime(1760000000000)).not.toBe('—')
  })

  it('applies once and resets through its dispose effect', () => {
    const mod = loadModule()
    const host = makeHost(false)
    mod.apply(host.ctx)
    mod.apply(host.ctx)
    expect(host.panels).toHaveLength(2)
    const reset = host.effects.find((effect) => String(effect.name).includes('reset apply guard'))
    expect(reset).toBeTruthy()
    reset!.callback()()
    mod.apply(host.ctx)
    expect(host.panels).toHaveLength(4)
  })
})
