/**
 * Fixed YOLOv5 v7.0 ONNX presets for the optional DNN feature.
 *
 * The download URL, byte size and sha256 are pinned here — never read from
 * models.yml, user input or the network at request time. The host downloads
 * only these five URLs, after an explicit human confirmation in the DSH
 * config card, and the worker never downloads anything.
 *
 * The bytes/sha256 below were measured from the official v7.0 release assets
 * (github.com/ultralytics/yolov5/releases/download/v7.0/<id>.onnx) and must
 * never be mixed with values from another release.
 *
 * License: the YOLOv5 v7.0 assets are GPL-3.0. The config card discloses it;
 * the plugin does not redistribute the weights.
 */

export const DNN_PIN_SOURCE = 'ultralytics/yolov5 v7.0'
export const DNN_PIN_LICENSE = 'GPL-3.0'

export type DnnModelId = 'yolov5n' | 'yolov5s' | 'yolov5m' | 'yolov5l' | 'yolov5x'

export interface DnnPin {
  id: DnnModelId
  filename: string
  url: string
  /** Exact file size in bytes; null while the pin is not verified. */
  bytes: number | null
  /** Lower-case hex sha256; null while the pin is not verified. */
  sha256: string | null
  source: string
  license: string
}

const RELEASE = 'https://github.com/ultralytics/yolov5/releases/download/v7.0'

function preset(id: DnnModelId, bytes: number, sha256: string): DnnPin {
  return {
    id,
    filename: id + '.onnx',
    url: RELEASE + '/' + id + '.onnx',
    bytes,
    sha256,
    source: DNN_PIN_SOURCE,
    license: DNN_PIN_LICENSE,
  }
}

export const DNN_PINS: readonly DnnPin[] = [
  preset('yolov5n', 3981910, '04f0e55c26f58d17145b36045780fe1250d5bd2187543e11568e5141d05b3262'),
  preset('yolov5s', 14698981, '8bc25a47abdbe2fd23e30eb948746bf856b031ea3cc815166687f52db9f8c659'),
  preset('yolov5m', 42606277, 'c46186375067d43cf479d014ce8cd3af4c1489fb13b4ebf4e2470d9def12f7d9'),
  preset('yolov5l', 93344111, '6d354c552d2562a020b24f695b75abed9e4298781eba3c9a2d55c9738479dd0c'),
  preset('yolov5x', 173701522, '81dc04d46512126d1545b7898abf9451526be7abdf4a748a48185dd1ccec42f8'),
]

const PIN_BY_ID = new Map<string, DnnPin>(DNN_PINS.map((pin) => [pin.id, pin]))

export function pinById(id: string): DnnPin | undefined {
  return PIN_BY_ID.get(id)
}

/** A pin must carry BOTH the exact size and the sha256 before it may download. */
export function pinIsVerified(pin: DnnPin): boolean {
  return (
    typeof pin.bytes === 'number' &&
    Number.isSafeInteger(pin.bytes) &&
    pin.bytes > 0 &&
    typeof pin.sha256 === 'string' &&
    /^[0-9a-f]{64}$/.test(pin.sha256)
  )
}

export interface DnnPinReportRow {
  id: string
  filename: string
  url: string
  bytes: number | null
  sha256: string | null
  verified: boolean
  source: string
  license: string
}

/** Stable projection for /doctor, dnn.status and the config card. */
export function dnnPinReport(): DnnPinReportRow[] {
  return DNN_PINS.map((pin) => ({
    id: pin.id,
    filename: pin.filename,
    url: pin.url,
    bytes: pin.bytes,
    sha256: pin.sha256,
    verified: pinIsVerified(pin),
    source: pin.source,
    license: pin.license,
  }))
}
