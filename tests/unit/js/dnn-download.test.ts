import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { afterEach, describe, expect, it } from 'vitest'
import type { DnnPin } from '../../../src/host/dnn-pins.js'
import {
  COCO80_LABELS,
  appendModelsYamlEntries,
  downloadDnnPins,
  dnnDownloadStatus,
  resetDnnDownloadForTests,
} from '../../../src/host/dnn-download.js'

const tempDirs: string[] = []

function tempDir(): string {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'dsh-blender-dnn-dl-'))
  tempDirs.push(dir)
  return dir
}

function pin(overrides: Partial<DnnPin> = {}): DnnPin {
  return {
    id: 'yolov5n',
    filename: 'yolov5n.onnx',
    url: 'https://example.test/yolov5n.onnx',
    bytes: 4,
    sha256: '87e93f89f2be0db364e8be052f79f389e6c2da239831922e24513288af522a43',
    source: 'ultralytics/yolov5 v7.0',
    license: 'GPL-3.0',
    ...overrides,
  }
}

function fetchReturning(map: Record<string, Uint8Array>) {
  const calls: string[] = []
  return {
    calls,
    fetchBytes: async (url: string) => {
      calls.push(url)
      const body = map[url]
      if (!body) throw new Error('unexpected url ' + url)
      return body
    },
  }
}

const BODY = new Uint8Array(Buffer.from('onnx'))
const TWO_PINS = [
  pin(),
  pin({
    id: 'yolov5s',
    filename: 'yolov5s.onnx',
    url: 'https://example.test/yolov5s.onnx',
    sha256: '87e93f89f2be0db364e8be052f79f389e6c2da239831922e24513288af522a43',
  }),
]
const TWO_BODIES: Record<string, Uint8Array> = {
  'https://example.test/yolov5n.onnx': BODY,
  'https://example.test/yolov5s.onnx': BODY,
}

afterEach(() => {
  resetDnnDownloadForTests()
  for (const dir of tempDirs.splice(0)) fs.rmSync(dir, { recursive: true, force: true })
})

describe('dnn download', () => {
  it('refuses unverified pins without touching the filesystem at all', async () => {
    const home = tempDir()
    const modelsDir = path.join(home, 'vision', 'models')
    let fetched = 0
    const result = await downloadDnnPins(['yolov5n'], {
      home,
      pins: [pin({ bytes: null, sha256: null })],
      fetchBytes: async () => {
        fetched += 1
        return BODY
      },
    })
    expect(result.ok).toBe(false)
    expect(result.code).toBe('dnn_pin_unavailable')
    expect(fetched).toBe(0)
    expect(fs.existsSync(modelsDir)).toBe(false)
    expect(fs.readdirSync(home)).toEqual([])
  })

  it('downloads verified pins with sha256 and byte checks, labels and models.yml', async () => {
    const home = tempDir()
    const modelsDir = path.join(home, 'vision', 'models')
    const src = fetchReturning(TWO_BODIES)
    const result = await downloadDnnPins(['yolov5n', 'yolov5s'], {
      home,
      pins: TWO_PINS,
      fetchBytes: src.fetchBytes,
    })
    expect(result.ok).toBe(true)
    expect(result.items.map((i) => [i.id, i.state])).toEqual([
      ['yolov5n', 'downloaded'],
      ['yolov5s', 'downloaded'],
    ])
    expect(fs.readFileSync(path.join(modelsDir, 'yolov5n.onnx'))).toEqual(Buffer.from(BODY))
    expect(fs.readFileSync(path.join(modelsDir, 'yolov5s.onnx'))).toEqual(Buffer.from(BODY))
    expect(src.calls).toEqual(Object.keys(TWO_BODIES))

    const labels = fs
      .readFileSync(path.join(modelsDir, 'coco80.txt'), 'utf8')
      .trimEnd()
      .split(/\r?\n/)
    expect(COCO80_LABELS).toHaveLength(80)
    expect(labels).toEqual(COCO80_LABELS)
    expect(labels[0]).toBe('person')
    expect(labels[79]).toBe('toothbrush')

    const yml = fs.readFileSync(path.join(modelsDir, 'models.yml'), 'utf8')
    expect(yml.startsWith('%YAML:1.0')).toBe(true)
    expect(yml).toContain('yolov5n:')
    expect(yml).toContain('yolov5s:')
    expect(yml).toContain('  model: yolov5n.onnx')
    expect(yml).toContain('  postprocessing: yolov5')
    expect(yml).toContain('  labels: coco80.txt')

    const status = dnnDownloadStatus()
    expect(status.state).toBe('done')
    expect(status.ids).toEqual(['yolov5n', 'yolov5s'])
  })

  it('deletes .part and records a checksum failure instead of promoting the file', async () => {
    const home = tempDir()
    const bad = pin({ sha256: 'f'.repeat(64) })
    const result = await downloadDnnPins(['yolov5n'], {
      home,
      pins: [bad],
      fetchBytes: async () => BODY,
    })
    expect(result.ok).toBe(false)
    expect(result.items[0]).toMatchObject({
      id: 'yolov5n',
      state: 'failed',
      code: 'dnn_checksum_mismatch',
    })
    const modelsDir = path.join(home, 'vision', 'models')
    expect(fs.readdirSync(modelsDir)).toEqual([])
  })

  it('is idempotent on repeated downloads and never duplicates models.yml entries', async () => {
    const home = tempDir()
    const src = fetchReturning(TWO_BODIES)
    const first = await downloadDnnPins(['yolov5n'], {
      home,
      pins: [pin()],
      fetchBytes: src.fetchBytes,
    })
    expect(first.ok).toBe(true)
    const ymlAfterFirst = fs.readFileSync(path.join(home, 'vision', 'models', 'models.yml'), 'utf8')
    const second = await downloadDnnPins(['yolov5n'], {
      home,
      pins: [pin()],
      fetchBytes: src.fetchBytes,
    })
    expect(second.ok).toBe(true)
    expect(second.items[0].state).toBe('already')
    expect(src.calls).toHaveLength(1)
    const ymlAfterSecond = fs.readFileSync(
      path.join(home, 'vision', 'models', 'models.yml'),
      'utf8',
    )
    expect(ymlAfterSecond).toBe(ymlAfterFirst)
    expect(ymlAfterSecond.match(/^yolov5n:/gm)).toHaveLength(1)
  })

  it('never overwrites an existing models.yml key', async () => {
    const home = tempDir()
    const modelsDir = path.join(home, 'vision', 'models')
    fs.mkdirSync(modelsDir, { recursive: true })
    fs.writeFileSync(
      path.join(modelsDir, 'models.yml'),
      '%YAML:1.0\n---\nyolov5n:\n  model: custom.onnx\n  width: 320\n  height: 320\n  postprocessing: yolov5\n',
    )
    const result = await downloadDnnPins(['yolov5n'], {
      home,
      pins: [pin()],
      fetchBytes: async () => BODY,
    })
    expect(result.ok).toBe(true)
    const yml = fs.readFileSync(path.join(modelsDir, 'models.yml'), 'utf8')
    expect(yml).toContain('  model: custom.onnx')
    expect(yml).not.toContain('  model: yolov5n.onnx')
    expect(yml.match(/^yolov5n:/gm)).toHaveLength(1)
  })

  it('rejects a second concurrent download', async () => {
    const home = tempDir()
    let release: (bytes: Uint8Array) => void = () => {}
    const gate = new Promise<Uint8Array>((resolve) => {
      release = resolve
    })
    const first = downloadDnnPins(['yolov5n'], {
      home,
      pins: [pin()],
      fetchBytes: async () => gate,
    })
    const second = await downloadDnnPins(['yolov5s'], {
      home,
      pins: [
        pin({ id: 'yolov5s', filename: 'yolov5s.onnx', url: 'https://example.test/yolov5s.onnx' }),
      ],
      fetchBytes: async () => BODY,
    })
    expect(second.ok).toBe(false)
    expect(second.code).toBe('download_busy')
    release(BODY)
    expect((await first).ok).toBe(true)
  })

  it('cleans up .part on transport failure and reports the failed item', async () => {
    const home = tempDir()
    const result = await downloadDnnPins(['yolov5n'], {
      home,
      pins: [pin()],
      fetchBytes: async () => {
        throw new Error('network down')
      },
    })
    expect(result.ok).toBe(false)
    expect(result.items[0]).toMatchObject({
      id: 'yolov5n',
      state: 'failed',
      code: 'dnn_download_failed',
    })
    const modelsDir = path.join(home, 'vision', 'models')
    expect(fs.readdirSync(modelsDir)).toEqual([])
  })

  it('re-hashes a same-size existing file and repairs a corrupt copy instead of reporting already', async () => {
    const home = tempDir()
    const modelsDir = path.join(home, 'vision', 'models')
    fs.mkdirSync(modelsDir, { recursive: true })
    // Same byte length as the pin, wrong content: size alone used to pass.
    fs.writeFileSync(path.join(modelsDir, 'yolov5n.onnx'), 'evil')
    const src = fetchReturning({ 'https://example.test/yolov5n.onnx': BODY })
    const result = await downloadDnnPins(['yolov5n'], {
      home,
      pins: [pin()],
      fetchBytes: src.fetchBytes,
    })
    expect(result.ok).toBe(true)
    expect(result.items[0]).toMatchObject({ id: 'yolov5n', state: 'downloaded' })
    expect(src.calls).toHaveLength(1)
    expect(fs.readFileSync(path.join(modelsDir, 'yolov5n.onnx'))).toEqual(Buffer.from(BODY))
  })

  it('keeps a genuinely identical existing file without fetching', async () => {
    const home = tempDir()
    const modelsDir = path.join(home, 'vision', 'models')
    fs.mkdirSync(modelsDir, { recursive: true })
    fs.writeFileSync(path.join(modelsDir, 'yolov5n.onnx'), BODY)
    const src = fetchReturning(TWO_BODIES)
    const result = await downloadDnnPins(['yolov5n'], {
      home,
      pins: [pin()],
      fetchBytes: src.fetchBytes,
    })
    expect(result.items[0].state).toBe('already')
    expect(src.calls).toHaveLength(0)
  })

  it('rejects unknown or path-like model ids before any fetch', async () => {
    const home = tempDir()
    for (const id of ['yolov5', '../yolov5n', 'yolov5n/x', '']) {
      const result = await downloadDnnPins([id], {
        home,
        pins: [pin()],
        fetchBytes: async () => {
          throw new Error('must not fetch')
        },
      })
      expect(result.ok).toBe(false)
      expect(result.code).toBe('invalid_model_id')
    }
  })

  it('appends entries without touching unrelated keys and keeps line endings', () => {
    const existing =
      'my-detector:\r\n  model: my.onnx\r\n  width: 640\r\n  height: 640\r\n  postprocessing: yolov5\r\n'
    const out = appendModelsYamlEntries(existing, ['yolov5n'])
    expect(out).toContain('my.onnx')
    expect(out).toContain('yolov5n:')
    expect(out.includes('\r\n')).toBe(true)
    expect(out.endsWith('\r\n')).toBe(true)
    const empty = appendModelsYamlEntries('', ['yolov5n'])
    expect(empty.startsWith('%YAML:1.0\n---\n')).toBe(true)
    expect(appendModelsYamlEntries(out, ['yolov5n'])).toBe(out)
  })
})
