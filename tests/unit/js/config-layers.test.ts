import { describe, expect, it } from 'vitest'
import {
  type ConfigLayer,
  configLayerFiles,
  describeConfig,
  mergeConfigLayers,
  parseDnnEnvLock,
  pluginSettingsFromEnv,
} from '../../../runtime/config.js'
import { normalizeRenderCacheLimits } from '../../../runtime/core/queue/render-cache-key.js'
import { pluginSettingsEnvPayload } from '../../../src/host/plugin-settings.js'

const layer = (
  id: string,
  cfg: Record<string, unknown>,
  path: string | null = '/cfg/' + id + '.json',
): ConfigLayer => ({
  id,
  path,
  label: id === 'plugin' ? 'plugin:DSH_BLENDER_PLUGIN_CONFIG' : id + ':' + String(path),
  cfg,
})

describe('mergeConfigLayers', () => {
  it('merges low to high with the later layer winning', () => {
    const merged = mergeConfigLayers([
      layer('package', { addonPort: 9000, features: { dnn: false, virtualGui: 'off' } }),
      layer('home', { features: { virtualGui: 'on' }, cache: { render: { maxEntries: 8 } } }),
      layer('file', { addonPort: 9100 }),
      layer('plugin', { features: { dnn: true }, vision: { model: 'yolov5s' } }, null),
    ])
    expect(merged.cfg.addonPort).toBe(9100)
    expect(merged.cfg.features).toMatchObject({ dnn: true, virtualGui: 'on' })
    expect(merged.cfg.cache).toMatchObject({ render: { maxEntries: 8 } })
    expect(merged.cfg.vision).toMatchObject({ model: 'yolov5s' })
    expect(merged.sources['addonPort']).toBe('file:/cfg/file.json')
    expect(merged.sources['features.dnn']).toBe('plugin:DSH_BLENDER_PLUGIN_CONFIG')
    expect(merged.sources['features.virtualGui']).toBe('home:/cfg/home.json')
    expect(merged.sources['cache.render.maxEntries']).toBe('home:/cfg/home.json')
    expect(merged.from).toBe('/cfg/file.json')
  })

  it('records defaults by omission and replaces arrays as a whole', () => {
    const merged = mergeConfigLayers([
      layer('package', { passes: ['beauty', 'depth'], name: 'a' }),
      layer('home', { passes: ['alpha'] }),
    ])
    expect(merged.cfg.passes).toEqual(['alpha'])
    expect(merged.sources['passes']).toBe('home:/cfg/home.json')
    expect(merged.sources['addonPort']).toBeUndefined()
    expect(merged.from).toBe('/cfg/home.json')
  })

  it('keeps empty layers from inventing values', () => {
    const merged = mergeConfigLayers([])
    expect(merged.cfg).toEqual({})
    expect(merged.from).toBeNull()
    expect(merged.layers).toEqual([])
  })
})

describe('pluginSettingsFromEnv', () => {
  it('accepts a JSON object and ignores empty input', () => {
    expect(pluginSettingsFromEnv('')).toEqual({ cfg: null, warning: null })
    expect(pluginSettingsFromEnv(undefined)).toEqual({ cfg: null, warning: null })
    expect(pluginSettingsFromEnv('{"features":{"dnn":true}}')).toMatchObject({
      cfg: { features: { dnn: true } },
      warning: null,
    })
  })

  it('turns malformed settings into a warning instead of throwing', () => {
    const bad = pluginSettingsFromEnv('{oops')
    expect(bad.cfg).toBeNull()
    expect(String(bad.warning)).toContain('DSH_BLENDER_PLUGIN_CONFIG')
    const notObject = pluginSettingsFromEnv('"text"')
    expect(notObject.cfg).toBeNull()
    expect(String(notObject.warning)).toContain('JSON 对象')
  })
})

describe('configLayerFiles', () => {
  it('puts the explicit DSH_BLENDER_CONFIG on top of the home config', () => {
    const files = configLayerFiles({ DSH_BLENDER_CONFIG: '/explicit/cfg.json' })
    expect(files.explicit).toBe('/explicit/cfg.json')
    expect(files.home.endsWith('dsh-blender.config.json')).toBe(true)
    expect(files.home).toContain('dsh-blender')
    expect(files.package.endsWith('dsh-blender.config.json')).toBe(true)
  })

  it('leaves explicit null when the variable is blank', () => {
    expect(configLayerFiles({ DSH_BLENDER_CONFIG: '  ' }).explicit).toBeNull()
  })
})

describe('describeConfig diagnostics (v3.2)', () => {
  it('reports home, layer sources and feature flags without secrets', () => {
    const cfg = describeConfig({})
    expect(cfg.home).toMatchObject({
      path: expect.any(String),
      source: expect.any(String),
      layout: { ok: true },
    })
    expect(Array.isArray(cfg.configLayers)).toBe(true)
    expect(cfg.sources).toEqual(expect.any(Object))
    expect(cfg.features).toMatchObject({
      dnn: expect.any(Boolean),
      dnnSource: expect.any(String),
      dnnLocked: expect.any(Boolean),
      dnnWarning: null,
    })
    expect(Array.isArray(cfg.warnings)).toBe(true)
    expect(String(cfg.env.DSH_BLENDER_PLUGIN_CONFIG)).not.toContain('{')
  })

  it('keeps the legacy workDir contract for doctor consumers', () => {
    const cfg = describeConfig({})
    expect(cfg.workDir).toMatchObject({
      win: expect.any(String),
      wsl: expect.any(String),
      from: expect.any(String),
    })
    expect(Object.prototype.hasOwnProperty.call(cfg.workDir, 'shared')).toBe(true)
  })
})

describe('DSH_BLENDER_DNN canonical parsing', () => {
  it('accepts only on/true/1 and off/false/0, case-insensitively', () => {
    for (const raw of ['on', 'ON', 'True', '1', ' true ']) {
      expect(parseDnnEnvLock(raw)).toEqual({ lock: 'on', value: true, invalid: null })
    }
    for (const raw of ['off', 'OFF', 'False', '0', ' false ']) {
      expect(parseDnnEnvLock(raw)).toEqual({ lock: 'off', value: false, invalid: null })
    }
    expect(parseDnnEnvLock(undefined)).toEqual({ lock: null, value: null, invalid: null })
    expect(parseDnnEnvLock('  ')).toEqual({ lock: null, value: null, invalid: null })
  })

  it('ignores invalid spellings instead of locking DNN off, and keeps the raw value', () => {
    for (const raw of ['maybe', 'yes', 'enabled', '2']) {
      expect(parseDnnEnvLock(raw)).toEqual({ lock: null, value: null, invalid: raw })
    }
  })
})

describe('invalid cache.render on the plugin layer reaches the doctor channel', () => {
  it('keeps the raw value in the layer and lets normalizeRenderCacheLimits report it', () => {
    const payload = pluginSettingsEnvPayload({ cache: { render: { maxEntries: 0 } } })
    const merged = mergeConfigLayers([
      layer('home', { cache: { render: { maxEntries: 12 } } }),
      layer('plugin', JSON.parse(payload.json), null),
    ])
    expect(merged.cfg.cache).toMatchObject({ render: { maxEntries: 0 } })
    const resolved = normalizeRenderCacheLimits(merged.cfg.cache.render)
    expect(resolved.limits.maxEntries).toBe(8)
    expect(resolved.issues).toEqual([
      expect.objectContaining({
        field: 'maxEntries',
        value: 0,
        min: 1,
        max: 32,
        reason: 'out-of-range',
        used: 8,
      }),
    ])
  })
})
