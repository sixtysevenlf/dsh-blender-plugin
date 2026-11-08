import { afterEach, describe, expect, it, vi } from 'vitest'

// split launch.ts into launch/resolve.ts. These tests exercise the
// Windows launch-agent handshake directly (CFG and fetch are controlled here)
// instead of only asserting that the source mentions the right strings.

const h = vi.hoisted(() => ({
  cfg: { launchAgentUrl: '' } as Record<string, unknown>,
}))
vi.mock('../../../runtime/config.js', () => ({ CFG: h.cfg, winToWsl: (p: string) => p }))

import {
  ADDON_NAME_HINTS,
  LAUNCH_BOOT_PY,
  addonNameHit,
  tryLaunchAgent,
} from '../../../runtime/launch/resolve.js'

function jsonResponse(body: unknown, ok = true): Response {
  return { ok, json: async () => body } as unknown as Response
}

const base = { ok: true }

afterEach(() => {
  vi.unstubAllGlobals()
  h.cfg.launchAgentUrl = ''
})

describe('addon name hints', () => {
  it('keeps exactly the package/legacy-MCP-free hint list', () => {
    expect(ADDON_NAME_HINTS).toEqual(['blender_bridge', 'dshbb', 'dsh_blender_bridge'])
    expect(addonNameHit('dsh_blender_bridge.zip')).toBe(true)
    expect(addonNameHit('DSH_BLENDER_BRIDGE')).toBe(true)
    expect(addonNameHit('dshbb_server')).toBe(true)
    expect(addonNameHit('blender_bridge')).toBe(true)
    // v2.3 dropped MCP for Blender support: the old module name must not match.
    expect(addonNameHit('blender_mcp')).toBe(false)
    expect(addonNameHit('')).toBe(false)
    expect(addonNameHit(null)).toBe(false)
  })

  it('inlines the same hint list into the boot script Python source', () => {
    const line = LAUNCH_BOOT_PY.split('\n').find((l) => l.startsWith('_HINTS = '))
    expect(line).toBeDefined()
    expect(JSON.parse((line as string).slice('_HINTS = '.length))).toEqual(ADDON_NAME_HINTS)
    expect(LAUNCH_BOOT_PY).toContain('def _name_hit(name):')
    expect(LAUNCH_BOOT_PY).toContain('return any(k in n for k in _HINTS)')
    expect(LAUNCH_BOOT_PY).toContain('"addon_module": None')
    expect(LAUNCH_BOOT_PY).toContain('"addon_version": None')
    expect(LAUNCH_BOOT_PY).toContain('"addon_issue": None')
    expect(LAUNCH_BOOT_PY).toContain('def _set_issue(code, detail, fix):')
    expect(LAUNCH_BOOT_PY).toContain('addon_not_installed')
    expect(LAUNCH_BOOT_PY).toContain('addon_disabled')
    expect(LAUNCH_BOOT_PY).toContain('addon_version_mismatch')
    expect(LAUNCH_BOOT_PY).toContain('addon_single_file')
    expect(LAUNCH_BOOT_PY).toContain('getattr(mod, "ADDON_VERSION", None)')
    expect(LAUNCH_BOOT_PY).toContain('bpy.context.preferences.addons.keys()')
    expect(LAUNCH_BOOT_PY).toContain('bl_ext.*.dsh_blender_bridge')
    expect(LAUNCH_BOOT_PY).toContain('protocol == 12')
  })
})

describe('tryLaunchAgent (windows launch agent handshake)', () => {
  it('is a no-op when the agent URL is empty or off', async () => {
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    const steps: string[] = []
    await expect(tryLaunchAgent({}, steps, base)).resolves.toBeNull()
    h.cfg.launchAgentUrl = 'off'
    await expect(tryLaunchAgent({}, steps, base)).resolves.toBeNull()
    expect(fetchMock).not.toHaveBeenCalled()
    expect(steps).toEqual([])
  })

  it('skips the agent when interop is requested explicitly', async () => {
    h.cfg.launchAgentUrl = 'http://127.0.0.1:7788'
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    const steps: string[] = []
    await expect(tryLaunchAgent({ interop: true }, steps, base)).resolves.toBeNull()
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('returns null when the health probe fails or is not ok', async () => {
    h.cfg.launchAgentUrl = 'http://127.0.0.1:7788'
    const steps: string[] = []
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => jsonResponse({}, false)),
    )
    await expect(tryLaunchAgent({}, steps, base)).resolves.toBeNull()
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new Error('ECONNREFUSED')
      }),
    )
    await expect(tryLaunchAgent({}, steps, base)).resolves.toBeNull()
    expect(steps).toEqual([])
  })

  it('POSTs the launch request and merges the agent answer with via=windows-agent', async () => {
    h.cfg.launchAgentUrl = 'http://127.0.0.1:7788/'
    const calls: Array<{ url: string; init?: RequestInit }> = []
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string, init?: RequestInit) => {
        calls.push({ url, init })
        return url.endsWith('/health')
          ? jsonResponse({ ok: true })
          : jsonResponse({ already: false, dispatched: true, launched: true })
      }),
    )
    const steps: string[] = []
    const out = await tryLaunchAgent(
      { file: 'D:/x.blend', force: 1, exe: 'D:/blender.exe' },
      steps,
      base,
    )
    expect(out).toMatchObject({
      ok: true,
      already: false,
      dispatched: true,
      via: 'windows-agent',
    })
    expect(calls.map((c) => c.url)).toEqual([
      'http://127.0.0.1:7788//health',
      'http://127.0.0.1:7788//launch',
    ])
    expect(calls[1].init?.method).toBe('POST')
    expect(JSON.parse(String(calls[1].init?.body))).toEqual({
      file: 'D:/x.blend',
      force: true,
      blenderExe: 'D:/blender.exe',
    })
    expect(steps.join('|')).toContain('Windows 启动代理在线')
    expect(steps.join('|')).toContain('代理已受理')
  })

  it('degrades to dispatched when the launch call fails (never double-launches)', async () => {
    h.cfg.launchAgentUrl = 'http://127.0.0.1:7788'
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) => {
        if (url.endsWith('/health')) return jsonResponse({ ok: true })
        throw new Error('socket hang up')
      }),
    )
    const steps: string[] = []
    const out = await tryLaunchAgent({ file: null }, steps, base)
    expect(out).toMatchObject({
      ok: false,
      dispatched: true,
      launched: true,
      listening: false,
      via: 'windows-agent',
    })
    expect(steps.join('|')).toContain('socket hang up')
    expect(steps.join('|')).toContain('按已派发处理')
  })
})
