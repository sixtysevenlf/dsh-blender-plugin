/**
 * vision_* 参数校验：只接受「已存在的本地图片路径 / 有界整数 / 枚举」，不接受 URL、
 * 通配符或脚本。校验失败统一返回 input 阶段的结构化失败（由 dispatch.ts 组装），
 * worker 侧仍会再校验一遍——这里的目的是尽量在 host 侧早失败，并让错误可读。
 */
import fs from 'node:fs'
import path from 'node:path'
import { isModelId } from './models.js'

export interface Roi {
  x: number
  y: number
  w: number
  h: number
}

export type Checked<T> =
  { ok: true; value: T } | { ok: false; message: string; details?: Record<string, unknown> }

/** 64 MP 解码上限（vision 设计）：超过返回 vision_image_too_large。 */
export const MAX_IMAGE_PIXELS = 64_000_000
/** analyze/compare/detect 的 maxContours / maxDetections 上限。 */
export const MAX_CONTOURS = 32
export const MAX_LINES = 32
export const MAX_DETECTIONS = 100
export const MAX_TRACK_FRAMES = 120
export const MAX_TRACK_POINTS = 20

const GLOB_CHARS = /[*?[\]{}]/

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

/** 图片路径：非空字符串、无 URL/通配、解析后是已存在文件。 */
export function checkImagePath(
  raw: unknown,
  cwd: string,
  label = 'path',
): Checked<{ path: string; absolute: string }> {
  if (typeof raw !== 'string' || !raw.trim()) {
    return {
      ok: false,
      message: label + ' 必须是已存在的图片文件路径（字符串）',
      details: { [label]: raw },
    }
  }
  const value = raw.trim()
  if (value.includes('://')) {
    return {
      ok: false,
      message: label + ' 不接受 URL，只接受本地文件路径',
      details: { [label]: value },
    }
  }
  if (GLOB_CHARS.test(value)) {
    return {
      ok: false,
      message: label + ' 不接受通配符或脚本表达式，只接受一个具体文件',
      details: { [label]: value },
    }
  }
  const absolute = path.resolve(cwd, value)
  let stat: fs.Stats
  try {
    stat = fs.statSync(absolute)
  } catch (e) {
    return {
      ok: false,
      message: label + ' 文件不存在：' + absolute,
      details: { [label]: value, absolute, reason: e instanceof Error ? e.message : String(e) },
    }
  }
  if (!stat.isFile()) {
    return {
      ok: false,
      message: label + ' 不是文件：' + absolute,
      details: { [label]: value, absolute },
    }
  }
  return { ok: true, value: { path: value, absolute } }
}

/** 可选 ROI：{x,y,w,h} 非负整数、w/h>0；未提供返回 null。 */
export function checkRoi(raw: unknown): Checked<Roi | null> {
  if (raw === undefined || raw === null) return { ok: true, value: null }
  if (!isPlainObject(raw))
    return { ok: false, message: 'roi 必须是 {x,y,w,h} 对象', details: { roi: raw } }
  const { x, y, w, h } = raw
  if (
    !Number.isInteger(x) ||
    !Number.isInteger(y) ||
    !Number.isInteger(w) ||
    !Number.isInteger(h)
  ) {
    return { ok: false, message: 'roi 的 x/y/w/h 必须是整数像素', details: { roi: raw } }
  }
  if ((x as number) < 0 || (y as number) < 0 || (w as number) <= 0 || (h as number) <= 0) {
    return { ok: false, message: 'roi 要求 x/y>=0 且 w/h>0', details: { roi: raw } }
  }
  return { ok: true, value: { x: x as number, y: y as number, w: w as number, h: h as number } }
}

/** 有界整数；未提供返回 fallback。 */
export function checkIntInRange(
  raw: unknown,
  min: number,
  max: number,
  fallback: number,
  label: string,
): Checked<number> {
  if (raw === undefined || raw === null) return { ok: true, value: fallback }
  if (typeof raw !== 'number' || !Number.isInteger(raw) || raw < min || raw > max) {
    return {
      ok: false,
      message: label + ' 必须是 ' + String(min) + '–' + String(max) + ' 的整数',
      details: { [label]: raw },
    }
  }
  return { ok: true, value: raw }
}

/** 0–1 浮点；未提供返回 fallback。 */
export function checkUnitFloat(raw: unknown, fallback: number, label: string): Checked<number> {
  if (raw === undefined || raw === null) return { ok: true, value: fallback }
  if (typeof raw !== 'number' || !Number.isFinite(raw) || raw < 0 || raw > 1) {
    return { ok: false, message: label + ' 必须是 0–1 之间的数字', details: { [label]: raw } }
  }
  return { ok: true, value: raw }
}

export function checkEnum<T extends string>(
  raw: unknown,
  allowed: readonly T[],
  fallback: T,
  label: string,
): Checked<T> {
  if (raw === undefined || raw === null) return { ok: true, value: fallback }
  if (typeof raw !== 'string' || !(allowed as readonly string[]).includes(raw)) {
    return {
      ok: false,
      message: label + ' 只能是 ' + allowed.join(' | '),
      details: { [label]: raw, allowed: [...allowed] },
    }
  }
  return { ok: true, value: raw as T }
}

/**
 * 可选模型 id：只接受 models.yml 的 key（不是路径/URL）。
 * 非法 id 的 details 带 `reason: 'invalid_model_id'`，dispatch 据此映射 vision_model_unsupported。
 */
export function checkOptionalId(raw: unknown, label: string): Checked<string | null> {
  if (raw === undefined || raw === null) return { ok: true, value: null }
  if (typeof raw !== 'string' || !raw.trim()) {
    return { ok: false, message: label + ' 必须是非空字符串', details: { [label]: raw } }
  }
  const value = raw.trim()
  if (!isModelId(value)) {
    return {
      ok: false,
      message:
        label +
        ' 必须是模型 id（^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$，models.yml 的 key），不接受路径或 URL：' +
        value,
      details: {
        [label]: value,
        reason: 'invalid_model_id',
        pattern: '^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$',
      },
    }
  }
  return { ok: true, value }
}

/** 1–120 个已存在帧文件；manifest 路径与图片同等处理（worker 决定语义）。 */
export function checkFramePaths(raw: unknown, cwd: string): Checked<string[]> {
  if (!Array.isArray(raw) || raw.length === 0) {
    return {
      ok: false,
      message: 'frames 必须是 1–' + String(MAX_TRACK_FRAMES) + ' 个已存在文件路径的数组',
      details: { frames: raw },
    }
  }
  if (raw.length > MAX_TRACK_FRAMES) {
    return {
      ok: false,
      message: 'frames 最多 ' + String(MAX_TRACK_FRAMES) + ' 帧，收到 ' + String(raw.length),
      details: { count: raw.length, limit: MAX_TRACK_FRAMES },
    }
  }
  const out: string[] = []
  for (let i = 0; i < raw.length; i += 1) {
    const checked = checkImagePath(raw[i], cwd, 'frames[' + String(i) + ']')
    if (!checked.ok) return checked
    out.push(checked.value.absolute)
  }
  return { ok: true, value: out }
}
