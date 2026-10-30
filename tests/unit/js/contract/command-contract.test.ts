import { afterAll, describe, expect, it } from 'vitest'
import fs from 'node:fs'
import path from 'node:path'
import { COMMAND_CATALOG } from '../../../../runtime/catalog.js'
import { AddonClient } from '../../../../runtime/engine.js'
import { startFakeAddon } from '../../../helpers/fake-addon.mjs'
import { pkgPath } from '../../../helpers/pkg'

const addonDir = pkgPath('addon', 'dsh_blender_bridge')
const readAddon = (...parts: string[]) => fs.readFileSync(path.join(addonDir, ...parts), 'utf8')
const source = readAddon('server.py')
const protocolSource = readAddon('protocol.py')
const coreSource = readAddon('commands', 'core.py')
const premiumSource = readAddon('commands', 'generate_premium.py')

function namesIn(block: string): string[] {
  return [...block.matchAll(/^\s+"([a-z0-9_]+)":\s*(?:self\.|premium_)/gm)].map((m) => m[1]!)
}

function baseHandlers(): string[] {
  const start = source.indexOf('handlers = {')
  const end = source.indexOf('# Add Polyhaven handlers only if enabled')
  expect(start).toBeGreaterThan(0)
  expect(end).toBeGreaterThan(start)
  return namesIn(source.slice(start, end))
}

function gatedHandlers(): Record<string, string[]> {
  const out: Record<string, string[]> = {}
  const re = /if bpy\.context\.scene\.(dshbb_use_\w+):\r?\n\s+\w+_handlers = \{([\s\S]*?)\r?\n\s+\}/g
  for (const m of source.matchAll(re)) out[m[1]!] = namesIn(m[2]!)
  return out
}

function tripoHandlers(): string[] {
  const start = premiumSource.indexOf('def premium_tripo_handlers()')
  expect(start).toBeGreaterThan(0)
  return namesIn(premiumSource.slice(start))
}

function allSourceHandlers(): string[] {
  return [...baseHandlers(), ...Object.values(gatedHandlers()).flat(), ...tripoHandlers()]
}

describe('addon command surface', () => {
  it('publishes 39 catalog commands and 24 always-on base handlers', () => {
    expect(Object.keys(COMMAND_CATALOG)).toHaveLength(39)
    expect(baseHandlers()).toHaveLength(23)
    expect(new Set(allSourceHandlers()).size).toBe(42) // 38 catalog handlers + ping + 4 documented extras
  })

  it('keeps every catalog command backed by a real addon handler', () => {
    const handlers = new Set(allSourceHandlers())
    handlers.add('ping')
    for (const [name, entry] of Object.entries(COMMAND_CATALOG) as [string, any][]) {
      expect(entry.d, name).toBeTruthy()
      expect(handlers.has(name), name).toBe(true)
      if (entry.gate) expect(gatedHandlers()[entry.gate], name).toContain(name)
    }
  })

  it('documents the off-catalog handlers separately', () => {
    const extras = allSourceHandlers().filter((n) => !COMMAND_CATALOG[n]).sort()
    expect(extras).toEqual([
      'create_tripo_job',
      'get_polyhaven_asset_preview',
      'import_generated_asset_tripo',
      'poll_tripo_job_status',
    ])
  })

  it('routes ping before dispatch and returns the flat success/error envelope', () => {
    expect(source).toContain('if cmd_type == "ping":')
    expect(source).toContain('return {"status": "success", "result": {"pong": True}}')
    expect(source).toContain('return {"status": "success", "result": result}')
    expect(source).toContain('return {"status": "error", "message": str(e)}')
    expect(source).toContain('return {"status": "error", "message": f"Unknown command type: {cmd_type}"}')
  })

  it('gates write commands behind the scene switches', () => {
    for (const [gate, names] of Object.entries(gatedHandlers())) {
      expect(source).toContain('if bpy.context.scene.' + gate + ':')
      for (const name of names) {
        if (!COMMAND_CATALOG[name]) continue
        expect(COMMAND_CATALOG[name].gate, name).toBe(gate)
      }
    }
  })

  it('pauses every non-read command and keeps read-only ones safe', () => {
    expect(source).toContain('if _ai_paused() and not _is_pause_safe_command(command.get("type")):')
    expect(source).toContain('"AI_PAUSED"')
    const pauseBlock = protocolSource.slice(protocolSource.indexOf('PAUSE_SAFE_COMMANDS = frozenset({'), protocolSource.indexOf('def _ai_paused'))
    expect(pauseBlock).toContain('"ping"')
    expect(pauseBlock).not.toContain('"execute_code"')
    expect(protocolSource).toContain('if cmd.startswith("get_"):')
  })

  it('advertises protocol 11 with a 16-name capability handshake', () => {
    const m = protocolSource.match(/ADDON_PROTOCOL_VERSION\s*=\s*(\d+)/)
    expect(m?.[1]).toBe('11')
    const info = coreSource.slice(coreSource.indexOf('def get_addon_info'), coreSource.indexOf('def get_scene_info'))
    expect(info).toContain('"protocol_version": ADDON_PROTOCOL_VERSION')
    const caps = info.slice(info.indexOf('"capabilities"'), info.indexOf('"blender_version"'))
    expect([...caps.matchAll(/^\s+"([a-z0-9_]+)",?$/gm)]).toHaveLength(16)
  })
})

describe('addon client command round-trip', () => {
  let addon: Awaited<ReturnType<typeof startFakeAddon>>
  let client: any

  afterAll(async () => {
    client?.close()
    await addon?.close()
  })

  it('performs the flat handshake over a real TCP socket', async () => {
    addon = await startFakeAddon()
    client = new AddonClient({ host: '127.0.0.1', port: addon.port })
    const info = await client.send('get_addon_info', {}, 3000)
    expect(info.protocol).toBe(11)
    const pong = await client.send('ping', {}, 3000)
    expect(pong).toMatchObject({ pong: true, server: 'fake-addon' })
  })

  it('propagates addon error envelopes without rewriting them', async () => {
    await expect(client.send('fail', {}, 3000)).rejects.toThrow('fake addon failure')
  })
})
