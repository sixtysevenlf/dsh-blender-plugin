
import { afterAll, beforeAll, describe, expect, it } from 'vitest'
import { ADDON_NAME_HINTS, LAUNCH_BOOT_PY, addonNameHit } from '../../../runtime/launch.js'
import { startBackend, freePort, type Backend } from '../../helpers/backend'
import { startFakeAddon } from '../../helpers/fake-addon.mjs'

// S3-c launch: the addon name hints must only match this repo's bridge (no MCP
// for Blender compatibility), the injected boot script must wire the addon and
// the status file, and op=launch must be idempotent / dry-runnable / honest when
// the exe is missing. Spawning a real Blender process is E2E-only.

describe('launch name hints and boot script', () => {
  it('only matches this repository bridge', () => {
    expect(ADDON_NAME_HINTS).toContain('blender_bridge')
    expect(ADDON_NAME_HINTS).toContain('dshbb')
    expect(ADDON_NAME_HINTS.some((hint: string) => hint.includes('mcp'))).toBe(false)
    expect(addonNameHit('dsh_blender_bridge')).toBe(true)
    expect(addonNameHit('dshbb_server')).toBe(true)
    expect(addonNameHit('blender_mcp')).toBe(false)
    expect(addonNameHit('blendermcp_server')).toBe(false)
    expect(addonNameHit('polyhaven')).toBe(false)
  })

  it('injects a boot script that scans, enables and reports', () => {
    for (const marker of ['_name_hit', 'addon_utils', 'addon_utils.enable', 'spec_from_file_location',
      'DSH_BLENDER_LAUNCH_STATUS', 'dsh_blender_bridge', '_report']) {
      expect(LAUNCH_BOOT_PY, marker).toContain(marker)
    }
    expect(LAUNCH_BOOT_PY).not.toContain('blender_mcp')
  })
})

describe('launch through a real backend', () => {
  let backend: Backend
  let addonPort: number

  beforeAll(async () => {
    addonPort = await freePort()
    backend = await startBackend({
      addonPort,
      env: { DSH_BLENDER_LAUNCH_AGENT_URL: 'off', DSH_BLENDER_LAUNCH_WAIT_MS: '2500' },
    })
  })
  afterAll(async () => { await backend.stop() })

  it('is idempotent when the addon is already listening', async () => {
    const addon = await startFakeAddon()
    const second = await startBackend({
      addonPort: addon.port,
      env: { DSH_BLENDER_LAUNCH_AGENT_URL: 'off', DSH_BLENDER_LAUNCH_WAIT_MS: '2500' },
    })
    try {
      const response = await second.post('/launch', {})
      expect(response.json.ok).toBe(true)
      expect(response.json.already).toBe(true)
      expect(response.json.launched).toBe(false)
      expect(response.json.addonPort).toBe(addon.port)
      expect(String(response.json.hint)).toContain('无需启动')
    } finally {
      await second.stop()
      await addon.close()
    }
  })

  it('describes the command on dry run without spawning', async () => {
    const response = await backend.post('/launch', { dryRun: true, exe: 'C:/fake/blender.exe' })
    expect(response.json.ok).toBe(true)
    expect(response.json.dryRun).toBe(true)
    expect(response.json.launched).toBe(false)
    expect(Array.isArray(response.json.args)).toBe(true)
    expect(String(response.json.args[0])).toBe('--python')
    expect(typeof response.json.bootScript).toBe('string')
    expect(String(response.json.hint)).toContain('dry_run')
  })

  it('fails readably when the exe does not exist', async () => {
    const response = await backend.post('/launch', { exe: '/definitely/missing/blender.exe', waitMs: 2000 })
    expect(response.json.ok).toBe(false)
    expect(String(response.json.error)).toContain('不存在')
    expect(String(response.json.hint)).toContain('DSH_BLENDER_EXE')
    expect(typeof response.json.waitedMs).toBe('number')
  })

  it('counts launches in /health', async () => {
    const health = await backend.get('/health')
    expect(health.json.ok).toBe(true)
    expect(health.json.stats.launches).toBeGreaterThanOrEqual(2)
  })
})
