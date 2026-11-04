import fs from 'node:fs'
import path from 'node:path'
import {
  pyModuleFile,
  ASSEMBLY_PATH,
  AUDIT_PATH,
  BLOCKOUT_PATH,
  CALIB_PATH,
  CLEARANCE_PATH,
  CONTRACT_PATH,
  DELIVER_PATH,
  EVALHARNESS_PATH,
  FACE_PATH,
  FIX_PATH,
  GATE_PATH,
  GENERATOR_PATH,
  HUMAN_PATH,
  IMG_PATH,
  MATERIAL_PATH,
  MONTAGE_PATH,
  MOTION_PATH,
  PERF_PATH,
  PIPELINE_PATH,
  PLANNER_PATH,
  PRESET_PATH,
  PRINT_PATH,
  QC_PATH,
  QC_RENDER_PATH,
  RENDER_GUARD_PATH,
  RUNNER_PATH,
  SCULPT_PATH,
  SHAPESOLVE_PATH,
  SHAPE_PATH,
  SURFACE_PATH,
  SWEEP_PATH,
  TXN_PATH,
  UV_PATH,
  VEHICLE_PATH,
  VIEW_PATH,
  VISUALREPORT_PATH,
  KIT_HASH,
} from '../../paths.js'
import { slipFixPath } from '../output/index.js'
import { winToWsl, wslToWin } from '../../config.js'
import { KERNEL_BOOTSTRAP } from './bootstrap.js'
import type { AddonClient } from '../../transport/probes.js'

export interface KernelDeps {
  RENDER_FLAG_WIN: string
  addon: AddonClient
  injected: Set<string>
}

export interface KernelIoDeps {
  addon: AddonClient
  injected: Set<string>
}

const MODULE_ATTR: Record<string, string> = {
  RUNNER_READY: 'dsh_loop_api',
  PERF_READY: 'dsh_perf_api',
  VIEW_READY: 'dsh_view_api',
  CONTRACT_READY: 'dsh_contract_api',
  PLAN_READY: 'dsh_plan_api',
  TXN_READY: 'dsh_txn_api',
  QC_READY: 'dsh_qc_api',
  GEO_READY: 'dsh_geo_api',
  QC_RENDER_READY: 'dsh_qc_render_api',
  PRESET_READY: 'dsh_preset_api',
  AUDIT_READY: 'dsh_audit_api',
  MONTAGE_READY: 'dsh_montage_api',
  MOTION_READY: 'dsh_motion_api',
  DELIVER_READY: 'dsh_deliver_api',
  GENERATOR_READY: 'dsh_generator_api',
  SCULPT_READY: 'dsh_sculpt_api',
  FIX_READY: 'dsh_fix_api',
  UV_READY: 'dsh_uv_api',
  PRINT_READY: 'dsh_print_api',
  SWEEP_READY: 'dsh_sweep_api',
  MATERIAL_READY: 'dsh_material_api',
  RENDER_GUARD_READY: 'dsh_render_guard',
  GATE_READY: 'dsh_gate_api',
  IMG_READY: 'dsh_img_api',
  CALIB_READY: 'dsh_calib_api',
  FACE_READY: 'dsh_face_api',
  HUMAN_READY: 'dsh_human_api',
  CLEARANCE_READY: 'dsh_clearance_api',
  VEHICLE_READY: 'dsh_vehicle_api',
  SHAPE_READY: 'dsh_shape_api',
  PIPELINE_READY: 'dsh_pipe_api',
  ASSEMBLY_READY: 'dsh_assembly_api',
  BLOCKOUT_READY: 'dsh_blockout_api',
  VISUALREPORT_READY: 'dsh_visualreport_api',
  SHAPESOLVE_READY: 'dsh_shapesolve_api',
  EVALHARNESS_READY: 'dsh_evalharness_api',
  SURFACE_READY: 'dsh_surface_api',
}

/** 读 runtime 下的 python 模块源码（preload / 作业脚本拼接用） */
export function readModuleSource(name: string): string {
  return fs.readFileSync(pyModuleFile(name), 'utf8')
}
/**
 * v0.9.1（93-B3）：脚本路径解析。headless 的 `file=` 语义是 **.blend**（老成员误传 .py 会拿到
 * `File format is not supported`），现在：显式 `scriptFile=` 收 .py；`file=` 传 .py 时自动改当脚本并给提示。
 * Windows 路径（D:\ 或 \\wsl.localhost\）与 WSL 路径（/home/...）都能收。
 */
export function readScriptPath(p: unknown): { win: string; wsl: string } {
  const s = slipFixPath(String(p), null) // v0.9.6 现场反馈：漏 / 的 WSL 路径先纠正
  const isWin = /^[A-Za-z]:[\\/]/.test(s) || s.slice(0, 2) === '\\\\'
  return { win: isWin ? s : wslToWin(s), wsl: isWin ? winToWsl(s) : s }
}
/**
 * v0.9.0：preload 多个模块必须各占一个命名空间。
 * 旧实现把多个模块源码**拼进同一个 globals**，而每个 runtime 模块都定义 _j/_kernel/_store/_now →
 * 后一个模块把前一个的 helper 顶掉。实测 `preload="txn,deliver"` 直接把 txn 打崩（KeyError: 'marks'）。
 * 做法：每个模块 exec 到自己的 dict；私有名（_ 开头）不外泄，公开名与常用 import 照旧拷回 globals
 * （向后兼容：以前直接调 `dsh_loop_start` / `audit_mesh` 这类公开函数的写法仍然可用）。
 * 模块尾部 `K.dsh_x_api = {...}` 走的是真实的 dsh_rt_kernel 模块对象 → API 照旧落在 K 上。
 */
export function preloadChunk(name: unknown): string {
  const clean = String(name).trim().replace(/\.py$/, '')
  const file = pyModuleFile(clean)
  const src = fs.readFileSync(file, 'utf8')
  const ns = '__dsh_ns_' + clean.replace(/[^A-Za-z0-9_]/g, '_')
  return [
    '# ---- preload ' + clean + '.py（独立命名空间：私有 helper 不互相覆写）----',
    ns + ' = {"__name__": ' + JSON.stringify(clean) + ', "__file__": ' + JSON.stringify(file) + '}',
    'exec(compile(' +
      JSON.stringify(src) +
      ', ' +
      JSON.stringify(clean + '.py') +
      ', "exec"), ' +
      ns +
      ')',
    'globals().update({k: v for k, v in ' + ns + '.items() if not k.startswith("_")})',
  ].join(String.fromCharCode(10))
}
export async function injectModule(
  deps: KernelIoDeps,
  file: string,
  marker: string,
  versionExpr = '1',
  prelude = '',
): Promise<boolean> {
  const attr = MODULE_ATTR[marker] || 'dsh_' + String(marker).toLowerCase() + '_api'
  const hashAttr = attr + '_fp'
  // 内容指纹（size + mtime）：模块文件一改，下次调用自动重新注入 —— 开发闭环必需。
  // 注意：K 跨后端重启保留，所以"只查 hasattr"会让改了文件却不生效（v0.7.0 修）。
  let fp: string
  try {
    const st = fs.statSync(file)
    fp = String(st.size) + '-' + String(Math.round(st.mtimeMs)) + '-k' + KIT_HASH
  } catch (e) {
    fp = 'missing-k' + KIT_HASH
  }
  // K 才是真相：Blender 重启后 K 会清空，仅靠本地 Set 会误判"已注入"
  try {
    // noMark：加载 python 模块是纯 plumbing，不可能改场景 —— 不该参与 stale 判定
    const chk = await deps.addon.send(
      'execute_code',
      {
        code:
          KERNEL_BOOTSTRAP +
          '\nprint("HAVE " + str(hasattr(K, "' +
          attr +
          '")) + " FP " + str(getattr(K, "' +
          hashAttr +
          '", "none")))',
      },
      30000,
      { noMark: true },
    )
    const chkRec = chk !== null && typeof chk === 'object' ? (chk as Record<string, unknown>) : null
    const t = chkRec && typeof chkRec.result === 'string' ? chkRec.result : ''
    if (t.includes('HAVE True') && t.includes(fp)) {
      deps.injected.add(file)
      return true
    }
    deps.injected.delete(file)
  } catch (e) {
    /* 探测失败 → 走重新注入 */
  }
  const src = fs.readFileSync(file, 'utf8')
  const tail =
    '\nK.' +
    hashAttr +
    ' = ' +
    JSON.stringify(fp) +
    '\nprint("' +
    marker +
    ' v%d" % ' +
    versionExpr +
    ')'
  const out = await deps.addon.send(
    'execute_code',
    {
      code:
        KERNEL_BOOTSTRAP +
        '\n__file__ = ' +
        JSON.stringify(file) +
        '\n' +
        (prelude ? prelude + '\n' : '') +
        src +
        tail,
    },
    60000,
    { noMark: true },
  )
  const outRec = out !== null && typeof out === 'object' ? (out as Record<string, unknown>) : null
  const txt = outRec && typeof outRec.result === 'string' ? outRec.result : ''
  if (!txt.includes(marker))
    throw new Error(path.basename(file) + ' 注入失败: ' + txt.slice(0, 200))
  deps.injected.add(file)
  return true
}
export function needFile(f: string, what: string): string {
  if (!fs.existsSync(f))
    throw new Error(what + ' 模块还没就位：' + path.basename(f) + '（v0.9.0 开发中）')
  return f
}

const ensureRunner = (d: KernelIoDeps) =>
  injectModule(d, RUNNER_PATH, 'RUNNER_READY', 'RUNNER_VERSION')
const ensurePerf = (d: KernelIoDeps) => injectModule(d, PERF_PATH, 'PERF_READY', 'PERF_VERSION')
const ensureView = (d: KernelIoDeps) => injectModule(d, VIEW_PATH, 'VIEW_READY', 'VIEW_VERSION')
const ensureContract = (d: KernelIoDeps) =>
  injectModule(d, CONTRACT_PATH, 'CONTRACT_READY', 'CONTRACT_VERSION')
const ensurePlanner = (d: KernelIoDeps) =>
  injectModule(d, PLANNER_PATH, 'PLAN_READY', 'PLAN_VERSION')
const ensureTxn = (d: KernelIoDeps) => injectModule(d, TXN_PATH, 'TXN_READY', 'TXN_VERSION')
const ensureQc = (d: KernelIoDeps) => injectModule(d, QC_PATH, 'QC_READY', 'QC_VERSION')
const ensureQcRender = (d: KernelIoDeps) =>
  injectModule(d, QC_RENDER_PATH, 'QC_RENDER_READY', 'QC_RENDER_VERSION')
const ensurePreset = (d: KernelIoDeps) =>
  injectModule(d, PRESET_PATH, 'PRESET_READY', 'PRESET_VERSION')
const ensureAudit = (d: KernelIoDeps) => injectModule(d, AUDIT_PATH, 'AUDIT_READY', 'AUDIT_VERSION')
const ensureMontage = (d: KernelIoDeps) =>
  injectModule(d, MONTAGE_PATH, 'MONTAGE_READY', 'MONTAGE_VERSION')
const ensureMotion = (d: KernelIoDeps) =>
  injectModule(d, needFile(MOTION_PATH, 'motion'), 'MOTION_READY', 'MOTION_VERSION')
const ensureDeliver = (d: KernelIoDeps) =>
  injectModule(d, needFile(DELIVER_PATH, 'deliver'), 'DELIVER_READY', 'DELIVER_VERSION')
const ensureGenerator = (d: KernelIoDeps) =>
  injectModule(d, needFile(GENERATOR_PATH, 'generator'), 'GENERATOR_READY', 'GENERATOR_VERSION')
// v0.9.6（上游整合）：五个新模块都走 needFile —— 还没落盘时给清楚的错，而不是 ENOENT
const ensureSculpt = (d: KernelIoDeps) =>
  injectModule(d, needFile(SCULPT_PATH, 'sculpt'), 'SCULPT_READY', 'SCULPT_VERSION')
const ensureFix = (d: KernelIoDeps) =>
  injectModule(d, needFile(FIX_PATH, 'mesh_fix'), 'FIX_READY', 'FIX_VERSION')
const ensureUv = (d: KernelIoDeps) =>
  injectModule(d, needFile(UV_PATH, 'uv_tools'), 'UV_READY', 'UV_VERSION')
const ensurePrint = (d: KernelIoDeps) =>
  injectModule(d, needFile(PRINT_PATH, 'printcheck'), 'PRINT_READY', 'PRINT_VERSION')
const ensureSweep = (d: KernelIoDeps) =>
  injectModule(d, needFile(SWEEP_PATH, 'sweep'), 'SWEEP_READY', 'SWEEP_VERSION')
const ensureMaterial = (d: KernelIoDeps) =>
  injectModule(d, needFile(MATERIAL_PATH, 'material'), 'MATERIAL_READY', 'MAT_VERSION')
// 门包会按 spec 调其它模块：先把常用的几个备好（各自注入一次，之后走缓存）
const ensureImg = (d: KernelIoDeps) =>
  injectModule(d, needFile(IMG_PATH, 'imgtools'), 'IMG_READY', 'IMG_VERSION')
// human_spec 要用 imgtools 量参考图 ⇒ 链路里带上
const ensurePipeline = (d: KernelIoDeps) =>
  injectModule(d, needFile(PIPELINE_PATH, 'pipeline'), 'PIPELINE_READY', 'PIPE_VERSION')
const ensureAssembly = (d: KernelIoDeps) =>
  injectModule(d, needFile(ASSEMBLY_PATH, 'assembly'), 'ASSEMBLY_READY', 'ASSEMBLY_VERSION')
const ensureBlockout = (d: KernelIoDeps) =>
  injectModule(d, needFile(BLOCKOUT_PATH, 'blockout'), 'BLOCKOUT_READY', 'BLOCKOUT_VERSION')
const ensureVisualReport = (d: KernelIoDeps) =>
  injectModule(
    d,
    needFile(VISUALREPORT_PATH, 'visualreport'),
    'VISUALREPORT_READY',
    'VISUALREPORT_VERSION',
  )
const ensureShapesolve = (d: KernelIoDeps) =>
  injectModule(d, needFile(SHAPESOLVE_PATH, 'shapesolve'), 'SHAPESOLVE_READY', 'SHAPESOLVE_VERSION')
const ensureEvalharness = (d: KernelIoDeps) =>
  injectModule(
    d,
    needFile(EVALHARNESS_PATH, 'evalharness'),
    'EVALHARNESS_READY',
    'EVALHARNESS_VERSION',
  )
const ensureSurface = (d: KernelIoDeps) =>
  injectModule(d, needFile(SURFACE_PATH, 'surface'), 'SURFACE_READY', 'SURFACE_VERSION')
const ensureShape = async (d: KernelIoDeps) => {
  await ensureVehicle(d)
  return injectModule(d, needFile(SHAPE_PATH, 'shapegen'), 'SHAPE_READY', 'SHAPE_VERSION')
}
const ensureClearance = (d: KernelIoDeps) =>
  injectModule(d, needFile(CLEARANCE_PATH, 'clearance'), 'CLEARANCE_READY', 'CLEAR_VERSION')
// vehicle_sections 要 imgtools 的 col_profile、panels 之后常配 clearance 验收 ⇒ 链路带上
const ensureVehicle = async (d: KernelIoDeps) => {
  await ensureImg(d)
  await ensureClearance(d)
  return injectModule(d, needFile(VEHICLE_PATH, 'vehicle'), 'VEHICLE_READY', 'VEHICLE_VERSION')
}
const ensureHuman = async (d: KernelIoDeps) => {
  await ensureImg(d)
  return injectModule(d, needFile(HUMAN_PATH, 'human'), 'HUMAN_READY', 'HUMAN_VERSION')
}
const ensureFace = (d: KernelIoDeps) =>
  injectModule(d, needFile(FACE_PATH, 'faceeval'), 'FACE_READY', 'FACE_VERSION')
const ensureCalib = async (d: KernelIoDeps) => {
  await ensureGate(d)
  return injectModule(d, needFile(CALIB_PATH, 'calib'), 'CALIB_READY', 'CALIB_VERSION')
}
const ensureGate = async (d: KernelIoDeps) => {
  for (const f of [
    ensureAudit,
    ensurePrint,
    ensureUv,
    ensureMaterial,
    ensureFix,
    ensureHuman,
    ensureAssembly,
  ]) {
    try {
      await f(d)
    } catch (e) {
      /* 某个模块注入失败不该拦下整包：gate 会按门报"该门需要模块 X" */
    }
  }
  return injectModule(d, needFile(GATE_PATH, 'gate'), 'GATE_READY', 'GATE_VERSION')
}
// 渲染 guard 必须把标记文件路径注入进去（Windows 形式；handler 闭包持有该命名空间）
const ensureRenderGuard = (d: KernelIoDeps, renderFlagWin: string) =>
  injectModule(
    d,
    needFile(RENDER_GUARD_PATH, 'render_guard'),
    'RENDER_GUARD_READY',
    'RG_VERSION',
    'DSH_RENDER_FLAG_WIN = ' + JSON.stringify(renderFlagWin),
  )

export async function sceneEpoch(deps: KernelIoDeps): Promise<unknown> {
  try {
    const out = await deps.addon.send(
      'execute_code',
      { code: KERNEL_BOOTSTRAP + '\nprint("EPOCH " + _dsh_epoch_json())' },
      20000,
    )
    const outRec = out !== null && typeof out === 'object' ? (out as Record<string, unknown>) : null
    const t = outRec && typeof outRec.result === 'string' ? outRec.result : ''
    const l = t.split('\n').filter((x: string) => x.indexOf('EPOCH ') === 0)
    return l.length ? JSON.parse(l[l.length - 1].slice('EPOCH '.length)) : null
  } catch (e) {
    return null
  }
}

const createEnsures = (
  d: KernelIoDeps,
  renderFlagWin: string,
): Record<string, () => Promise<boolean>> => ({
  ensureRunner: () => ensureRunner(d),
  ensurePerf: () => ensurePerf(d),
  ensureView: () => ensureView(d),
  ensureContract: () => ensureContract(d),
  ensurePlanner: () => ensurePlanner(d),
  ensureTxn: () => ensureTxn(d),
  ensureQc: () => ensureQc(d),
  ensureQcRender: () => ensureQcRender(d),
  ensurePreset: () => ensurePreset(d),
  ensureAudit: () => ensureAudit(d),
  ensureMontage: () => ensureMontage(d),
  ensureMotion: () => ensureMotion(d),
  ensureDeliver: () => ensureDeliver(d),
  ensureGenerator: () => ensureGenerator(d),
  ensureSculpt: () => ensureSculpt(d),
  ensureFix: () => ensureFix(d),
  ensureUv: () => ensureUv(d),
  ensurePrint: () => ensurePrint(d),
  ensureSweep: () => ensureSweep(d),
  ensureMaterial: () => ensureMaterial(d),
  ensureImg: () => ensureImg(d),
  ensurePipeline: () => ensurePipeline(d),
  ensureAssembly: () => ensureAssembly(d),
  ensureBlockout: () => ensureBlockout(d),
  ensureVisualReport: () => ensureVisualReport(d),
  ensureShapesolve: () => ensureShapesolve(d),
  ensureEvalharness: () => ensureEvalharness(d),
  ensureSurface: () => ensureSurface(d),
  ensureShape: () => ensureShape(d),
  ensureClearance: () => ensureClearance(d),
  ensureVehicle: () => ensureVehicle(d),
  ensureHuman: () => ensureHuman(d),
  ensureFace: () => ensureFace(d),
  ensureCalib: () => ensureCalib(d),
  ensureGate: () => ensureGate(d),
  ensureRenderGuard: () => ensureRenderGuard(d, renderFlagWin),
})

export function createKernelInternals(E: KernelDeps) {
  const { RENDER_FLAG_WIN: renderFlagWin, addon, injected } = E
  const d: KernelIoDeps = { addon, injected }
  return {
    MODULE_ATTR,
    readModuleSource,
    readScriptPath,
    preloadChunk,
    injectModule: (file: string, marker: string, versionExpr = '1', prelude = '') =>
      injectModule(d, file, marker, versionExpr, prelude),
    needFile,
    ...createEnsures(d, renderFlagWin),
    sceneEpoch: () => sceneEpoch(d),
  }
}
