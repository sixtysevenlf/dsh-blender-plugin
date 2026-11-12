/**
 * Host-side view of the vision model directory.
 *
 * The host never interprets models.yml: the worker parses and validates it with
 * cv2.FileStorage (DNN design). This module only scans *.onnx files and, for
 * status/doctor display, extracts top-level model ids with a deliberately
 * conservative line scan. listConfiguredModelIds is advisory only and must not
 * be used for inference or validation.
 */
import fs from 'node:fs'
import path from 'node:path'

export const VISION_DIR = 'vision'
export const MODELS_DIR = 'models'
export const MODELS_YML = 'models.yml'

/** Model ids are keys in models.yml; they are never paths or URLs. */
export const MODEL_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/

export interface VisionModelFile {
  /** Path relative to the models directory, using forward slashes. */
  relativePath: string
  /** File name without the .onnx suffix. */
  name: string
  size: number
}

export interface VisionModelsScan {
  dir: string
  exists: boolean
  modelsYml: boolean
  onnxCount: number
  files: VisionModelFile[]
  names: string[]
}

export interface VisionModelsSnapshot {
  dir: string
  modelsYml: boolean
  onnxCount: number
  names: string[]
}

export function visionModelsDir(homeDir: string): string {
  return path.join(homeDir, VISION_DIR, MODELS_DIR)
}

export function isModelId(value: string): boolean {
  return MODEL_ID_PATTERN.test(value)
}

function walk(dir: string, relative: string, out: VisionModelFile[]): void {
  let entries: fs.Dirent[]
  try {
    entries = fs.readdirSync(dir, { withFileTypes: true })
  } catch {
    return
  }
  entries.sort((left, right) => left.name.localeCompare(right.name))
  for (const entry of entries) {
    // Symlinks are skipped: the model directory must stay self-contained.
    if (entry.isSymbolicLink()) continue
    const absolute = path.join(dir, entry.name)
    const nextRelative = relative === '' ? entry.name : relative + '/' + entry.name
    if (entry.isDirectory()) {
      walk(absolute, nextRelative, out)
      continue
    }
    if (!entry.isFile()) continue
    if (!entry.name.toLowerCase().endsWith('.onnx')) continue
    let size: number
    try {
      size = fs.statSync(absolute).size
    } catch {
      size = 0
    }
    out.push({ relativePath: nextRelative, name: entry.name.slice(0, -'.onnx'.length), size })
  }
}

/** Recursively lists .onnx files; unreadable or missing directories yield []. */
export function scanModelFiles(modelsDir: string): VisionModelFile[] {
  const files: VisionModelFile[] = []
  walk(modelsDir, '', files)
  return files
}

export function scanVisionModels(homeDir: string): VisionModelsScan {
  const dir = visionModelsDir(homeDir)
  let exists: boolean
  try {
    exists = fs.statSync(dir).isDirectory()
  } catch {
    exists = false
  }
  const files = scanModelFiles(dir)
  const names: string[] = []
  const seen = new Set<string>()
  for (const file of files) {
    if (seen.has(file.name)) continue
    seen.add(file.name)
    names.push(file.name)
  }
  let modelsYml: boolean
  try {
    modelsYml = fs.statSync(path.join(dir, MODELS_YML)).isFile()
  } catch {
    modelsYml = false
  }
  return { dir, exists, modelsYml, onnxCount: files.length, files, names }
}

/** Bounded snapshot used by vision_status and /doctor. */
export function modelsSnapshot(homeDir: string): VisionModelsSnapshot {
  const scan = scanVisionModels(homeDir)
  return { dir: scan.dir, modelsYml: scan.modelsYml, onnxCount: scan.onnxCount, names: scan.names }
}

const TOP_LEVEL_KEY = /^([A-Za-z0-9][A-Za-z0-9._-]{0,63}):\s*(?:#.*)?$/

/**
 * Advisory list of models.yml top-level ids for status display.
 *
 * This is a line scan, not a YAML parser: quoted keys, flow mappings and any
 * non-ASCII ids are intentionally ignored, and the worker remains the single
 * authority for whether a model is usable.
 */
export function listConfiguredModelIds(homeDir: string): string[] {
  let text: string
  try {
    text = fs.readFileSync(path.join(visionModelsDir(homeDir), MODELS_YML), 'utf8')
  } catch {
    return []
  }
  const ids: string[] = []
  const seen = new Set<string>()
  const lines = text.replace(/^\uFEFF/, '').split('\n')
  for (const raw of lines) {
    const line = raw.endsWith('\r') ? raw.slice(0, -1) : raw
    if (line === '' || line.trim() === '') continue
    if (line !== line.trimStart()) continue
    if (
      line.startsWith('#') ||
      line.startsWith('%') ||
      line.startsWith('---') ||
      line.startsWith('...')
    )
      continue
    const match = TOP_LEVEL_KEY.exec(line)
    if (!match) continue
    const id = match[1] as string
    if (seen.has(id)) continue
    seen.add(id)
    ids.push(id)
  }
  return ids
}
