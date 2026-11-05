// headless 选项：preload 与 GPU/引擎前导
import fs from 'node:fs'
import { GPU_PRELUDE, pyModuleFile } from '../../../paths.js'
import type { HeadlessDeps, HeadlessOpts } from '../types.js'

export interface HeadlessPreload {
  mods: unknown[]
  pre: string
  shotsSpec: Record<string, unknown> | null
  shotsWanted: boolean
}

export function buildHeadlessPreload(opts: HeadlessOpts, deps: HeadlessDeps): HeadlessPreload {
  // preload: 把 runtime 里的 python 模块（view/perf/runner/contract/planner...）源码拼进脚本开头
  const mods: unknown[] = Array.isArray(opts.preload)
    ? opts.preload
    : opts.preload
      ? String(opts.preload).split(',')
      : []
  // ---- v0.9.3：shots=[…] 多视角渲染一体化 —— 自动带上 qc_render（渲染 harness 就在里面）
  const shotsSpec: Record<string, unknown> | null = Array.isArray(opts.shots)
    ? { views: opts.shots }
    : opts.shots && typeof opts.shots === 'object'
      ? Object.assign({}, opts.shots as Record<string, unknown>)
      : null
  const shotsWanted = !!(shotsSpec && Array.isArray(shotsSpec.views) && shotsSpec.views.length)
  if (shotsWanted && !mods.some((m) => String(m).trim().replace(/\.py$/, '') === 'qc_render'))
    mods.push('qc_render')
  let pre = ''
  for (const m of mods) {
    const name = String(m).trim().replace(/\.py$/, '')
    if (!name) continue
    // 同作业层，取 runtime/py/<domain>/ 的真实文件（旧路径是 shim）。
    const f = pyModuleFile(name)
    if (!fs.existsSync(f)) throw new Error('preload 找不到模块：' + name + '（' + f + '）')
    pre += deps.preloadChunk(name) + '\n'
  }
  return { mods, pre, shotsSpec, shotsWanted }
}

export interface HeadlessEngine {
  engineMode: string
  gpuOff: boolean
  gpuManual: boolean
  gpuPre: string
}

export function buildEnginePrelude(opts: HeadlessOpts): HeadlessEngine {
  // ---- 引擎前导（v0.8.0）：默认 eevee + 光追；可选 cycles / keep（向后兼容 gpu:"false"=keep）
  const engineMode = String(
    opts.engine === undefined || opts.engine === null ? 'eevee' : opts.engine,
  ).toLowerCase()
  const legacyGpu = String(
    opts.gpu === undefined || opts.gpu === null ? '' : opts.gpu,
  ).toLowerCase()
  const gpuOff =
    engineMode === 'keep' ||
    engineMode === 'off' ||
    engineMode === 'none' ||
    legacyGpu === 'false' ||
    legacyGpu === 'off' ||
    legacyGpu === '0'
  const gpuManual = legacyGpu === 'true' || legacyGpu === 'required'
  let gpuPre = ''
  if (!gpuOff) {
    const modeLit = engineMode === 'cycles' || engineMode === 'cycle' ? 'cycles' : 'eevee'
    gpuPre =
      '# ---- DSH engine prelude (' +
      modeLit +
      ') ----\n' +
      GPU_PRELUDE.replace(/__DSH_ENGINE__/g, "'" + modeLit + "'").replace(
        /__DSH_GPU_MANUAL__/g,
        gpuManual ? 'True' : 'False',
      ) +
      '\n'
  }
  return { engineMode, gpuOff, gpuManual, gpuPre }
}
