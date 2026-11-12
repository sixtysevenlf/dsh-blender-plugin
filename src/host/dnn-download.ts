/**
 * Human-confirmed download of the fixed YOLOv5 v7.0 presets (DNN design).
 *
 * Rules enforced here:
 * - only the five ids in dnn-pins.ts, and only pins that are VERIFIED
 *   (exact byte size + sha256) may be fetched; an unverified pin fails with
 *   `dnn_pin_unavailable` before any filesystem access;
 * - single concurrency per host process;
 * - write `<id>.onnx.part`, verify sha256 and byte size, then atomically
 *   rename to `<id>.onnx`; failures delete the part file;
 * - idempotent: a byte-identical existing model is reported as `already`;
 * - write coco80.txt (missing only) and append models.yml entries without
 *   overwriting an existing top-level key.
 *
 * The worker never downloads anything; this module is the only network path
 * in the DNN feature and it is triggered only by an explicit RPC from the
 * human-facing config card.
 */
import { createHash } from 'node:crypto'
import fs from 'node:fs'
import path from 'node:path'
import { dnnPaths, getDnnHome } from './dnn-home.js'
import { DNN_PINS, pinById, pinIsVerified, type DnnPin } from './dnn-pins.js'

export const MODELS_YML_HEADER = '%YAML:1.0\n---\n'
export const COCO80_LABELS: readonly string[] = [
  'person',
  'bicycle',
  'car',
  'motorcycle',
  'airplane',
  'bus',
  'train',
  'truck',
  'boat',
  'traffic light',
  'fire hydrant',
  'stop sign',
  'parking meter',
  'bench',
  'bird',
  'cat',
  'dog',
  'horse',
  'sheep',
  'cow',
  'elephant',
  'bear',
  'zebra',
  'giraffe',
  'backpack',
  'umbrella',
  'handbag',
  'tie',
  'suitcase',
  'frisbee',
  'skis',
  'snowboard',
  'sports ball',
  'kite',
  'baseball bat',
  'baseball glove',
  'skateboard',
  'surfboard',
  'tennis racket',
  'bottle',
  'wine glass',
  'cup',
  'fork',
  'knife',
  'spoon',
  'bowl',
  'banana',
  'apple',
  'sandwich',
  'orange',
  'broccoli',
  'carrot',
  'hot dog',
  'pizza',
  'donut',
  'cake',
  'chair',
  'couch',
  'potted plant',
  'bed',
  'dining table',
  'toilet',
  'tv',
  'laptop',
  'mouse',
  'remote',
  'keyboard',
  'cell phone',
  'microwave',
  'oven',
  'toaster',
  'sink',
  'refrigerator',
  'book',
  'clock',
  'vase',
  'scissors',
  'teddy bear',
  'hair drier',
  'toothbrush',
] as const

const MODEL_ID_RE = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/

export interface DownloadItem {
  id: string
  state: 'downloaded' | 'already' | 'failed'
  path?: string
  bytes?: number
  code?: string
  message?: string
}

export interface DownloadOutcome {
  ok: boolean
  code?: string
  message?: string
  items: DownloadItem[]
  modelsDir: string
  modelsYml?: string
}

export interface DownloadDeps {
  home?: string
  modelsDir?: string
  /** Test seam: replace the pinned preset table. */
  pins?: readonly DnnPin[]
  /** Test seam: return the file body for a fixed URL. */
  fetchBytes?: (url: string) => Promise<Uint8Array>
}

export interface DownloadStatus {
  state: 'idle' | 'running' | 'done' | 'failed'
  ids: string[]
  startedAt?: number
  finishedAt?: number
  lastError?: { code: string; message: string } | null
  items: DownloadItem[]
}

let active = false
let state: DownloadStatus = { state: 'idle', ids: [], items: [], lastError: null }

export function dnnDownloadStatus(): DownloadStatus {
  return {
    state: state.state,
    ids: state.ids.slice(),
    startedAt: state.startedAt,
    finishedAt: state.finishedAt,
    lastError: state.lastError ? { ...state.lastError } : null,
    items: state.items.map((item) => ({ ...item })),
  }
}

export function resetDnnDownloadForTests(): void {
  active = false
  state = { state: 'idle', ids: [], items: [], lastError: null }
}

function failure(code: string, message: string, modelsDir = ''): DownloadOutcome {
  state = {
    ...state,
    state: 'failed',
    finishedAt: Date.now(),
    lastError: { code, message },
  }
  return { ok: false, code, message, items: [], modelsDir }
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

function yamlKeyPresent(existing: string, id: string): boolean {
  return new RegExp('^' + escapeRegExp(id) + '\\s*:', 'm').test(existing)
}

function entryBlock(id: string, eol: string): string {
  return (
    [
      id + ':',
      '  model: ' + id + '.onnx',
      '  mean: 0.0',
      '  scale: 0.00392',
      '  width: 640',
      '  height: 640',
      '  rgb: true',
      '  labels: coco80.txt',
      '  postprocessing: yolov5',
    ].join(eol) + eol
  )
}

/** Pure append: existing keys win, header only for a fresh file, EOL preserved. */
export function appendModelsYamlEntries(existing: string, ids: readonly string[]): string {
  const eol = existing.includes('\r\n') ? '\r\n' : '\n'
  const missing = ids.filter((id) => !yamlKeyPresent(existing, id))
  if (missing.length === 0) return existing
  let out = existing
  if (out.length === 0) out = MODELS_YML_HEADER.replace(/\n/g, eol)
  else if (!out.endsWith('\n')) out += eol
  for (const id of missing) out += entryBlock(id, eol)
  return out
}

function ensureCoco80(modelsDir: string): void {
  const target = path.join(modelsDir, 'coco80.txt')
  if (fs.existsSync(target)) return
  fs.writeFileSync(target, COCO80_LABELS.join('\n') + '\n', 'utf8')
}

function sha256(bytes: Uint8Array): string {
  return createHash('sha256').update(bytes).digest('hex')
}

function defaultFetch(url: string): Promise<Uint8Array> {
  return fetch(url).then(async (response) => {
    if (!response.ok) throw new Error('HTTP ' + String(response.status) + ' for ' + url)
    return new Uint8Array(await response.arrayBuffer())
  })
}

/** Fetch and verify one pin: existing byte-identical file is `already`, else download+sha256+atomic rename. */
async function fetchOnePin(
  pin: DnnPin,
  fetchBytes: (url: string) => Promise<Uint8Array>,
  modelsDir: string,
): Promise<DownloadItem> {
  const finalPath = path.join(modelsDir, pin.filename)
  try {
    // Size alone proves nothing: hash an existing same-size file before claiming
    // `already`, otherwise a truncated/corrupt download is never repaired.
    if (fs.existsSync(finalPath) && fs.statSync(finalPath).size === pin.bytes) {
      const existing = fs.readFileSync(finalPath)
      if (sha256(existing) === pin.sha256) {
        return { id: pin.id, state: 'already', path: finalPath, bytes: pin.bytes }
      }
    }
    const bytes = await fetchBytes(pin.url)
    const partPath = finalPath + '.part'
    try {
      fs.writeFileSync(partPath, bytes)
      const digest = sha256(bytes)
      if (bytes.byteLength !== pin.bytes) {
        throw Object.assign(
          new Error(
            'size mismatch: expected ' + String(pin.bytes) + ', got ' + String(bytes.byteLength),
          ),
          { dnnCode: 'dnn_checksum_mismatch' },
        )
      }
      if (digest !== pin.sha256) {
        throw Object.assign(
          new Error('sha256 mismatch: expected ' + String(pin.sha256) + ', got ' + digest),
          { dnnCode: 'dnn_checksum_mismatch' },
        )
      }
      fs.renameSync(partPath, finalPath)
    } catch (error) {
      try {
        fs.unlinkSync(partPath)
      } catch {
        /* part already gone */
      }
      throw error
    }
    return { id: pin.id, state: 'downloaded', path: finalPath, bytes: pin.bytes }
  } catch (error) {
    const code = (error as { dnnCode?: string }).dnnCode ?? 'dnn_download_failed'
    return {
      id: pin.id,
      state: 'failed',
      code,
      message: error instanceof Error ? error.message : String(error),
    }
  }
}

/** Write coco80.txt and append models.yml entries for every successfully fetched id; returns the yml path. */
function writeModelsIndex(modelsDir: string, items: DownloadItem[]): string {
  const ymlPath = path.join(modelsDir, 'models.yml')
  const usable = items.filter((item) => item.state !== 'failed').map((item) => item.id)
  if (usable.length === 0) return ymlPath
  ensureCoco80(modelsDir)
  const before = fs.existsSync(ymlPath) ? fs.readFileSync(ymlPath, 'utf8') : ''
  const after = appendModelsYamlEntries(before, usable)
  if (after !== before) fs.writeFileSync(ymlPath, after, 'utf8')
  return ymlPath
}

export async function downloadDnnPins(
  ids: readonly string[],
  deps: DownloadDeps = {},
): Promise<DownloadOutcome> {
  const modelsDir = deps.modelsDir ?? dnnPaths(deps.home ?? getDnnHome()).modelsDir
  if (active) {
    return {
      ok: false,
      code: 'download_busy',
      message: '已有下载任务在进行，请等待完成后再试',
      items: [],
      modelsDir,
    }
  }
  if (ids.length === 0) return failure('invalid_params', '至少选择一个模型', modelsDir)
  const pins = deps.pins ?? DNN_PINS
  const byId = new Map<string, DnnPin>(pins.map((pin) => [String(pin.id), pin]))
  for (const id of ids) {
    if (!MODEL_ID_RE.test(id) || !byId.has(id) || !pinById(id)) {
      return failure('invalid_model_id', '未知模型 id: ' + JSON.stringify(id), modelsDir)
    }
  }
  const unverified = ids.filter((id) => !pinIsVerified(byId.get(id) as DnnPin))
  if (unverified.length > 0) {
    return failure(
      'dnn_pin_unavailable',
      '预设校验值未采集，暂不能下载: ' +
        unverified.join(', ') +
        '（见 src/host/dnn-pins.ts 顶部说明）',
      modelsDir,
    )
  }

  active = true
  state = { state: 'running', ids: ids.slice(), items: [], startedAt: Date.now(), lastError: null }
  const items: DownloadItem[] = []
  try {
    fs.mkdirSync(modelsDir, { recursive: true })
    const fetchBytes = deps.fetchBytes ?? defaultFetch
    for (const id of ids) {
      items.push(await fetchOnePin(byId.get(id) as DnnPin, fetchBytes, modelsDir))
    }
    const modelsYml = writeModelsIndex(modelsDir, items)
    const failed = items.find((item) => item.state === 'failed')
    const ok = !failed
    state = {
      state: ok ? 'done' : 'failed',
      ids: ids.slice(),
      items: items.map((item) => ({ ...item })),
      startedAt: state.startedAt,
      finishedAt: Date.now(),
      lastError: failed
        ? { code: failed.code ?? 'dnn_download_failed', message: failed.message ?? '' }
        : null,
    }
    return {
      ok,
      code: failed?.code,
      message: failed?.message,
      items,
      modelsDir,
      modelsYml,
    }
  } catch (error) {
    const code = 'dnn_download_failed'
    const message = error instanceof Error ? error.message : String(error)
    state = {
      state: 'failed',
      ids: ids.slice(),
      items: items.map((item) => ({ ...item })),
      startedAt: state.startedAt,
      finishedAt: Date.now(),
      lastError: { code, message },
    }
    return { ok: false, code, message, items, modelsDir }
  } finally {
    active = false
  }
}
