/**
 * blender_see 的参数校验：viewpoint / mode / 固定档位 / 各视角专属参数。
 *
 * 拆成小函数是为了守住函数预算；语义来自 tool-surface spec。校验失败返回
 * toolError envelope（{ text, envelope }），execute 层原样返回给模型。
 */
import { text } from '../../contracts/dynamic.js'
import { toolError } from '../errors.js'

const ASPECT_SIZES: Record<string, [number, number]> = {
  '1:1': [1, 1],
  '4:3': [4, 3],
  '16:9': [16, 9],
}
const MAX_SIZES: Record<string, number[]> = {
  '1:1': [544, 768, 1024, 1280],
  '4:3': [640, 960, 1280],
  '16:9': [1024, 1152, 1280],
}
const CAMERA_MAX_SIZES = [544, 768, 1024, 1280]
const DEFAULT_ASPECT = '16:9'

const CUSTOM_ONLY = ['lens', 'ortho', 'ortho_scale'] as const
const ASPECT_FIX =
  'aspect=1:1|4:3|16:9；max_size 必须是对应比例的固定档位（1:1: 544/768/1024/1280；4:3: 640/960/1280；16:9: 1024/1152/1280）'

export interface Dims {
  aspect: string
  maxSize: number
  width: number
  height: number
}

export interface FramePlan {
  kind: 'frame'
  viewpoint: 'viewport' | 'custom' | 'camera'
  dims?: Dims
  full?: boolean
  area?: number
  camera?: string
  from?: string
  lookAt?: string
  lens?: number
  ortho?: boolean
  orthoScale?: number
  shading?: string
  overlays?: boolean
  diagnostics?: boolean
  force: boolean
}

export interface SequencePlan {
  kind: 'sequence'
  viewpoint: 'viewport' | 'custom'
  dims: Dims
  from?: string
  lookAt?: string
  lens?: number
  ortho?: boolean
  orthoScale?: number
  shading?: string
  overlays?: boolean
  seconds: number
  fps: number
}

export type Plan = FramePlan | SequencePlan
export type Failure = { text: string; envelope: unknown }

export function dimsFor(aspect: string, maxSize?: unknown): Dims | null {
  const sizes = MAX_SIZES[aspect]
  if (!sizes) return null
  let size: number
  if (maxSize === undefined || maxSize === null) {
    // 只给 aspect 时取该比例的最小档；什么都没给时默认 16:9 + 1024
    if (aspect === DEFAULT_ASPECT) return { aspect, maxSize: 1024, width: 1024, height: 576 }
    size = sizes[0]
  } else {
    size = Number(maxSize)
    if (!sizes.includes(size)) return null
  }
  const [aw, ah] = ASPECT_SIZES[aspect]
  return { aspect, maxSize: size, width: size, height: Math.round((size * ah) / aw) }
}

function invalidParams(message: string, fix: string): Failure {
  return toolError('invalid_params', message, fix) as unknown as Failure
}

function has(v: unknown): boolean {
  return v !== undefined && v !== null && String(v).trim() !== ''
}

/** 固定档位解析；失败时给出统一 fix。 */
function dimsOrFailure(aspect: string, maxSize: unknown): Dims | Failure {
  const dims = dimsFor(aspect, maxSize)
  return dims || invalidParams('aspect/max_size 组合不合法', ASPECT_FIX)
}

function rejectCustomOnly(viewpoint: string, args: Record<string, unknown>): Failure | null {
  for (const k of CUSTOM_ONLY) {
    if (!has(args[k])) continue
    return invalidParams(
      'viewpoint=' + viewpoint + ' 不接受 ' + k,
      '要指定机位请用 viewpoint=custom + from/look_at',
    )
  }
  return null
}

function validateSequenceWindow(
  args: Record<string, unknown>,
): { seconds: number; fps: number } | Failure {
  const seconds = args.seconds === undefined ? 2 : Number(args.seconds)
  const fps = args.fps === undefined ? 4 : Number(args.fps)
  if (!(seconds >= 0.2 && seconds <= 20))
    return invalidParams('seconds 超出范围：0.2–20', '按需要缩短采样窗口')
  if (!(fps >= 0.5 && fps <= 8)) return invalidParams('fps 超出范围：0.5–8', '降低采样频率')
  const frames = Math.round(seconds * fps)
  if (!(frames >= 1 && frames <= 32))
    return invalidParams(
      'sequence 帧数超出 1–32（round(seconds*fps)）',
      '调整 seconds/fps，让 round(seconds*fps) 落在 1..32',
    )
  return { seconds, fps }
}

function sequenceRejections(args: Record<string, unknown>): Failure | null {
  if (has(args.full) || has(args.area))
    return invalidParams(
      'sequence 不支持 full/area',
      '整窗口截图只在 mode=frame + viewpoint=viewport 可用',
    )
  if (args.diagnostics === true)
    return invalidParams('sequence 不支持 diagnostics', '要逐帧诊断请拆成多次 mode=frame')
  if (has(args.code) || has(args.command))
    return invalidParams(
      'sequence 不执行 code/命令',
      '要边跑脚本边看，用 blender_py 的 see:true，或分两步：blender_py → blender_see mode=sequence',
    )
  if (has(args.camera))
    return invalidParams('sequence 不支持 camera', '见 blender_run(kind=render)')
  if (args.force === true)
    return invalidParams('sequence 不支持 force', '每一帧都会按 hash 去重后附图，无需 force')
  return null
}

function sequenceView(
  args: Record<string, unknown>,
  viewpoint: 'viewport' | 'custom',
): Failure | null {
  if (viewpoint === 'custom') {
    if (!has(args.from) || !has(args.look_at))
      return invalidParams(
        'custom 视角必须同时给 from 与 look_at',
        '例如 from="4,-4,3" look_at="0,0,1"',
      )
    return null
  }
  return rejectCustomOnly('viewport', args)
}

function buildSequence(
  args: Record<string, unknown>,
  viewpoint: 'viewport' | 'custom',
): SequencePlan | Failure {
  const rejected = sequenceRejections(args)
  if (rejected) return rejected
  const window = validateSequenceWindow(args)
  if ('text' in window) return window
  const dims = dimsOrFailure(text(args.aspect).trim() || DEFAULT_ASPECT, args.max_size)
  if ('text' in dims) return dims
  const viewRejected = sequenceView(args, viewpoint)
  if (viewRejected) return viewRejected
  return {
    kind: 'sequence',
    viewpoint,
    dims,
    from: text(args.from) || undefined,
    lookAt: text(args.look_at) || undefined,
    lens: args.lens === undefined ? undefined : Number(args.lens),
    ortho: args.ortho === undefined ? undefined : !!args.ortho,
    orthoScale: args.ortho_scale === undefined ? undefined : Number(args.ortho_scale),
    shading: text(args.shading) || undefined,
    overlays: args.overlays === undefined ? undefined : !!args.overlays,
    seconds: window.seconds,
    fps: window.fps,
  }
}

function cameraFrame(args: Record<string, unknown>): FramePlan | Failure {
  if (has(args.from) || has(args.look_at) || has(args.full) || has(args.area) || has(args.aspect))
    return invalidParams(
      'camera 视角不接受 from/look_at/full/area/aspect',
      'camera 用当前帧相机与渲染比例；max_size 只作长边上限（544/768/1024/1280）',
    )
  const rejected = rejectCustomOnly('camera', args)
  if (rejected) return rejected
  const size = args.max_size === undefined ? 1024 : Number(args.max_size)
  if (!CAMERA_MAX_SIZES.includes(size))
    return invalidParams(
      'max_size 只接受 544 / 768 / 1024 / 1280',
      'camera 按渲染比例缩放；改 max_size 控制长边',
    )
  const cameraArg = args.camera === true ? '' : text(args.camera).trim()
  return { kind: 'frame', viewpoint: 'camera', camera: cameraArg || undefined, force: !!args.force }
}

function viewportFrame(args: Record<string, unknown>): FramePlan | Failure {
  if (has(args.camera))
    return invalidParams('viewpoint=viewport 不接受 camera', '要跟场景相机请用 viewpoint=camera')
  if (has(args.from) || has(args.look_at))
    return invalidParams(
      'viewpoint=viewport 不接受 from/look_at',
      '要指定机位请用 viewpoint=custom + from/look_at',
    )
  const rejected = rejectCustomOnly('viewport', args)
  if (rejected) return rejected
  if (has(args.aspect) && args.full === true)
    return invalidParams('full=true 不与 aspect 组合', 'full=true 固定 1024 长边的整窗口截图')
  if (args.full === true)
    return {
      kind: 'frame',
      viewpoint: 'viewport',
      full: true,
      area: Number(args.area) || 0,
      force: !!args.force,
    }
  if (has(args.aspect)) {
    const dims = dimsOrFailure(text(args.aspect).trim(), args.max_size)
    if ('text' in dims) return dims
    return { kind: 'frame', viewpoint: 'viewport', dims, force: !!args.force }
  }
  const dims = dimsOrFailure(DEFAULT_ASPECT, args.max_size)
  if ('text' in dims) return dims
  return { kind: 'frame', viewpoint: 'viewport', dims, force: !!args.force }
}

function customFrame(args: Record<string, unknown>): FramePlan | Failure {
  if (has(args.full) || has(args.area))
    return invalidParams('custom 不支持 full/area', '整窗口截图只在 viewpoint=viewport + full=true')
  if (has(args.camera))
    return invalidParams('custom 不接受 camera', '要跟场景相机请用 viewpoint=camera')
  if (!has(args.from) || !has(args.look_at))
    return invalidParams(
      'custom 视角必须同时给 from 与 look_at',
      '例如 from="4,-4,3" look_at="0,0,1"',
    )
  const dims = dimsOrFailure(text(args.aspect).trim() || DEFAULT_ASPECT, args.max_size)
  if ('text' in dims) return dims
  return {
    kind: 'frame',
    viewpoint: 'custom',
    dims,
    from: text(args.from),
    lookAt: text(args.look_at),
    lens: args.lens === undefined ? undefined : Number(args.lens),
    ortho: args.ortho === undefined ? undefined : !!args.ortho,
    orthoScale: args.ortho_scale === undefined ? undefined : Number(args.ortho_scale),
    shading: text(args.shading) || undefined,
    overlays: args.overlays === undefined ? undefined : !!args.overlays,
    diagnostics: args.diagnostics === undefined ? undefined : !!args.diagnostics,
    force: !!args.force,
  }
}

function framePlan(args: Record<string, unknown>, viewpoint: string): FramePlan | Failure {
  if (has(args.seconds) || has(args.fps))
    return invalidParams('frame 不接受 seconds/fps', '要序列帧请用 mode=sequence')
  if (has(args.command))
    return invalidParams(
      'blender_see 不执行命令',
      '只读命令请用 blender_inspect；执行写命令用 blender_py',
    )
  if (viewpoint === 'camera') return cameraFrame(args)
  if (viewpoint === 'viewport') return viewportFrame(args)
  return customFrame(args)
}

export function validate(args: Record<string, unknown>): Plan | Failure {
  if (has(args.view_size))
    return invalidParams(
      'view_size 已删除：blender_see 用 aspect + max_size 固定档位',
      '给 aspect=1:1|4:3|16:9 与 max_size（1:1: 544/768/1024/1280；4:3: 640/960/1280；16:9: 1024/1152/1280）',
    )
  const rawView = text(args.viewpoint).trim()
  const mode = text(args.mode).trim() || 'frame'
  const hasAngles = has(args.from) || has(args.look_at)
  if (mode !== 'frame' && mode !== 'sequence')
    return invalidParams(
      'mode 只能是 frame 或 sequence',
      'mode=frame 取单帧，mode=sequence 取序列帧',
    )
  if (mode === 'sequence' && !rawView)
    return invalidParams(
      'sequence 必须显式给 viewpoint=viewport 或 custom',
      'sequence 不从 from/look_at 推断机位；相机动画请用 blender_run(kind=render)',
    )
  const viewpoint = rawView || (hasAngles ? 'custom' : 'viewport')
  if (viewpoint !== 'viewport' && viewpoint !== 'custom' && viewpoint !== 'camera')
    return invalidParams(
      'viewpoint 只能是 viewport / camera / custom',
      'viewport=当前 GUI 视口；camera=当前帧相机；custom=from+look_at',
    )
  if (mode === 'sequence') {
    if (viewpoint !== 'viewport' && viewpoint !== 'custom')
      return invalidParams(
        'sequence 只支持 viewpoint=viewport 或 custom',
        '相机动画请用 blender_run(kind=render, camera=..., frames=[start,end])；sequence 必须显式给 viewpoint',
      )
    return buildSequence(args, viewpoint as 'viewport' | 'custom')
  }
  return framePlan(args, viewpoint)
}
