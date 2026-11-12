import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { afterEach, describe, expect, it } from 'vitest'

import {
  MODELS_YML,
  listConfiguredModelIds,
  modelsSnapshot,
  scanModelFiles,
  scanVisionModels,
  visionModelsDir,
} from '../../../runtime/vision/models.js'

const homes: string[] = []

function tempHome(): string {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'dsh-vision-models-'))
  homes.push(dir)
  return dir
}

function modelsDir(home: string): string {
  const dir = path.join(home, 'vision', 'models')
  fs.mkdirSync(dir, { recursive: true })
  return dir
}

afterEach(() => {
  while (homes.length > 0) {
    const dir = homes.pop() as string
    fs.rmSync(dir, { recursive: true, force: true })
  }
})

describe('vision models directory', () => {
  it('resolves the models dir under home', () => {
    expect(visionModelsDir('C:/home')).toBe(path.join('C:/home', 'vision', 'models'))
  })

  it('returns an empty scan when the directory is missing', () => {
    const home = tempHome()
    const scan = scanVisionModels(home)
    expect(scan.dir).toBe(path.join(home, 'vision', 'models'))
    expect(scan.exists).toBe(false)
    expect(scan.modelsYml).toBe(false)
    expect(scan.onnxCount).toBe(0)
    expect(scan.files).toEqual([])
    expect(scan.names).toEqual([])
  })

  it('scans .onnx files recursively and case-insensitively', () => {
    const home = tempHome()
    const dir = modelsDir(home)
    fs.writeFileSync(path.join(dir, 'yolov5n.onnx'), 'x')
    fs.mkdirSync(path.join(dir, 'custom'))
    fs.writeFileSync(path.join(dir, 'custom', 'MyModel.ONNX'), 'xx')
    fs.writeFileSync(path.join(dir, 'notes.txt'), 'nope')
    const files = scanModelFiles(dir)
    expect(files.map((f) => f.relativePath).sort()).toEqual(['custom/MyModel.ONNX', 'yolov5n.onnx'])
    expect(files.find((f) => f.relativePath === 'yolov5n.onnx')?.size).toBe(1)
    const scan = scanVisionModels(home)
    expect(scan.exists).toBe(true)
    expect(scan.onnxCount).toBe(2)
    expect(scan.names.sort()).toEqual(['MyModel', 'yolov5n'])
  })

  it('reports models.yml presence and a bounded snapshot', () => {
    const home = tempHome()
    const dir = modelsDir(home)
    fs.writeFileSync(path.join(dir, MODELS_YML), 'yolov5n:\n  model: yolov5n.onnx\n')
    fs.writeFileSync(path.join(dir, 'yolov5n.onnx'), 'x')
    const snapshot = modelsSnapshot(home)
    expect(snapshot).toEqual({
      dir,
      modelsYml: true,
      onnxCount: 1,
      names: ['yolov5n'],
    })
  })

  it('does not throw when a model directory is unreadable', () => {
    const home = tempHome()
    modelsDir(home)
    expect(() => scanModelFiles(path.join(home, 'vision', 'models', 'missing'))).not.toThrow()
    expect(scanModelFiles(path.join(home, 'vision', 'models', 'missing'))).toEqual([])
  })
})

describe('listConfiguredModelIds', () => {
  it('returns [] when models.yml is missing', () => {
    const home = tempHome()
    fs.mkdirSync(path.join(home, 'vision', 'models'), { recursive: true })
    expect(listConfiguredModelIds(home)).toEqual([])
    expect(listConfiguredModelIds(path.join(home, 'nope'))).toEqual([])
  })

  it('reads top-level ids while skipping comments, directives and nested keys', () => {
    const home = tempHome()
    const dir = modelsDir(home)
    fs.writeFileSync(
      path.join(dir, MODELS_YML),
      [
        '%YAML:1.0',
        '---',
        '# comment',
        'yolov5n:',
        '  model: yolov5n.onnx',
        '  width: 640',
        'my-detector:   # inline comment',
        '  model: custom/my.onnx',
        '  labels: labels.txt',
        '   ',
        '...',
        '',
      ].join('\n'),
    )
    expect(listConfiguredModelIds(home)).toEqual(['yolov5n', 'my-detector'])
  })

  it('deduplicates duplicate keys but keeps first-seen order', () => {
    const home = tempHome()
    const dir = modelsDir(home)
    fs.writeFileSync(
      path.join(dir, MODELS_YML),
      'b:\n  model: b.onnx\na:\n  model: a.onnx\nb:\n  model: b2.onnx\n',
    )
    expect(listConfiguredModelIds(home)).toEqual(['b', 'a'])
  })

  it('ignores keys that are not valid model ids', () => {
    const home = tempHome()
    const dir = modelsDir(home)
    fs.writeFileSync(
      path.join(dir, MODELS_YML),
      'bad/id:\n  model: x.onnx\nyolov5n:\n  model: y.onnx\n: empty\n',
    )
    expect(listConfiguredModelIds(home)).toEqual(['yolov5n'])
  })

  it('returns [] for an empty or unreadable models.yml', () => {
    const home = tempHome()
    const dir = modelsDir(home)
    fs.writeFileSync(path.join(dir, MODELS_YML), '')
    expect(listConfiguredModelIds(home)).toEqual([])
    fs.rmSync(path.join(dir, MODELS_YML))
    fs.mkdirSync(path.join(dir, MODELS_YML))
    expect(listConfiguredModelIds(home)).toEqual([])
  })
})
