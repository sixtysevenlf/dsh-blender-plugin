import { afterEach, describe, expect, it, vi } from 'vitest'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { createFakeContext } from '../../helpers/fake-context'
import * as plugin from '../../../src/index.js'
import { resetHomeCache } from '../../../runtime/home.js'
import { loadConfigLayers, mergeConfigLayers } from '../../../runtime/config.js'
import { resetDnnHomeCache } from '../../../src/host/dnn-home.js'
import {
  DEFAULT_PLUGIN_SETTINGS,
  PLUGIN_SETTINGS_ENV,
  applySettingsEnv,
  normalizePluginSettings,
  pluginSettingsEnvPayload,
  readPluginSettingsFromEnv,
  repairPluginSettings,
  resolvePluginSettings,
  serializePluginSettings,
  validatePluginSettings,
} from '../../../src/host/plugin-settings.js'

describe('plugin config schema', () => {
  it('publishes the documented defaults for the new sections', () => {
    if (!plugin.Config) return
    const resolve = plugin.Config as unknown as (input: unknown) => any
    const out = resolve({})
    expect(out.features.dnn).toBe(false)
    expect(out.cache.render).toEqual({ maxEntries: 8, maxBytes: 4 * 1024 ** 3, ttlDays: 7 })
    expect(out.vision.dnn).toEqual({ backend: 'opencv', device: 'cpu' })
    expect(out.vision.model).toBe('yolov5n')
  })
})

describe('plugin settings normalization', () => {
  it('defaults every field for empty or broken input', () => {
    for (const input of [undefined, null, {}, [], 'nope', 42]) {
      expect(normalizePluginSettings(input)).toEqual(DEFAULT_PLUGIN_SETTINGS)
    }
  })

  it('reads valid values without changing them', () => {
    const settings = normalizePluginSettings({
      features: { dnn: true },
      cache: { render: { maxEntries: 12, maxBytes: 2 * 1024 ** 3, ttlDays: 14 } },
      vision: {
        python: 'C:\\Python311\\python.exe',
        venv: 'C:\\venvs\\cv',
        model: 'yolov5s',
        dnn: { backend: 'onnxruntime', device: 'auto' },
      },
    })
    expect(settings).toEqual({
      features: { dnn: true },
      cache: { render: { maxEntries: 12, maxBytes: 2 * 1024 ** 3, ttlDays: 14 } },
      vision: {
        python: 'C:\\Python311\\python.exe',
        venv: 'C:\\venvs\\cv',
        model: 'yolov5s',
        dnn: { backend: 'onnxruntime', device: 'auto' },
      },
    })
  })

  it('normalizes invalid cache numbers to the documented defaults and reports issues (no clamping)', () => {
    const low = resolvePluginSettings({
      cache: { render: { maxEntries: 0, maxBytes: 1, ttlDays: -5 } },
    })
    expect(low.settings.cache.render).toEqual({
      maxEntries: DEFAULT_PLUGIN_SETTINGS.cache.render.maxEntries,
      maxBytes: DEFAULT_PLUGIN_SETTINGS.cache.render.maxBytes,
      ttlDays: DEFAULT_PLUGIN_SETTINGS.cache.render.ttlDays,
    })
    expect(low.issues.map((issue) => issue.field)).toEqual([
      'cache.render.maxEntries',
      'cache.render.maxBytes',
      'cache.render.ttlDays',
    ])
    expect(low.issues[0]).toMatchObject({
      field: 'cache.render.maxEntries',
      value: 0,
      min: 1,
      max: 32,
      reason: 'out-of-range',
      used: 8,
    })
    expect(low.issues[0].allowed).toContain('1')

    const high = resolvePluginSettings({
      cache: { render: { maxEntries: 999, maxBytes: 99 * 1024 ** 3, ttlDays: 90 } },
    })
    expect(high.settings.cache.render).toEqual(DEFAULT_PLUGIN_SETTINGS.cache.render)
    expect(high.issues).toHaveLength(3)

    const fractional = resolvePluginSettings({
      cache: { render: { maxEntries: 4.6, maxBytes: 2.5 * 1024 ** 3, ttlDays: 7.2 } },
    })
    // A fractional value is not rounded into range: it falls back to the default.
    expect(fractional.settings.cache.render.maxEntries).toBe(8)
    expect(fractional.settings.cache.render.ttlDays).toBe(7)
    expect(fractional.issues.map((issue) => issue.reason)).toContain('not-an-integer')
  })

  it('falls back to valid enums when the value is not a known option', () => {
    const settings = normalizePluginSettings({
      vision: { dnn: { backend: 'tensorrt', device: 'tpu' } },
    })
    expect(settings.vision.dnn).toEqual({ backend: 'opencv', device: 'cpu' })
  })
})

describe('plugin settings validation', () => {
  it('accepts the documented default combination', () => {
    const result = validatePluginSettings(normalizePluginSettings({}))
    expect(result.ok).toBe(true)
    expect(result.errors).toEqual([])
  })

  it('rejects opencv with a non-cpu device', () => {
    for (const device of ['auto', 'gpu'] as const) {
      const settings = normalizePluginSettings({ vision: { dnn: { backend: 'opencv', device } } })
      const result = validatePluginSettings(settings)
      expect(result.ok).toBe(false)
      expect(result.errors.join('\n')).toContain('backend=opencv')
    }
  })

  it('rejects a relative vision.venv and a malformed model id', () => {
    const settings = normalizePluginSettings({
      vision: { venv: 'relative/venv', model: '../escape' },
    })
    const result = validatePluginSettings(settings)
    expect(result.ok).toBe(false)
    expect(result.errors.join('\n')).toContain('vision.venv')
    expect(result.errors.join('\n')).toContain('vision.model')
  })

  it('warns (without failing) when onnxruntime is selected', () => {
    const settings = normalizePluginSettings({
      vision: {
        venv: path.resolve('/tmp/cv-venv'),
        dnn: { backend: 'onnxruntime', device: 'cpu' },
      },
    })
    const result = validatePluginSettings(settings)
    expect(result.ok).toBe(true)
    expect(result.warnings.join('\n')).toContain('onnxruntime')
  })
})

describe('plugin settings repair', () => {
  it('repairs only the invalid fields and records what changed', () => {
    const broken = normalizePluginSettings({
      features: { dnn: true },
      vision: { venv: 'relative', model: 'bad id', dnn: { backend: 'opencv', device: 'gpu' } },
    })
    const { settings, repairs } = repairPluginSettings(broken)
    expect(settings.features.dnn).toBe(true)
    expect(settings.vision.venv).toBe('')
    expect(settings.vision.model).toBe('yolov5n')
    expect(settings.vision.dnn).toEqual({ backend: 'opencv', device: 'cpu' })
    expect(repairs).toHaveLength(3)
    expect(validatePluginSettings(settings).ok).toBe(true)
  })
})

describe('plugin activation', () => {
  const tmpDirs: string[] = []

  afterEach(() => {
    delete process.env.DSH_BLENDER_HOME
    while (tmpDirs.length) {
      const dir = tmpDirs.pop()
      if (dir) fs.rmSync(dir, { recursive: true, force: true })
    }
  })

  it('serializes settings into the gateway environment without touching runtime.json', () => {
    const home = fs.mkdtempSync(path.join(os.tmpdir(), 'dsh-plugin-settings-home-'))
    tmpDirs.push(home)
    const snapshot = path.join(home, 'vision', 'runtime.json')
    fs.mkdirSync(path.dirname(snapshot), { recursive: true })
    fs.writeFileSync(snapshot, '{"revision":1,"features":{"dnn":false}}', 'utf8')
    const previous = process.env.DSH_BLENDER_PLUGIN_CONFIG
    process.env.DSH_BLENDER_HOME = home
    process.env.DSH_BLENDER_PLUGIN_CONFIG = '{"stale":true}'
    const f = createFakeContext()
    try {
      plugin.apply(f.ctx, {
        autoStart: false,
        tui: false,
        port: 9898,
        features: { dnn: true },
        vision: { model: 'yolov5s', dnn: { backend: 'opencv', device: 'cpu' } },
      })
      const serialized = JSON.parse(process.env.DSH_BLENDER_PLUGIN_CONFIG || '{}')
      expect(serialized.features.dnn).toBe(true)
      expect(serialized.vision.model).toBe('yolov5s')
      expect(serialized.stale).toBeUndefined()
      expect(fs.readFileSync(snapshot, 'utf8')).toBe('{"revision":1,"features":{"dnn":false}}')
    } finally {
      f.dispose()
      if (previous === undefined) delete process.env.DSH_BLENDER_PLUGIN_CONFIG
      else process.env.DSH_BLENDER_PLUGIN_CONFIG = previous
    }
  })
})

describe('plugin settings serialization', () => {
  it('serializes a stable JSON object with only non-default keys', () => {
    const settings = normalizePluginSettings({
      features: { dnn: true },
      cache: { render: { maxEntries: 3, maxBytes: 1024 ** 3, ttlDays: 2 } },
      vision: { python: '/usr/bin/python3', model: 'yolov5m' },
    })
    const raw = serializePluginSettings(settings)
    // venv '' and vision.dnn defaults are omitted so the home config still wins.
    expect(JSON.parse(raw)).toEqual({
      features: { dnn: true },
      cache: { render: { maxEntries: 3, maxBytes: 1024 ** 3, ttlDays: 2 } },
      vision: { python: '/usr/bin/python3', model: 'yolov5m' },
    })
    expect(serializePluginSettings(settings)).toBe(raw)
    expect(raw).not.toContain('password')
    expect(raw).not.toContain('token')
    // Untouched settings serialize to an empty layer: no shadowing, source=default.
    expect(serializePluginSettings(DEFAULT_PLUGIN_SETTINGS)).toBe('{}')
  })

  it('passes invalid cache values through raw so the Gateway reports them like a config file', () => {
    const payload = pluginSettingsEnvPayload({ cache: { render: { maxEntries: 0 } } }, undefined)
    expect(JSON.parse(payload.json)).toEqual({ cache: { render: { maxEntries: 0 } } })
    expect(payload.issues).toHaveLength(1)
    expect(payload.issues[0]).toMatchObject({
      field: 'cache.render.maxEntries',
      value: 0,
      used: 8,
      reason: 'out-of-range',
    })
  })

  it('applies into and reads back from the environment', () => {
    const env: NodeJS.ProcessEnv = {}
    const settings = normalizePluginSettings({
      features: { dnn: true },
      vision: { model: 'yolov5l' },
    })
    applySettingsEnv(settings, env)
    expect(env[PLUGIN_SETTINGS_ENV]).toBe(serializePluginSettings(settings))
    expect(readPluginSettingsFromEnv(env[PLUGIN_SETTINGS_ENV])).toEqual(settings)
  })

  it('returns null for missing or malformed environment values', () => {
    expect(readPluginSettingsFromEnv(undefined)).toBeNull()
    expect(readPluginSettingsFromEnv('')).toBeNull()
    expect(readPluginSettingsFromEnv('not-json')).toBeNull()
    expect(readPluginSettingsFromEnv('[1,2]')).toBeNull()
  })
})

describe('plugin settings layer end to end', () => {
  const tmpDirs: string[] = []

  afterEach(() => {
    delete process.env.DSH_BLENDER_HOME
    delete process.env.DSH_BLENDER_PLUGIN_CONFIG
    resetHomeCache()
    resetDnnHomeCache()
    while (tmpDirs.length) {
      const dir = tmpDirs.pop()
      if (dir) fs.rmSync(dir, { recursive: true, force: true })
    }
  })

  function setupHome(config: Record<string, unknown>): string {
    const home = fs.mkdtempSync(path.join(os.tmpdir(), 'dsh-plugin-settings-layering-'))
    tmpDirs.push(home)
    fs.writeFileSync(
      path.join(home, 'dsh-blender.config.json'),
      JSON.stringify(config) + '\n',
      'utf8',
    )
    process.env.DSH_BLENDER_HOME = home
    delete process.env.DSH_BLENDER_PLUGIN_CONFIG
    resetHomeCache()
    resetDnnHomeCache()
    return home
  }

  it('lets the home config win for untouched plugin settings and reports the home source', () => {
    setupHome({ features: { dnn: true }, vision: { model: 'mymodel' } })
    const f = createFakeContext()
    try {
      plugin.apply(f.ctx, { autoStart: false, tui: false, port: 9899 })
      // No defaults are serialized, so nothing shadows the home layer.
      expect(JSON.parse(process.env.DSH_BLENDER_PLUGIN_CONFIG || '{}')).toEqual({})
      const merged = mergeConfigLayers(loadConfigLayers())
      const features = merged.cfg.features as { dnn?: boolean }
      const vision = merged.cfg.vision as { model?: string }
      expect(features.dnn).toBe(true)
      expect(vision.model).toBe('mymodel')
      expect(merged.sources['features.dnn']).toMatch(/^home:/)
      expect(merged.sources['vision.model']).toMatch(/^home:/)
      // A value nobody set has no source: configSource() reports `default`.
      expect(merged.sources['vision.dnn.device']).toBeUndefined()
    } finally {
      f.dispose()
    }
  })

  it('lets an explicit non-default plugin value win over the home config', () => {
    setupHome({ features: { dnn: true }, vision: { model: 'mymodel' } })
    const f = createFakeContext()
    try {
      plugin.apply(f.ctx, {
        autoStart: false,
        tui: false,
        port: 9899,
        vision: { model: 'yolov5x' },
      })
      expect(JSON.parse(process.env.DSH_BLENDER_PLUGIN_CONFIG || '{}')).toEqual({
        vision: { model: 'yolov5x' },
      })
      const merged = mergeConfigLayers(loadConfigLayers())
      const features = merged.cfg.features as { dnn?: boolean }
      const vision = merged.cfg.vision as { model?: string }
      expect(vision.model).toBe('yolov5x')
      expect(merged.sources['vision.model']).toMatch(/^plugin:DSH_BLENDER_PLUGIN_CONFIG/)
      // Keys the user did not touch still come from home.
      expect(features.dnn).toBe(true)
      expect(merged.sources['features.dnn']).toMatch(/^home:/)
    } finally {
      f.dispose()
    }
  })

  it('surfaces home_unwritable from apply() without crashing', () => {
    const file = path.join(os.tmpdir(), 'dsh-plugin-settings-home-file-' + String(process.pid))
    fs.writeFileSync(file, 'not a directory', 'utf8')
    tmpDirs.push(file)
    process.env.DSH_BLENDER_HOME = file
    delete process.env.DSH_BLENDER_PLUGIN_CONFIG
    resetHomeCache()
    resetDnnHomeCache()
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    const f = createFakeContext()
    try {
      expect(() => plugin.apply(f.ctx, { autoStart: false, tui: false, port: 9899 })).not.toThrow()
      const structured = warn.mock.calls
        .map((call) => String(call[0]))
        .find((text) => text.includes('home_unwritable'))
      expect(structured).toBeTruthy()
      expect(structured).toContain('"path"')
      expect(structured).toContain('"reason"')
      expect(structured).toContain('"fix"')
      // The gateway still gets the settings; only writes were skipped.
      expect(process.env.DSH_BLENDER_PLUGIN_CONFIG).toBeDefined()
    } finally {
      warn.mockRestore()
      f.dispose()
    }
  })
})
