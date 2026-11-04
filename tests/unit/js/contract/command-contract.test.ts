import { afterAll, describe, expect, it } from 'vitest'
import fs from 'node:fs'
import path from 'node:path'
import { COMMAND_CATALOG, COMMAND_CATALOG_OMISSIONS } from '../../../../runtime/catalog/index.js'
import { AddonClient } from '../../../../runtime/transport/probes.js'
import { startFakeAddon } from '../../../helpers/fake-addon.mjs'
import { pkgPath } from '../../../helpers/pkg'

// the addon now has one command_registry.py; server/protocol derive from it.
// This contract test reads the tree rather than a fixed file list so the split
// into commands/<domain>/ does not make the source scan silently empty.

const addonDir = pkgPath('addon', 'dsh_blender_bridge')
const readAddon = (...parts: string[]) => fs.readFileSync(path.join(addonDir, ...parts), 'utf8')
function readTree(dir: string): string {
  return fs
    .readdirSync(dir, { withFileTypes: true })
    .map((entry) => {
      const full = path.join(dir, entry.name)
      return entry.isDirectory()
        ? readTree(full)
        : entry.name.endsWith('.py')
          ? fs.readFileSync(full, 'utf8')
          : ''
    })
    .join('\n')
}
const addonSource = readTree(addonDir)
const registrySource = readAddon('command_registry.py')
const protocolSource = readAddon('protocol.py')
const serverSource = readAddon('server.py')
const coreInfo = readAddon('commands', 'core', '__init__.py')

function registryCommands(): string[] {
  return [...registrySource.matchAll(/_command\("([^"]+)"/g)].map((m) => m[1]!)
}

describe('addon command surface', () => {
  const registered = registryCommands()

  it('publishes 38 catalog commands and 39 wire commands', () => {
    expect(Object.keys(COMMAND_CATALOG)).toHaveLength(38)
    expect(registrySource).toContain('REGISTERED_COMMAND_COUNT = 38')
    expect(registrySource).toContain('WIRE_COMMAND_COUNT = 39')
    expect(registered).toHaveLength(39)
    expect(new Set(registered).size).toBe(39)
    expect(registered).toContain('ping')
    expect(registered.filter((n) => n !== 'ping')).toHaveLength(38)
  })

  it('keeps the host catalog and the addon registry aligned in both directions', () => {
    const host = new Set(Object.keys(COMMAND_CATALOG))
    const registryNames = registered.filter((name) => name !== 'ping')
    const registrySet = new Set(registryNames)
    // Every model-facing catalog entry must exist on the addon wire registry.
    expect([...host].filter((name) => name !== 'ping' && !registrySet.has(name))).toEqual([])
    // Every registered command must be model-facing or explicitly declared omitted.
    const omitted = new Set(COMMAND_CATALOG_OMISSIONS)
    expect(registryNames.filter((name) => !host.has(name))).toEqual([...omitted])
  })

  it('keeps every catalog command in the registry with a real handler', () => {
    const registeredSet = new Set(registered)
    for (const name of Object.keys(COMMAND_CATALOG)) {
      expect(registeredSet.has(name), name).toBe(true)
      if (name === 'ping') continue
      expect(addonSource, name).toContain('def ' + name + '(')
    }
    // ping is the transport probe: its handler is ping_response, not ping().
    expect(registrySource).toContain('handler="ping_response"')
    expect(addonSource).toContain('def ping_response(')
  })

  it('derives the gated commands from the registry and keeps ping off-catalog', () => {
    expect(registrySource).toContain('GATE_PROPERTIES')
    expect(registrySource).toContain('"polyhaven"')
    expect(registrySource).toContain('"hyper3d"')
    expect(registrySource).toContain('"sketchfab"')
    expect(registrySource).toContain('"polypizza"')
    expect(registrySource).toContain('"hunyuan"')
    expect(COMMAND_CATALOG.ping).toBeDefined()
    expect(COMMAND_CATALOG.get_polyhaven_asset_preview).toBeUndefined()
    expect(serverSource).toContain('WIRE_SPECS')
    expect(serverSource).toContain('gate_property')
  })

  it('gates every write command through the registry gate metadata', () => {
    const gated = [
      'get_polyhaven_categories',
      'search_polyhaven_assets',
      'download_polyhaven_asset',
      'download_sketchfab_model',
      'download_polypizza_model',
      'create_hunyuan_job',
      'create_rodin_job',
    ]
    expect(gated).toHaveLength(7)
    for (const name of gated) expect(registrySource, name).toContain('"' + name + '"')
    for (const name of gated) expect(COMMAND_CATALOG[name]?.gate, name).toBeTruthy()
    // The catalog also gates read/download commands; the 7 above are the write
    // subset. Both counts are pinned so a lost/renamed gate cannot pass silently.
    const allGated = Object.keys(COMMAND_CATALOG).filter((n) => COMMAND_CATALOG[n]?.gate)
    expect(allGated).toHaveLength(15)
    for (const name of gated) expect(allGated, name).toContain(name)
  })

  it('pauses every non-read command and keeps read-only ones safe', () => {
    expect(protocolSource).toContain('ADDON_PROTOCOL_VERSION = 11')
    expect(protocolSource).toContain('is_pause_safe_command')
    expect(registrySource).toContain('PAUSE_SAFE_EXTRA')
    expect(registrySource).toContain('"ping"')
    expect(protocolSource).toContain('if cmd.startswith("get_"):')
    expect(serverSource).toContain('is_pause_safe_command')
  })

  it('advertises protocol 11 with capabilities derived from the command registry', () => {
    expect(protocolSource).toMatch(/ADDON_PROTOCOL_VERSION\s*=\s*11/)
    expect(coreInfo).toContain('"protocol_version": ADDON_PROTOCOL_VERSION')
    // the handshake must not drift back to a hardcoded capability list.
    expect(coreInfo).toMatch(/"capabilities":\s*sorted\(COMMAND_NAMES\)/)
    expect(coreInfo).not.toMatch(/"capabilities":\s*sorted\(\[/)
    expect(coreInfo).toMatch(/from \.\.\.command_registry import COMMAND_NAMES/)
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
