/**
 * vision_pass — read render manifests and turn pass images into bounded numbers.
 *
 * This op is host-local on purpose (vision_pass design): it reads manifest.json files and
 * the pass PNGs the render queue wrote, needs no Blender session and no Python
 * worker, and never returns raw depth / normal / index arrays. Every decode rule
 * comes from the manifest — a manifest that does not pin dtype, value range or
 * the depth formula is rejected with vision_pass_manifest_invalid instead of
 * being guessed.
 */
import fs from 'node:fs'
import path from 'node:path'
import { PngDecodeError, decodePng, type PngImage } from './png.js'
import type { VisionFailureReceipt } from './protocol.js'

export const VISION_PASS_LIMITS = {
  manifests: 240,
  objects: 64,
  /** Per-frame object rows kept in the receipt; the aggregate keeps all objects. */
  frameObjects: 16,
  bboxPairs: 32,
  /** Above this many ROI pixels statistics use a deterministic stride. */
  exactPixels: 4_000_000,
  /** Depth samples kept per object when computing the median for ordering. */
  objectDepthSamples: 50_000,
  /** Per-request decode budget across every pass image (structured error above). */
  totalPixels: 400_000_000,
  /** Per-request wall-clock budget for the whole vision_pass call. */
  totalMs: 60_000,
  /** Pass PNG byte cap (also a decode-memory guard). */
  pngBytes: 256 * 1024 * 1024,
  /** Manifest JSON byte cap. */
  manifestBytes: 8 * 1024 * 1024,
  /** object_index mapping entry cap. */
  objectMappingEntries: 4096,
} as const

export const VISION_PASSES = ['depth', 'normal', 'object_index', 'alpha'] as const
export type VisionPassId = (typeof VISION_PASSES)[number]

export interface VisionRoi {
  x: number
  y: number
  w: number
  h: number
}

export interface PassManifestEntry {
  path: string
  dtype: string
  maxValue: number
  colorspace?: string
  near?: number
  far?: number
  space?: string
  encoding?: string
  objects?: Record<number, string>
  background?: number
  /** Depth: raw sample meaning "no geometry" (default 0); only this value is invalid. */
  noGeometryValue?: number
  /** Depth: which end clips to a real plane, e.g. "far" (render contract). */
  clampedAt?: string
}

export interface PassManifest {
  manifestVersion: number
  frame: number
  resolution: [number, number]
  camera?: string
  file: string
  /** render 声明的降级 pass（请求了但文件缺失/不可用）；非空帧不是干净成功。 */
  degraded?: string[]
  passes: Partial<Record<VisionPassId | 'beauty', PassManifestEntry>>
}

export interface VisionFailure extends VisionFailureReceipt {
  op: 'vision_pass'
  fallback: string
  [key: string]: unknown
}

type Result<T> = { ok: true; value: T } | { ok: false; error: VisionFailure }

function failure(
  code: string,
  error: string,
  evidence: Record<string, unknown> = {},
  stage: VisionFailure['stage'] = 'input',
  fix?: string,
): VisionFailure {
  const defaultFix =
    code === 'vision_pass_manifest_invalid'
      ? '重新用带 passes 的 blender_run 生成 manifest（每帧 manifest.json 记录 dtype/range/depth near+far/normal space/object index 映射），再对同一批 manifest 调用 vision_pass。'
      : code === 'vision_pass_budget_exceeded'
        ? '减小 manifests 帧数、roi 或 pass 集合后分批调用（上限见 evidence.totalPixels / totalMs）。'
        : code === 'vision_pass_unsupported'
          ? '只请求 manifest 里声明过的 depth / normal / object_index / alpha，或先重渲染缺失的 pass。'
          : '检查 manifests / passes / roi / objects 参数：路径必须是已存在的 manifest.json，roi 必须在分辨率内。'
  return {
    ok: false,
    op: 'vision_pass',
    stage,
    code,
    message: error,
    error,
    root_cause: error,
    fix: fix ?? defaultFix,
    fallback:
      '不需要 pass 语义统计时可改用 vision_analyze 读单张图片；blender_run collect 仍会返回 manifest 与文件清单。',
    retryable: false,
    evidence,
  }
}

function round(value: number, digits = 6): number {
  if (!Number.isFinite(value)) return value
  const scale = 10 ** digits
  return Math.round(value * scale) / scale
}

function isValidRoi(value: unknown): value is VisionRoi {
  if (!value || typeof value !== 'object') return false
  const v = value as Record<string, unknown>
  return (
    Number.isInteger(v.x) &&
    Number.isInteger(v.y) &&
    Number.isInteger(v.w) &&
    Number.isInteger(v.h) &&
    (v.w as number) > 0 &&
    (v.h as number) > 0 &&
    (v.x as number) >= 0 &&
    (v.y as number) >= 0
  )
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

function normalizeDtype(raw: unknown): { bitDepth: 8 | 16; maxValue: number } | null {
  if (typeof raw !== 'string') return null
  const key = raw.trim().toLowerCase()
  if (['uint16', 'u16', '16u', 'ushort', '16-bit'].includes(key)) {
    return { bitDepth: 16, maxValue: 65535 }
  }
  if (['uint8', 'u8', '8u', 'ubyte', '8-bit'].includes(key)) {
    return { bitDepth: 8, maxValue: 255 }
  }
  return null
}

function isInsideDir(parent: string, candidate: string): boolean {
  const rel = path.relative(parent, candidate)
  return rel === '' || (!rel.startsWith('..') && !path.isAbsolute(rel))
}

function readJson(file: string): Result<Record<string, unknown>> {
  let text: string
  try {
    text = fs.readFileSync(file, 'utf8')
  } catch (e) {
    return {
      ok: false,
      error: failure('vision_pass_manifest_invalid', '无法读取 manifest：' + file, {
        file,
        reason: e instanceof Error ? e.message : String(e),
      }),
    }
  }
  try {
    const parsed: unknown = JSON.parse(text)
    if (!isPlainObject(parsed)) {
      return {
        ok: false,
        error: failure('vision_pass_manifest_invalid', 'manifest 顶层必须是 JSON 对象：' + file, {
          file,
        }),
      }
    }
    return { ok: true, value: parsed }
  } catch (e) {
    return {
      ok: false,
      error: failure('vision_pass_manifest_invalid', 'manifest 不是合法 JSON：' + file, {
        file,
        reason: e instanceof Error ? e.message : String(e),
      }),
    }
  }
}

function parseResolution(raw: unknown): [number, number] | null {
  if (Array.isArray(raw) && raw.length === 2) {
    const [w, h] = raw
    if (Number.isInteger(w) && Number.isInteger(h) && (w as number) > 0 && (h as number) > 0) {
      return [w as number, h as number]
    }
    return null
  }
  if (isPlainObject(raw)) {
    const w = raw.width
    const h = raw.height
    if (Number.isInteger(w) && Number.isInteger(h) && (w as number) > 0 && (h as number) > 0) {
      return [w as number, h as number]
    }
  }
  return null
}

function parseObjectMap(raw: unknown): Record<number, string> | null {
  if (!isPlainObject(raw)) return null
  const out: Record<number, string> = {}
  for (const [key, value] of Object.entries(raw)) {
    const index = Number(key)
    if (!Number.isInteger(index) || index < 0) return null
    // render 侧写 {"1":{"name":"Cube","type":"MESH"}}；也接受简写 {"1":"Cube"}。
    const name =
      typeof value === 'string'
        ? value
        : isPlainObject(value) && typeof value.name === 'string'
          ? value.name
          : null
    if (name === null || !name.trim()) return null
    out[index] = name.trim()
  }
  return out
}

function parsePassShape(
  manifestFile: string,
  passId: VisionPassId,
  raw: unknown,
): Result<{
  where: { file: string; pass: VisionPassId }
  dtype: { bitDepth: 8 | 16; maxValue: number }
}> {
  const where = { file: manifestFile, pass: passId }
  if (!isPlainObject(raw)) {
    return {
      ok: false,
      error: failure(
        'vision_pass_manifest_invalid',
        'manifest 的 passes.' + passId + ' 缺少条目',
        where,
      ),
    }
  }
  const dtype = normalizeDtype(raw.dtype)
  if (!dtype) {
    return {
      ok: false,
      error: failure(
        'vision_pass_manifest_invalid',
        'manifest 的 passes.' + passId + ' 没有可识别的 dtype（只接受 uint8/uint16）',
        { ...where, dtype: raw.dtype },
      ),
    }
  }
  if (raw.range !== undefined) {
    const range = raw.range
    // render 产出的 range 是归一化 [0,1]；兼容直接写整数存储值域 [0,255]/[0,65535]。
    const okRange =
      Array.isArray(range) &&
      range.length === 2 &&
      Number.isFinite(range[0]) &&
      Number.isFinite(range[1]) &&
      range[0] === 0 &&
      (range[1] === 1 || range[1] === dtype.maxValue)
    if (!okRange) {
      return {
        ok: false,
        error: failure(
          'vision_pass_manifest_invalid',
          'manifest 的 passes.' +
            passId +
            ' range 必须是归一化 [0,1] 或整数存储值域 [0,' +
            String(dtype.maxValue) +
            ']',
          { ...where, range },
        ),
      }
    }
  }
  return { ok: true, value: { where, dtype } }
}

function resolvePassFile(
  manifestFile: string,
  passId: VisionPassId,
  raw: Record<string, unknown>,
  where: { file: string; pass: VisionPassId },
): Result<string> {
  const relPath = raw.path
  if (typeof relPath !== 'string' || !relPath.trim()) {
    return {
      ok: false,
      error: failure(
        'vision_pass_manifest_invalid',
        'manifest 的 passes.' + passId + ' 缺少 path',
        where,
      ),
    }
  }
  const baseDir = path.dirname(manifestFile)
  const absolute = path.resolve(baseDir, relPath)
  let realFile: string
  let realBase: string
  try {
    realFile = fs.realpathSync(absolute)
    realBase = fs.realpathSync(baseDir)
  } catch (e) {
    return {
      ok: false,
      error: failure('vision_pass_manifest_invalid', 'pass 文件不存在：' + absolute, {
        ...where,
        path: absolute,
        reason: e instanceof Error ? e.message : String(e),
      }),
    }
  }
  if (!isInsideDir(realBase, realFile)) {
    return {
      ok: false,
      error: failure(
        'vision_pass_manifest_invalid',
        'pass 路径逃出了 manifest 所在目录：' + relPath,
        {
          ...where,
          path: relPath,
        },
      ),
    }
  }
  return { ok: true, value: realFile }
}

function applyDepthEntry(
  entry: PassManifestEntry,
  raw: Record<string, unknown>,
  root: Record<string, unknown>,
  where: { file: string; pass: VisionPassId },
): VisionFailure | null {
  // render 在 passes.depth 与根 depth 两处都写 near/far；任一处存在即可。
  const rootDepth = isPlainObject(root.depth) ? root.depth : {}
  const near = raw.near ?? rootDepth.near
  const far = raw.far ?? rootDepth.far
  if (!Number.isFinite(near) || !Number.isFinite(far)) {
    return failure(
      'vision_pass_manifest_invalid',
      'manifest 的 depth pass 缺少 near/far，无法把存储值解码成米',
      { ...where, near, far },
    )
  }
  if ((far as number) <= (near as number)) {
    return failure('vision_pass_manifest_invalid', 'manifest 的 depth pass near 必须小于 far', {
      ...where,
      near,
      far,
    })
  }
  // "no geometry" 的存储值由 render 声明（默认 0）；只有这个值代表无效。
  const rawNoGeometry = raw.noGeometryValue ?? rootDepth.noGeometryValue ?? 0
  if (
    !Number.isInteger(rawNoGeometry) ||
    (rawNoGeometry as number) < 0 ||
    (rawNoGeometry as number) > 65535
  ) {
    return failure(
      'vision_pass_manifest_invalid',
      'manifest 的 depth pass noGeometryValue 必须是 0–65535 的整数',
      { ...where, noGeometryValue: rawNoGeometry },
    )
  }
  entry.near = near as number
  entry.far = far as number
  entry.noGeometryValue = rawNoGeometry as number
  const clampedAt = raw.clampedAt ?? rootDepth.clampedAt
  if (typeof clampedAt === 'string' && clampedAt) entry.clampedAt = clampedAt
  return null
}

function applyNormalEntry(
  entry: PassManifestEntry,
  raw: Record<string, unknown>,
  root: Record<string, unknown>,
  where: { file: string; pass: VisionPassId },
): VisionFailure | null {
  // render 写 coordinateSpace: "camera"；旧契约写法是 space: "camera"。两处都认。
  const rootNormal = isPlainObject(root.normal) ? root.normal : {}
  const rawSpace =
    (typeof raw.space === 'string' && raw.space) ||
    (typeof raw.coordinateSpace === 'string' && raw.coordinateSpace) ||
    (typeof rootNormal.space === 'string' && rootNormal.space) ||
    (typeof rootNormal.coordinateSpace === 'string' && rootNormal.coordinateSpace) ||
    ''
  const space = rawSpace.trim().toLowerCase()
  if (space !== 'camera' && space !== 'camera_space' && space !== 'camera-space') {
    return failure(
      'vision_pass_manifest_invalid',
      'manifest 的 normal pass 必须声明相机空间（space/coordinateSpace="camera"），拒绝按未知坐标系猜测',
      { ...where, space: rawSpace || undefined },
    )
  }
  entry.space = 'camera'
  entry.encoding =
    (typeof raw.encoding === 'string' && raw.encoding) ||
    (typeof rootNormal.encoding === 'string' && rootNormal.encoding) ||
    undefined
  return null
}

function applyObjectIndexEntry(
  entry: PassManifestEntry,
  raw: Record<string, unknown>,
  root: Record<string, unknown>,
  where: { file: string; pass: VisionPassId },
): VisionFailure | null {
  // render 把映射写在根 objectIndex.mapping；旧契约写作 passes.object_index.objects。
  const rootIndex = isPlainObject(root.objectIndex)
    ? root.objectIndex
    : isPlainObject(root.object_index)
      ? root.object_index
      : {}
  for (const candidate of [raw.objects, rootIndex.mapping, rootIndex.objects]) {
    if (
      isPlainObject(candidate) &&
      Object.keys(candidate).length > VISION_PASS_LIMITS.objectMappingEntries
    ) {
      return failure(
        'vision_pass_manifest_invalid',
        'object_index 映射条目超过 ' +
          String(VISION_PASS_LIMITS.objectMappingEntries) +
          ' 个（拒绝超大批量映射）',
        {
          ...where,
          entries: Object.keys(candidate).length,
          limit: VISION_PASS_LIMITS.objectMappingEntries,
        },
      )
    }
  }
  const objects =
    parseObjectMap(raw.objects) ??
    parseObjectMap(rootIndex.mapping) ??
    parseObjectMap(rootIndex.objects)
  if (!objects) {
    return failure(
      'vision_pass_manifest_invalid',
      'manifest 的 object_index pass 缺少合法的 index→对象名映射（objectIndex.mapping 或 passes.object_index.objects，0 为背景）',
      { ...where, objectIndex: rootIndex },
    )
  }
  entry.objects = objects
  const background = Number.isInteger(raw.background)
    ? (raw.background as number)
    : Number.isInteger(rootIndex.background)
      ? (rootIndex.background as number)
      : undefined
  if (background !== undefined) entry.background = background
  return null
}

function parseEntry(
  manifestFile: string,
  passId: VisionPassId,
  raw: unknown,
  root: Record<string, unknown>,
): Result<PassManifestEntry> {
  const shape = parsePassShape(manifestFile, passId, raw)
  if (!shape.ok) return shape
  const { where, dtype } = shape.value
  const rawObj = raw as Record<string, unknown>
  const file = resolvePassFile(manifestFile, passId, rawObj, where)
  if (!file.ok) return file
  const entry: PassManifestEntry = {
    path: file.value,
    dtype: String(rawObj.dtype),
    maxValue: dtype.maxValue,
    colorspace: typeof rawObj.colorspace === 'string' ? rawObj.colorspace : undefined,
    encoding: typeof rawObj.encoding === 'string' ? rawObj.encoding : undefined,
    background: Number.isInteger(rawObj.background) ? (rawObj.background as number) : undefined,
  }
  if (passId === 'depth') {
    const error = applyDepthEntry(entry, rawObj, root, where)
    if (error) return { ok: false, error }
  }
  if (passId === 'normal') {
    const error = applyNormalEntry(entry, rawObj, root, where)
    if (error) return { ok: false, error }
  }
  if (passId === 'object_index') {
    const error = applyObjectIndexEntry(entry, rawObj, root, where)
    if (error) return { ok: false, error }
  }
  const encodingError = validateEncoding(passId, entry.encoding, where)
  if (encodingError) return { ok: false, error: encodingError }
  return { ok: true, value: entry }
}

/** 每种 pass 的已知编码写法；未声明编码时不做猜测式校验。 */
const PASS_ENCODINGS: Record<VisionPassId, readonly string[]> = {
  depth: ['normalized_depth', 'depth_normalized', 'normalized', 'depth', 'depth_m'],
  normal: ['n*0.5+0.5', 'rgb*2-1', 'normalized', 'normal'],
  object_index: ['index/65535', 'index', 'object_index', 'index16'],
  alpha: ['alpha', 'alpha/255', 'coverage', 'a'],
}

function validateEncoding(
  passId: VisionPassId,
  encoding: string | undefined,
  where: { file: string; pass: VisionPassId },
): VisionFailure | null {
  if (encoding === undefined || !encoding.trim()) return null
  const key = encoding.trim().toLowerCase()
  if (PASS_ENCODINGS[passId].includes(key)) return null
  return failure(
    'vision_pass_manifest_invalid',
    'manifest 的 ' + passId + ' pass 声明了不支持的 encoding：' + encoding,
    { ...where, encoding, supported: [...PASS_ENCODINGS[passId]] },
  )
}

function validateManifestMeta(
  data: Record<string, unknown>,
  absolute: string,
): Result<{ frame: number; resolution: [number, number] }> {
  if (data.manifestVersion !== 1) {
    return {
      ok: false,
      error: failure(
        'vision_pass_manifest_invalid',
        '不支持的 manifestVersion：' + String(data.manifestVersion) + '（本版本只接受 1）',
        { file: absolute, manifestVersion: data.manifestVersion },
      ),
    }
  }
  const resolution = parseResolution(data.resolution)
  if (!resolution) {
    return {
      ok: false,
      error: failure(
        'vision_pass_manifest_invalid',
        'manifest 缺少合法的 resolution [width,height]',
        {
          file: absolute,
          resolution: data.resolution,
        },
      ),
    }
  }
  const frame = data.frame
  if (!Number.isInteger(frame) || (frame as number) < 0) {
    return {
      ok: false,
      error: failure('vision_pass_manifest_invalid', 'manifest 缺少合法的 frame 序号', {
        file: absolute,
        frame,
      }),
    }
  }
  if (!isPlainObject(data.passes)) {
    return {
      ok: false,
      error: failure('vision_pass_manifest_invalid', 'manifest 缺少 passes 对象', {
        file: absolute,
      }),
    }
  }
  return { ok: true, value: { frame: frame as number, resolution } }
}

function collectManifestPasses(
  data: Record<string, unknown>,
  absolute: string,
  frame: number,
): Result<{ requested: VisionPassId[]; degradedRaw: unknown }> {
  const passes = data.passes as Record<string, unknown>
  const requested = Object.keys(passes).filter((key) =>
    (VISION_PASSES as readonly string[]).includes(key),
  )
  if (!requested.length) {
    return {
      ok: false,
      error: failure(
        'vision_pass_manifest_invalid',
        'manifest 没有任何受支持的 pass（depth / normal / object_index / alpha）',
        { file: absolute, passes: Object.keys(passes) },
      ),
    }
  }
  // render 保证 frame 与 frame_XXXX 目录名一致；不一致说明 manifest 被搬错位。
  const dirFrame = /^frame_(\d+)$/.exec(path.basename(path.dirname(absolute)))
  if (dirFrame && Number(dirFrame[1]) !== frame) {
    return {
      ok: false,
      error: failure(
        'vision_pass_manifest_invalid',
        'manifest.frame=' +
          String(frame) +
          ' 与目录 ' +
          path.basename(path.dirname(absolute)) +
          ' 不一致',
        { file: absolute, frame, dirFrame: Number(dirFrame[1]) },
      ),
    }
  }
  const degradedRaw = data.degraded
  if (
    degradedRaw !== undefined &&
    !(Array.isArray(degradedRaw) && degradedRaw.every((v) => typeof v === 'string'))
  ) {
    return {
      ok: false,
      error: failure('vision_pass_manifest_invalid', 'manifest.degraded 必须是 pass 名数组', {
        file: absolute,
        degraded: degradedRaw,
      }),
    }
  }
  return { ok: true, value: { requested: requested as VisionPassId[], degradedRaw } }
}

function loadManifest(file: string, cwd: string): Result<PassManifest> {
  const absolute = path.resolve(cwd, file)
  // 单次 stat：不再 exists+stat 两次（TOCTOU），也不让 ENOENT/EACCES 逸出 dispatch。
  let stat: fs.Stats
  try {
    stat = fs.statSync(absolute)
  } catch (e) {
    return {
      ok: false,
      error: failure('vision_pass_manifest_invalid', 'manifest 文件不存在：' + absolute, {
        file: absolute,
        reason: e instanceof Error ? e.message : String(e),
      }),
    }
  }
  if (!stat.isFile()) {
    return {
      ok: false,
      error: failure(
        'vision_pass_manifest_invalid',
        'manifest 不是普通文件（目录/FIFO/设备）：' + absolute,
        { file: absolute },
      ),
    }
  }
  if (stat.size > VISION_PASS_LIMITS.manifestBytes) {
    return {
      ok: false,
      error: failure(
        'vision_pass_manifest_invalid',
        'manifest 超过 ' + String(VISION_PASS_LIMITS.manifestBytes) + ' 字节上限：' + absolute,
        { file: absolute, bytes: stat.size, limit: VISION_PASS_LIMITS.manifestBytes },
      ),
    }
  }
  const parsed = readJson(absolute)
  if (!parsed.ok) return parsed
  const data = parsed.value
  const meta = validateManifestMeta(data, absolute)
  if (!meta.ok) return meta
  const collected = collectManifestPasses(data, absolute, meta.value.frame)
  if (!collected.ok) return collected
  const { requested, degradedRaw } = collected.value
  const passes: PassManifest['passes'] = {}
  for (const passId of requested) {
    const entry = parseEntry(
      absolute,
      passId,
      (data.passes as Record<string, unknown>)[passId],
      data,
    )
    if (!entry.ok) return entry
    passes[passId] = entry.value
  }
  return {
    ok: true,
    value: {
      manifestVersion: 1,
      frame: meta.value.frame,
      resolution: meta.value.resolution,
      camera: typeof data.camera === 'string' ? data.camera : undefined,
      file: absolute,
      ...(degradedRaw !== undefined ? { degraded: degradedRaw as string[] } : {}),
      passes,
    },
  }
}

interface ResolvedRoi {
  x: number
  y: number
  w: number
  h: number
  pixels: number
  stride: number
  sampled: boolean
}

function resolveRoi(
  roi: VisionRoi | undefined,
  width: number,
  height: number,
): Result<ResolvedRoi> {
  const area = roi ?? { x: 0, y: 0, w: width, h: height }
  if (area.x + area.w > width || area.y + area.h > height) {
    return {
      ok: false,
      error: failure(
        'vision_input_invalid',
        'roi 超出图片分辨率：roi=' +
          JSON.stringify(area) +
          '，resolution=' +
          String(width) +
          'x' +
          String(height),
        { roi: area, resolution: [width, height] },
      ),
    }
  }
  const pixels = area.w * area.h
  const stride =
    pixels > VISION_PASS_LIMITS.exactPixels
      ? Math.max(1, Math.ceil(Math.sqrt(pixels / VISION_PASS_LIMITS.exactPixels)))
      : 1
  return { ok: true, value: { ...area, pixels, stride, sampled: stride > 1 } }
}

/** 单请求解码预算：像素按 manifest resolution 在解码前计入，时长在帧间检查。 */
interface PassBudget {
  pixels: number
  frames: number
  startedAt: number
  limitPixels: number
  limitMs: number
  now: () => number
}

function budgetFailure(budget: PassBudget, kind: 'pixels' | 'time'): VisionFailure {
  const elapsed = Math.max(0, budget.now() - budget.startedAt)
  return failure(
    'vision_pass_budget_exceeded',
    'vision_pass 单请求超预算（' +
      (kind === 'pixels' ? '解码像素' : '耗时') +
      '）：已用 ' +
      String(budget.pixels) +
      ' 像素 / ' +
      String(elapsed) +
      ' ms（' +
      String(budget.frames) +
      ' 帧）',
    {
      reason: kind,
      usedPixels: budget.pixels,
      totalPixels: budget.limitPixels,
      elapsedMs: elapsed,
      totalMs: budget.limitMs,
      frames: budget.frames,
    },
    'input',
    '减小 manifests 帧数、roi 或 pass 集合后分批调用；单请求上限 ' +
      String(budget.limitPixels) +
      ' 像素、' +
      String(budget.limitMs) +
      ' ms。',
  )
}

function chargePixels(budget: PassBudget, pixels: number): VisionFailure | null {
  budget.pixels += pixels
  return budget.pixels > budget.limitPixels ? budgetFailure(budget, 'pixels') : null
}

function budgetExceeded(budget: PassBudget): VisionFailure | null {
  if (budget.pixels > budget.limitPixels) return budgetFailure(budget, 'pixels')
  if (budget.now() - budget.startedAt > budget.limitMs) return budgetFailure(budget, 'time')
  return null
}

function loadPassImage(
  file: string,
  passId: VisionPassId,
  resolution: [number, number],
  expectedChannels: number[],
  declaredMaxValue?: number,
  budget?: PassBudget,
): Result<PngImage> {
  let stat: fs.Stats
  try {
    stat = fs.statSync(file)
  } catch (e) {
    return {
      ok: false,
      error: failure('vision_pass_manifest_invalid', 'pass 文件不存在：' + file, {
        file,
        pass: passId,
        reason: e instanceof Error ? e.message : String(e),
      }),
    }
  }
  if (!stat.isFile()) {
    return {
      ok: false,
      error: failure(
        'vision_pass_manifest_invalid',
        'pass 文件不是普通文件（目录/FIFO/设备）：' + file,
        { file, pass: passId },
      ),
    }
  }
  if (stat.size > VISION_PASS_LIMITS.pngBytes) {
    return {
      ok: false,
      error: failure(
        'vision_pass_manifest_invalid',
        'pass 图片超过 ' + String(VISION_PASS_LIMITS.pngBytes) + ' 字节上限：' + file,
        { file, pass: passId, bytes: stat.size, limit: VISION_PASS_LIMITS.pngBytes },
      ),
    }
  }
  if (budget) {
    // 解码前按 manifest resolution 计费：超预算时不会真的分配/解压大图。
    const over = chargePixels(budget, resolution[0] * resolution[1])
    if (over) return { ok: false, error: over }
  }
  let image: PngImage
  try {
    image = decodePng(fs.readFileSync(file))
  } catch (e) {
    const reason =
      e instanceof PngDecodeError ? e.reason : e instanceof Error ? e.message : String(e)
    return {
      ok: false,
      error: failure('vision_pass_manifest_invalid', 'pass 图片无法解码：' + file, {
        file,
        pass: passId,
        reason,
      }),
    }
  }
  if (image.width !== resolution[0] || image.height !== resolution[1]) {
    return {
      ok: false,
      error: failure(
        'vision_pass_manifest_invalid',
        'pass 图片尺寸与 manifest resolution 不一致：' + file,
        {
          file,
          pass: passId,
          image: [image.width, image.height],
          resolution,
        },
      ),
    }
  }
  if (!expectedChannels.includes(image.channels)) {
    return {
      ok: false,
      error: failure('vision_pass_manifest_invalid', 'pass 图片通道数与 pass 类型不匹配：' + file, {
        file,
        pass: passId,
        channels: image.channels,
        expected: expectedChannels,
      }),
    }
  }
  if (declaredMaxValue !== undefined) {
    // 声明的 dtype 必须等于 PNG 真实位深，否则解码公式就是猜的。
    const declaredBits = declaredMaxValue === 65535 ? 16 : 8
    if (image.bitDepth !== declaredBits) {
      return {
        ok: false,
        error: failure(
          'vision_pass_manifest_invalid',
          'pass 图片位深与 manifest dtype 不一致：' + file,
          {
            file,
            pass: passId,
            declaredBits,
            actualBits: image.bitDepth,
          },
        ),
      }
    }
  }
  return { ok: true, value: image }
}

function quantile(sorted: Float64Array, q: number): number {
  const n = sorted.length
  if (n === 0) return Number.NaN
  if (n === 1) return sorted[0]
  const pos = (n - 1) * q
  const lo = Math.floor(pos)
  const hi = Math.ceil(pos)
  if (lo === hi) return sorted[lo]
  const weight = pos - lo
  return sorted[lo] * (1 - weight) + sorted[hi] * weight
}

interface FrameDepth {
  unit: 'm'
  min: number | null
  max: number | null
  mean: number | null
  p01: number | null
  p05: number | null
  p50: number | null
  p95: number | null
  p99: number | null
  invalid_ratio: number
  valid_pixels: number
  decoded: string
  near: number
  far: number
}

function analyzeDepth(image: PngImage, entry: PassManifestEntry, roi: ResolvedRoi): FrameDepth {
  const near = entry.near as number
  const far = entry.far as number
  const max = image.maxValue
  const scale = (far - near) / max
  const capacity = Math.ceil(roi.w / roi.stride) * Math.ceil(roi.h / roi.stride)
  const values = new Float64Array(capacity)
  let count = 0
  let invalid = 0
  let total = 0
  const step = roi.stride
  const noGeometry = entry.noGeometryValue ?? 0
  for (let y = roi.y; y < roi.y + roi.h; y += step) {
    let offset = y * image.rowBytes + roi.x * image.bpp
    for (let x = roi.x; x < roi.x + roi.w; x += step) {
      const raw =
        image.bitDepth === 16
          ? (image.data[offset] << 8) | image.data[offset + 1]
          : image.data[offset]
      offset += image.bpp * step
      total += 1
      if (raw === noGeometry) {
        invalid += 1
        continue
      }
      values[count] = near + raw * scale
      count += 1
    }
  }
  const valid = values.subarray(0, count)
  valid.sort()
  let sum = 0
  for (let i = 0; i < valid.length; i += 1) sum += valid[i]
  return {
    unit: 'm',
    min: valid.length ? round(valid[0]) : null,
    max: valid.length ? round(valid[valid.length - 1]) : null,
    mean: valid.length ? round(sum / valid.length) : null,
    p01: valid.length ? round(quantile(valid, 0.01)) : null,
    p05: valid.length ? round(quantile(valid, 0.05)) : null,
    p50: valid.length ? round(quantile(valid, 0.5)) : null,
    p95: valid.length ? round(quantile(valid, 0.95)) : null,
    p99: valid.length ? round(quantile(valid, 0.99)) : null,
    invalid_ratio: total ? round(invalid / total) : 0,
    valid_pixels: valid.length,
    decoded:
      'depth_m = near + (raw/' +
      String(max) +
      ') * (far - near); invalid = raw ' +
      String(noGeometry) +
      ' (noGeometryValue, no geometry)',
    near,
    far,
  }
}

interface FrameNormal {
  space: 'camera'
  mean: [number, number, number] | null
  angular_dispersion_deg: number | null
  valid_ratio: number
  decoded: string
}

/** Camera-space unit vector from the encoded RGB sample (n = raw*2/max - 1). */
function decodeNormal(
  image: PngImage,
  x: number,
  y: number,
  scale: number,
): [number, number, number] {
  const r = image.sample(x, y, 0)
  const g = image.sample(x, y, image.channels >= 3 ? 1 : 0)
  const b = image.sample(x, y, image.channels >= 3 ? 2 : 0)
  return [r * scale - 1, g * scale - 1, b * scale - 1]
}

function analyzeNormal(image: PngImage, roi: ResolvedRoi): FrameNormal {
  const scale = 2 / image.maxValue
  let sx = 0
  let sy = 0
  let sz = 0
  let valid = 0
  let total = 0
  const step = roi.stride
  for (let y = roi.y; y < roi.y + roi.h; y += step) {
    for (let x = roi.x; x < roi.x + roi.w; x += step) {
      const [nx, ny, nz] = decodeNormal(image, x, y, scale)
      total += 1
      const len = Math.sqrt(nx * nx + ny * ny + nz * nz)
      if (!(len > 1e-6)) continue
      sx += nx / len
      sy += ny / len
      sz += nz / len
      valid += 1
    }
  }
  if (!valid) {
    return {
      space: 'camera',
      mean: null,
      angular_dispersion_deg: null,
      valid_ratio: 0,
      decoded: 'n = raw*2/max - 1, normalized; camera space',
    }
  }
  let mx = sx / valid
  let my = sy / valid
  let mz = sz / valid
  const mlen = Math.sqrt(mx * mx + my * my + mz * mz)
  mx /= mlen
  my /= mlen
  mz /= mlen
  let angleSum = 0
  let counted = 0
  for (let y = roi.y; y < roi.y + roi.h; y += step) {
    for (let x = roi.x; x < roi.x + roi.w; x += step) {
      const [nx, ny, nz] = decodeNormal(image, x, y, scale)
      const len = Math.sqrt(nx * nx + ny * ny + nz * nz)
      if (!(len > 1e-6)) continue
      const dot = (nx * mx + ny * my + nz * mz) / len
      const clamped = Math.max(-1, Math.min(1, dot))
      angleSum += (Math.acos(clamped) * 180) / Math.PI
      counted += 1
    }
  }
  return {
    space: 'camera',
    mean: [round(mx), round(my), round(mz)],
    angular_dispersion_deg: counted ? round(angleSum / counted) : null,
    valid_ratio: total ? round(valid / total) : 0,
    decoded: 'n = raw*2/max - 1, normalized; camera space',
  }
}

interface ObjectFrameRow {
  name: string
  index: number
  pixels: number
  /** pixels 是采样计数（stride>1 时小于真实像素数）。 */
  sampled: boolean
  coverage: number
  bbox: [number, number, number, number]
  median_depth_m: number | null
}

interface FrameObjects {
  background_pixels: number
  /** 非背景、非映射索引的像素数（默认 0 为背景）。 */
  unmapped_pixels: number
  rows: ObjectFrameRow[]
  truncated: boolean
  object_count: number
  depth_available: boolean
  sampled: boolean
}

function analyzeObjects(
  image: PngImage,
  entry: PassManifestEntry,
  roi: ResolvedRoi,
  depths: PngImage | null,
  near: number | null,
  far: number | null,
): FrameObjects {
  const mapping = entry.objects ?? {}
  const background = entry.background ?? 0
  const step = roi.stride
  const counts = new Map<number, number>()
  const boxes = new Map<number, [number, number, number, number]>()
  const depthSamples = new Map<number, { values: number[]; seen: number }>()
  let backgroundPixels = 0
  let unmappedPixels = 0
  let total = 0
  const noGeometry = entry.noGeometryValue ?? 0
  const depthScale = near !== null && far !== null && depths ? (far - near) / depths.maxValue : 0
  for (let y = roi.y; y < roi.y + roi.h; y += step) {
    let offset = y * image.rowBytes + roi.x * image.bpp
    let depthOffset = depths ? y * depths.rowBytes + roi.x * depths.bpp : 0
    for (let x = roi.x; x < roi.x + roi.w; x += step) {
      const raw =
        image.bitDepth === 16
          ? (image.data[offset] << 8) | image.data[offset + 1]
          : image.data[offset]
      offset += image.bpp * step
      total += 1
      if (raw === background) {
        backgroundPixels += 1
      } else if (mapping[raw] !== undefined) {
        counts.set(raw, (counts.get(raw) ?? 0) + 1)
        const box = boxes.get(raw)
        if (!box) boxes.set(raw, [x, y, x + 1, y + 1])
        else {
          if (x < box[0]) box[0] = x
          if (y < box[1]) box[1] = y
          if (x + 1 > box[2]) box[2] = x + 1
          if (y + 1 > box[3]) box[3] = y + 1
        }
        if (depths && depthScale > 0 && near !== null && far !== null) {
          const draw =
            depths.bitDepth === 16
              ? (depths.data[depthOffset] << 8) | depths.data[depthOffset + 1]
              : depths.data[depthOffset]
          if (draw !== noGeometry) {
            const sample = near + draw * depthScale
            const bucket = depthSamples.get(raw)
            if (!bucket) depthSamples.set(raw, { values: [sample], seen: 1 })
            else {
              bucket.seen += 1
              if (bucket.values.length < VISION_PASS_LIMITS.objectDepthSamples) {
                bucket.values.push(sample)
              } else {
                // 确定性蓄水池：样本覆盖整个扫描顺序，而不是只取前 50k 个。
                const slot = Math.floor(hash01(bucket.seen) * bucket.seen)
                if (slot < VISION_PASS_LIMITS.objectDepthSamples) bucket.values[slot] = sample
              }
            }
          }
        }
      } else {
        unmappedPixels += 1
      }
      if (depths) depthOffset += depths.bpp * step
    }
  }
  const rows: ObjectFrameRow[] = []
  const sortedIndices = [...counts.keys()].sort(
    (a, b) => (counts.get(b) ?? 0) - (counts.get(a) ?? 0),
  )
  for (const index of sortedIndices.slice(0, VISION_PASS_LIMITS.frameObjects)) {
    const bucket = depthSamples.get(index)
    let median: number | null = null
    if (bucket && bucket.values.length) {
      const sorted = [...bucket.values].sort((a, b) => a - b)
      median = round(quantile(Float64Array.from(sorted), 0.5))
    }
    rows.push({
      name: mapping[index] as string,
      index,
      pixels: counts.get(index) as number,
      sampled: roi.sampled,
      coverage: round((counts.get(index) as number) / total),
      bbox: boxes.get(index) as [number, number, number, number],
      median_depth_m: median,
    })
  }
  return {
    background_pixels: backgroundPixels,
    unmapped_pixels: unmappedPixels,
    rows,
    truncated: sortedIndices.length > VISION_PASS_LIMITS.frameObjects,
    object_count: sortedIndices.length,
    depth_available: Boolean(depths) && near !== null && far !== null,
    sampled: roi.sampled,
  }
}

/** 确定性 [0,1) 伪随机：蓄水池采样用，保证同一输入得同一结果。 */
function hash01(n: number): number {
  const mixed = Math.imul((n ^ 0x9e3779b1) >>> 0, 2654435761) >>> 0
  return mixed / 4294967296
}

function analyzeAlpha(image: PngImage, roi: ResolvedRoi): { coverage: number; decoded: string } {
  const step = roi.stride
  const channel = image.channels === 4 ? 3 : image.channels === 2 ? 1 : 0
  const max = image.maxValue
  let foreground = 0
  let total = 0
  for (let y = roi.y; y < roi.y + roi.h; y += step) {
    let offset = y * image.rowBytes + roi.x * image.bpp + channel * (image.bitDepth === 16 ? 2 : 1)
    for (let x = roi.x; x < roi.x + roi.w; x += step) {
      const raw =
        image.bitDepth === 16
          ? (image.data[offset] << 8) | image.data[offset + 1]
          : image.data[offset]
      offset += image.bpp * step
      total += 1
      if (raw > max / 2) foreground += 1
    }
  }
  return {
    coverage: total ? round(foreground / total) : 0,
    decoded: 'alpha > max/2 counts as foreground',
  }
}

interface FrameResult {
  frame: number
  manifest: string
  resolution: [number, number]
  sampled: boolean
  depth?: FrameDepth
  normal?: FrameNormal
  alpha?: { coverage: number; decoded: string }
  objects?: FrameObjects
  warnings: string[]
}

interface ObjectAcc {
  name: string
  index: number
  coverages: number[]
  minPixels: number
  maxPixels: number
  boxes: Array<[number, number, number, number]>
  depths: number[]
  sampled: boolean
}

function accumulateObjects(frames: FrameResult[]): Map<string, ObjectAcc> {
  const acc = new Map<string, ObjectAcc>()
  for (const frame of frames) {
    for (const row of frame.objects?.rows ?? []) {
      let entry = acc.get(row.name)
      if (!entry) {
        entry = {
          name: row.name,
          index: row.index,
          coverages: [],
          minPixels: row.pixels,
          maxPixels: row.pixels,
          boxes: [],
          depths: [],
          sampled: row.sampled,
        }
        acc.set(row.name, entry)
      }
      entry.sampled = entry.sampled || row.sampled
      entry.coverages.push(row.coverage)
      entry.minPixels = Math.min(entry.minPixels, row.pixels)
      entry.maxPixels = Math.max(entry.maxPixels, row.pixels)
      entry.boxes.push(row.bbox)
      if (row.median_depth_m !== null) entry.depths.push(row.median_depth_m)
    }
  }
  return acc
}

function buildObjectRows(acc: Map<string, ObjectAcc>): {
  rows: ObjectAcc[]
  truncatedWarning: string | null
} {
  const rows = [...acc.values()]
    .sort((a, b) => b.coverages.length - a.coverages.length || a.name.localeCompare(b.name))
    .slice(0, VISION_PASS_LIMITS.objects)
  let truncatedWarning: string | null = null
  if (acc.size > VISION_PASS_LIMITS.objects) {
    truncatedWarning =
      'objects_truncated: 可见对象 ' +
      String(acc.size) +
      ' 超过上限 ' +
      String(VISION_PASS_LIMITS.objects) +
      '，只保留可见帧数最多的对象'
  }
  return { rows, truncatedWarning }
}

function buildObjectEntries(rows: ObjectAcc[]): Array<Record<string, unknown>> {
  return rows.map((entry) => {
    const mean = entry.coverages.reduce((a, b) => a + b, 0) / entry.coverages.length
    const variance =
      entry.coverages.reduce((a, b) => a + (b - mean) * (b - mean), 0) / entry.coverages.length
    const x0 = Math.min(...entry.boxes.map((b) => b[0]))
    const y0 = Math.min(...entry.boxes.map((b) => b[1]))
    const x1 = Math.max(...entry.boxes.map((b) => b[2]))
    const y1 = Math.max(...entry.boxes.map((b) => b[3]))
    const medianDepth = entry.depths.length
      ? entry.depths.reduce((a, b) => a + b, 0) / entry.depths.length
      : null
    return {
      name: entry.name,
      index: entry.index,
      visible_frames: entry.coverages.length,
      coverage_mean: round(mean),
      coverage_std: round(Math.sqrt(variance)),
      coverage_min: round(Math.min(...entry.coverages)),
      coverage_max: round(Math.max(...entry.coverages)),
      visible_pixels_min: entry.minPixels,
      visible_pixels_max: entry.maxPixels,
      /** pixels 均为采样计数（sampled=true 时 stride>1，真实像素数更大）。 */
      sampled: entry.sampled,
      bbox_union: [x0, y0, x1, y1],
      median_depth_m: medianDepth === null ? null : round(medianDepth),
    }
  })
}

function buildMissingObjectWarnings(
  frames: FrameResult[],
  objects: Array<Record<string, unknown>>,
): string[] {
  const missingFrames = new Map<string, number[]>()
  for (const frame of frames) {
    const present = new Set((frame.objects?.rows ?? []).map((row) => row.name))
    for (const object of objects) {
      if (!present.has(object.name as string)) {
        const list = missingFrames.get(object.name as string) ?? []
        list.push(frame.frame)
        missingFrames.set(object.name as string, list)
      }
    }
  }
  const warnings: string[] = []
  for (const [name, list] of missingFrames) {
    warnings.push(
      'object_missing_in_frames: ' + name + ' 在帧 ' + list.slice(0, 8).join(',') + ' 不可见',
    )
  }
  return warnings
}

function buildObjectOrdering(
  objects: Array<Record<string, unknown>>,
): Array<Record<string, unknown>> {
  const ordered = objects
    .filter((object) => object.median_depth_m !== null)
    .sort((a, b) => (a.median_depth_m as number) - (b.median_depth_m as number))
  ordered.forEach((object, index) => {
    object.depth_order = index + 1
  })
  for (const object of objects) {
    if (object.depth_order === undefined) object.depth_order = null
  }
  const bboxPairOrder: Array<Record<string, unknown>> = []
  for (
    let i = 0;
    i < ordered.length && bboxPairOrder.length < VISION_PASS_LIMITS.bboxPairs;
    i += 1
  ) {
    for (
      let j = i + 1;
      j < ordered.length && bboxPairOrder.length < VISION_PASS_LIMITS.bboxPairs;
      j += 1
    ) {
      const a = ordered[i]
      const b = ordered[j]
      const ba = a.bbox_union as [number, number, number, number]
      const bb = b.bbox_union as [number, number, number, number]
      const overlap =
        Math.max(ba[0], bb[0]) < Math.min(ba[2], bb[2]) &&
        Math.max(ba[1], bb[1]) < Math.min(ba[3], bb[3])
      if (!overlap) continue
      bboxPairOrder.push({
        a: a.name,
        b: b.name,
        front: a.name,
        basis: 'bbox_overlap_median_depth',
        note: 'bbox 级近似：重叠只表示投影包围盒相交，不表示真实遮挡或穿透',
      })
    }
  }
  return bboxPairOrder
}

function aggregateObjects(frames: FrameResult[]): {
  objects: Array<Record<string, unknown>>
  ordering: Record<string, unknown>
  bbox_overlap_order: Array<Record<string, unknown>>
  warnings: string[]
} {
  const acc = accumulateObjects(frames)
  const { rows, truncatedWarning } = buildObjectRows(acc)
  const objects = buildObjectEntries(rows)
  const warnings: string[] = []
  if (truncatedWarning) warnings.push(truncatedWarning)
  warnings.push(...buildMissingObjectWarnings(frames, objects))
  const bboxPairOrder = buildObjectOrdering(objects)
  return {
    objects,
    ordering: {
      method: 'median_depth',
      note: '顺序只表示可见覆盖与中位深度（近→远），不是隐藏面重建或精确遮挡；bbox 重叠另给 bbox_overlap_order。',
    },
    bbox_overlap_order: bboxPairOrder,
    warnings,
  }
}

function validateRequestedPasses(requestedPasses: string[] | undefined): Result<null> {
  if (!requestedPasses) return { ok: true, value: null }
  if (!requestedPasses.length) {
    return {
      ok: false,
      error: failure(
        'vision_input_invalid',
        'passes 不能是空数组（省略表示使用 manifest 中已有的 pass）',
      ),
    }
  }
  const unknown = requestedPasses.filter((p) => !(VISION_PASSES as readonly string[]).includes(p))
  if (unknown.length) {
    return {
      ok: false,
      error: failure('vision_pass_unsupported', '不支持的 pass：' + unknown.join(', '), {
        passes: unknown,
        supported: [...VISION_PASSES],
      }),
    }
  }
  return { ok: true, value: null }
}

function parseVisionInputArgs(args: Record<string, unknown>): Result<{
  manifestFiles: string[]
  roiArg: VisionRoi | undefined
  requestedPasses: string[] | undefined
  objectFilter: string[] | undefined
  warnings: string[]
}> {
  const rawManifests = args.manifests
  if (!Array.isArray(rawManifests) || rawManifests.length === 0) {
    return {
      ok: false,
      error: failure('vision_input_invalid', 'manifests 必须是 1–240 个 manifest.json 路径的数组', {
        received: rawManifests,
      }),
    }
  }
  const warnings: string[] = []
  let manifestFiles = rawManifests.map((value) => String(value))
  if (manifestFiles.length > VISION_PASS_LIMITS.manifests) {
    warnings.push(
      'manifests_truncated: 收到 ' +
        String(manifestFiles.length) +
        ' 个 manifest，只分析前 ' +
        String(VISION_PASS_LIMITS.manifests) +
        ' 个',
    )
    manifestFiles = manifestFiles.slice(0, VISION_PASS_LIMITS.manifests)
  }
  if (args.roi !== undefined && !isValidRoi(args.roi)) {
    return {
      ok: false,
      error: failure('vision_input_invalid', 'roi 必须是 {x,y,w,h} 的非负整数像素矩形（w/h > 0）', {
        roi: args.roi,
      }),
    }
  }
  const roiArg = args.roi as VisionRoi | undefined
  const requestedPasses = Array.isArray(args.passes)
    ? args.passes.map((value) => String(value))
    : undefined
  const passesCheck = validateRequestedPasses(requestedPasses)
  if (!passesCheck.ok) return passesCheck
  const objectFilter = Array.isArray(args.objects)
    ? args.objects.map((value) => String(value))
    : undefined
  if (objectFilter && objectFilter.length === 0) {
    return {
      ok: false,
      error: failure(
        'vision_input_invalid',
        'objects 不能是空数组（省略表示不过滤；显式空列表无意义）',
      ),
    }
  }
  if (objectFilter && objectFilter.length > VISION_PASS_LIMITS.objects) {
    return {
      ok: false,
      error: failure(
        'vision_input_invalid',
        'objects 最多 ' + String(VISION_PASS_LIMITS.objects) + ' 个',
        { count: objectFilter.length },
      ),
    }
  }
  return {
    ok: true,
    value: { manifestFiles, roiArg, requestedPasses, objectFilter, warnings },
  }
}

function loadAndValidateManifests(
  manifestFiles: string[],
  cwd: string,
  warnings: string[],
): Result<PassManifest[]> {
  const manifests: PassManifest[] = []
  for (const file of manifestFiles) {
    const loaded = loadManifest(file, cwd)
    if (!loaded.ok) return { ok: false, error: { ...loaded.error, warnings } }
    manifests.push(loaded.value)
  }
  const resolution = manifests[0].resolution
  for (const manifest of manifests) {
    if (manifest.resolution[0] !== resolution[0] || manifest.resolution[1] !== resolution[1]) {
      return {
        ok: false,
        error: failure(
          'vision_pass_manifest_invalid',
          '同一批 manifest 的 resolution 不一致：' + manifest.file,
          { file: manifest.file, resolution: manifest.resolution, expected: resolution },
        ),
      }
    }
  }
  return { ok: true, value: manifests }
}

function resolveActivePasses(
  manifests: PassManifest[],
  requestedPasses: string[] | undefined,
  warnings: string[],
): Result<VisionPassId[]> {
  // Default pass set: what the first manifest actually contains; later frames that
  // lack one of those passes are warned about instead of being silently skipped.
  // A pass explicitly requested by the caller must exist in at least one manifest;
  // otherwise the request cannot be served at all (Lead/render agreed semantics).
  if (requestedPasses) {
    for (const passId of requestedPasses) {
      if (!manifests.some((manifest) => manifest.passes[passId as VisionPassId])) {
        return {
          ok: false,
          error: {
            ...failure(
              'vision_pass_unsupported',
              '请求的 ' + passId + ' pass 不在任何 manifest 中',
              {
                pass: passId,
                requested: requestedPasses,
                available: Object.keys(manifests[0].passes),
              },
            ),
            warnings,
          },
        }
      }
    }
  }
  const activePasses = (requestedPasses ?? (Object.keys(manifests[0].passes) as VisionPassId[]))
    .filter((value, index, list) => list.indexOf(value) === index)
    .filter((p): p is VisionPassId => (VISION_PASSES as readonly string[]).includes(p))
  return { ok: true, value: activePasses }
}

function loadFramePasses(
  manifest: PassManifest,
  activePasses: VisionPassId[],
  resolution: [number, number],
  frame: FrameResult,
  warnings: string[],
  budget: PassBudget,
): Result<Partial<Record<VisionPassId, PngImage>>> {
  const loaded: Partial<Record<VisionPassId, PngImage>> = {}
  const degraded = new Set(manifest.degraded ?? [])
  for (const passId of activePasses) {
    const entry = manifest.passes[passId]
    // A missing pass is acceptable only when the manifest itself declared it
    // degraded for this frame; otherwise the batch contract is broken.
    const skipDegraded = (): boolean => {
      if (!degraded.has(passId)) return false
      const text =
        'frame_degraded: frame ' +
        String(manifest.frame) +
        ' 缺少 ' +
        passId +
        ' pass（manifest.degraded 已声明，帧统计不含该 pass）'
      frame.warnings.push(text)
      if (!warnings.includes(text)) warnings.push(text)
      return true
    }
    if (!entry) {
      if (skipDegraded()) continue
      return {
        ok: false,
        error: {
          ...failure(
            'vision_pass_manifest_invalid',
            'manifest 缺少 ' + passId + ' pass：' + manifest.file,
            {
              file: manifest.file,
              frame: manifest.frame,
              pass: passId,
              expected: activePasses,
              available: Object.keys(manifest.passes),
              degraded: [...degraded],
            },
          ),
          warnings,
        },
      }
    }
    const expected = passId === 'normal' ? [3, 4] : passId === 'alpha' ? [1, 2, 4] : [1, 2]
    const image = loadPassImage(entry.path, passId, resolution, expected, entry.maxValue, budget)
    if (!image.ok) {
      if (skipDegraded()) continue
      return { ok: false, error: { ...image.error, warnings } }
    }
    loaded[passId] = image.value
  }
  return { ok: true, value: loaded }
}

function analyzeFramePasses(
  manifest: PassManifest,
  loaded: Partial<Record<VisionPassId, PngImage>>,
  roiValue: ResolvedRoi,
  objectFilter: string[] | undefined,
  frame: FrameResult,
  warnings: string[],
  resolution: [number, number],
  budget: PassBudget,
): VisionFailure | null {
  if (loaded.depth && manifest.passes.depth) {
    frame.depth = analyzeDepth(loaded.depth, manifest.passes.depth, roiValue)
  }
  if (loaded.normal) {
    frame.normal = analyzeNormal(loaded.normal, roiValue)
  }
  if (loaded.alpha && manifest.passes.alpha) {
    frame.alpha = analyzeAlpha(loaded.alpha, roiValue)
  }
  if (loaded.object_index && manifest.passes.object_index) {
    // Depth is optional, but when the manifest has it we order objects by it even
    // when the caller only asked for object_index.
    if (!loaded.depth && manifest.passes.depth) {
      const extraDepth = loadPassImage(
        manifest.passes.depth.path,
        'depth',
        resolution,
        [1, 2],
        manifest.passes.depth.maxValue,
        budget,
      )
      if (!extraDepth.ok) return { ...extraDepth.error, warnings }
      loaded.depth = extraDepth.value
      frame.depth = analyzeDepth(loaded.depth, manifest.passes.depth, roiValue)
    }
    const objects = analyzeObjects(
      loaded.object_index,
      manifest.passes.object_index,
      roiValue,
      loaded.depth ?? null,
      manifest.passes.depth?.near ?? null,
      manifest.passes.depth?.far ?? null,
    )
    if (objects.unmapped_pixels > 0) {
      frame.warnings.push(
        'unmapped_object_index: 帧 ' +
          String(manifest.frame) +
          ' 有 ' +
          String(objects.unmapped_pixels) +
          ' 个采样像素的索引不在映射中（已忽略，不是背景）',
      )
    }
    if (objects.truncated) {
      frame.warnings.push(
        'frame_objects_truncated: 帧 ' +
          String(manifest.frame) +
          ' 有 ' +
          String(objects.object_count) +
          ' 个可见对象，逐帧只列前 ' +
          String(VISION_PASS_LIMITS.frameObjects) +
          ' 个（聚合覆盖全部）',
      )
    }
    if (objectFilter) {
      const wanted = new Set(objectFilter)
      const known = new Set(Object.values(manifest.passes.object_index.objects ?? {}))
      for (const name of wanted) {
        if (!known.has(name)) {
          frame.warnings.push('object_not_in_manifest: ' + name)
        }
      }
      objects.rows = objects.rows.filter((row) => wanted.has(row.name))
    }
    if (!loaded.depth) {
      frame.warnings.push('depth_order_unavailable: 没有 depth pass，前后顺序为 null')
    }
    frame.objects = objects
  }
  return null
}

function processFrame(
  manifest: PassManifest,
  activePasses: VisionPassId[],
  roiValue: ResolvedRoi,
  objectFilter: string[] | undefined,
  warnings: string[],
  resolution: [number, number],
  budget: PassBudget,
): Result<FrameResult> {
  const frame: FrameResult = {
    frame: manifest.frame,
    manifest: manifest.file,
    resolution: manifest.resolution,
    sampled: roiValue.sampled,
    warnings: [],
  }
  budget.frames += 1
  const loadedResult = loadFramePasses(manifest, activePasses, resolution, frame, warnings, budget)
  if (!loadedResult.ok) return loadedResult
  const analyzeError = analyzeFramePasses(
    manifest,
    loadedResult.value,
    roiValue,
    objectFilter,
    frame,
    warnings,
    resolution,
    budget,
  )
  if (analyzeError) return { ok: false, error: analyzeError }
  return { ok: true, value: frame }
}

function buildAggregate(
  frames: FrameResult[],
  manifests: PassManifest[],
  activePasses: VisionPassId[],
  objectsResult: {
    objects: Array<Record<string, unknown>>
    ordering: Record<string, unknown>
    bbox_overlap_order: Array<Record<string, unknown>>
  },
): Record<string, unknown> {
  const depthFrames = frames.filter((f) => f.depth)
  const normalFrames = frames.filter((f) => f.normal)
  const alphaFrames = frames.filter((f) => f.alpha)
  const range = (values: Array<number | null | undefined>) => {
    const clean = values.filter((v): v is number => typeof v === 'number')
    if (!clean.length) return null
    return {
      min: round(Math.min(...clean)),
      max: round(Math.max(...clean)),
      mean: round(clean.reduce((a, b) => a + b, 0) / clean.length),
    }
  }
  return {
    frame_count: frames.length,
    manifest_count: manifests.length,
    passes: [...activePasses],
    /** 聚合计数是否为采样值（任一帧 stride>1 即 true）。 */
    sampled: frames.some((frame) => frame.sampled),
    depth: depthFrames.length
      ? {
          unit: 'm',
          min: range(depthFrames.map((f) => f.depth?.min)),
          max: range(depthFrames.map((f) => f.depth?.max)),
          mean: range(depthFrames.map((f) => f.depth?.mean)),
          invalid_ratio: range(depthFrames.map((f) => f.depth?.invalid_ratio)),
          frames: depthFrames.length,
        }
      : null,
    normal: normalFrames.length
      ? {
          space: 'camera',
          angular_dispersion_deg: range(normalFrames.map((f) => f.normal?.angular_dispersion_deg)),
          valid_ratio: range(normalFrames.map((f) => f.normal?.valid_ratio)),
          frames: normalFrames.length,
        }
      : null,
    alpha: alphaFrames.length
      ? { coverage: range(alphaFrames.map((f) => f.alpha?.coverage)), frames: alphaFrames.length }
      : null,
    objects: objectsResult.objects,
    ordering: objectsResult.ordering,
    bbox_overlap_order: objectsResult.bbox_overlap_order,
  }
}

/** vision_pass 的运行选项：cwd + 可注入的单请求预算与帧间让出。 */
export interface VisionPassRunOptions {
  cwd?: string
  /** 单请求解码像素上限（默认 VISION_PASS_LIMITS.totalPixels）。 */
  totalPixels?: number
  /** 单请求时长上限（默认 VISION_PASS_LIMITS.totalMs）。 */
  totalMs?: number
  now?: () => number
  /** 帧间让出事件循环；默认 setImmediate（测试可注入）。 */
  yieldBetweenFrames?: () => Promise<void>
}

interface PassRun {
  manifests: PassManifest[]
  activePasses: VisionPassId[]
  roiValue: ResolvedRoi
  objectFilter: string[] | undefined
  warnings: string[]
  resolution: [number, number]
  frames: FrameResult[]
  seenFrames: Set<number>
  index: number
  budget: PassBudget
}

function preparePassRun(
  args: Record<string, unknown>,
  opts: VisionPassRunOptions,
): Result<PassRun> {
  const cwd = opts.cwd ?? process.cwd()
  const input = parseVisionInputArgs(args)
  if (!input.ok) return input
  const { manifestFiles, roiArg, requestedPasses, objectFilter } = input.value
  const warnings = input.value.warnings
  const manifests = loadAndValidateManifests(manifestFiles, cwd, warnings)
  if (!manifests.ok) return manifests
  const resolution = manifests.value[0].resolution
  const active = resolveActivePasses(manifests.value, requestedPasses, warnings)
  if (!active.ok) return active
  const roi = resolveRoi(roiArg, resolution[0], resolution[1])
  if (!roi.ok) return { ok: false, error: { ...roi.error, warnings } }
  const now = opts.now ?? Date.now
  const budget: PassBudget = {
    pixels: 0,
    frames: 0,
    startedAt: now(),
    limitPixels: opts.totalPixels ?? VISION_PASS_LIMITS.totalPixels,
    limitMs: opts.totalMs ?? VISION_PASS_LIMITS.totalMs,
    now,
  }
  return {
    ok: true,
    value: {
      manifests: manifests.value,
      activePasses: active.value,
      roiValue: roi.value,
      objectFilter,
      warnings,
      resolution,
      frames: [],
      seenFrames: new Set<number>(),
      index: 0,
      budget,
    },
  }
}

function processNextFrame(run: PassRun): Result<null> {
  const manifest = run.manifests[run.index]
  run.index += 1
  if (run.seenFrames.has(manifest.frame)) {
    run.warnings.push('duplicate_frame: ' + String(manifest.frame) + ' 出现在多个 manifest 中')
  }
  run.seenFrames.add(manifest.frame)
  if (manifest.degraded && manifest.degraded.length) {
    run.warnings.push(
      'frame_degraded: frame ' +
        String(manifest.frame) +
        ' 的 pass 降级/缺失：' +
        manifest.degraded.join(', '),
    )
  }
  const frameResult = processFrame(
    manifest,
    run.activePasses,
    run.roiValue,
    run.objectFilter,
    run.warnings,
    run.resolution,
    run.budget,
  )
  if (!frameResult.ok) return { ok: false, error: { ...frameResult.error, warnings: run.warnings } }
  run.frames.push(frameResult.value)
  return { ok: true, value: null }
}

function buildPassResult(run: PassRun): Record<string, unknown> {
  const aggregateObjectsResult = aggregateObjects(run.frames)
  run.warnings.push(...aggregateObjectsResult.warnings)
  const aggregate = buildAggregate(
    run.frames,
    run.manifests,
    run.activePasses,
    aggregateObjectsResult,
  )
  return {
    ok: true,
    op: 'vision_pass',
    roi: run.roiValue.sampled
      ? {
          x: run.roiValue.x,
          y: run.roiValue.y,
          w: run.roiValue.w,
          h: run.roiValue.h,
          sampled: true,
        }
      : {
          x: run.roiValue.x,
          y: run.roiValue.y,
          w: run.roiValue.w,
          h: run.roiValue.h,
          sampled: false,
        },
    resolution: run.resolution,
    limits: {
      manifests: VISION_PASS_LIMITS.manifests,
      objects: VISION_PASS_LIMITS.objects,
      frame_objects: VISION_PASS_LIMITS.frameObjects,
    },
    frames: run.frames,
    aggregate,
    warnings: run.warnings,
  }
}

function yieldToEventLoop(): Promise<void> {
  return new Promise<void>((resolve) => {
    setImmediate(resolve)
  })
}

/**
 * Run vision_pass synchronously (tests / callers outside the Gateway loop).
 * Pure host-side work: validate arguments, read manifests, decode pass images,
 * return bounded per-frame + aggregate numbers.
 */
export function runVisionPass(
  args: Record<string, unknown>,
  opts: VisionPassRunOptions = {},
): Record<string, unknown> {
  const prepared = preparePassRun(args, opts)
  if (!prepared.ok) return prepared.error
  const run = prepared.value
  while (run.index < run.manifests.length) {
    const step = processNextFrame(run)
    if (!step.ok) return step.error
    const over = budgetExceeded(run.budget)
    if (over) return { ...over, warnings: run.warnings }
  }
  return buildPassResult(run)
}

/**
 * Async variant used by dispatch: same rules, but yields to the event loop between
 * frames so addon RPC / heartbeats keep running while up to 240 frames decode.
 * Enforces the per-request pixel + wall-clock budget and returns it as a structured
 * vision_pass_budget_exceeded failure.
 */
export async function runVisionPassAsync(
  args: Record<string, unknown>,
  opts: VisionPassRunOptions = {},
): Promise<Record<string, unknown>> {
  const prepared = preparePassRun(args, opts)
  if (!prepared.ok) return prepared.error
  const run = prepared.value
  const yieldFrame = opts.yieldBetweenFrames ?? yieldToEventLoop
  while (run.index < run.manifests.length) {
    const step = processNextFrame(run)
    if (!step.ok) return step.error
    const over = budgetExceeded(run.budget)
    if (over) return { ...over, warnings: run.warnings }
    if (run.index < run.manifests.length) await yieldFrame()
  }
  return buildPassResult(run)
}
