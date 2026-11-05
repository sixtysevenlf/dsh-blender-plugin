// 作业脚本前导/正文拼装
import fs from 'node:fs'
import { GPU_PRELUDE, pyModuleFile, pyModuleNames } from '../../../paths.js'
import { slipFixPath } from '../../../core/output/index.js'
import {
  PATH_GUARD,
  envPrelude,
  scriptFileBlock,
  shotsBlock,
  workdirBlock,
} from '../../../core/scripts.js'
import { KERNEL_BOOTSTRAP } from '../../../core/kernel/index.js'
import { hardExitJobParts } from '../../../hard_exit.js'
import { writeHeadlessScript } from '../../../staging.js'
import type { JobsDeps } from '../types.js'
import type { JobBuildArgs } from './args.js'

export interface JobParts {
  parts: string[]
  jobShotsSpec: Record<string, unknown> | null
  sp: ReturnType<typeof writeHeadlessScript>
  timeoutMs: number
}

export function buildJobParts(
  E: JobsDeps,
  opts: Record<string, unknown>,
  a: JobBuildArgs,
): JobParts {
  const parts = [envPrelude(a.envPairs)]
  if (opts.bootstrap !== false) parts.push(KERNEL_BOOTSTRAP)
  parts.push('_dsh_stage_emit("engine-prelude")')
  parts.push(
    [
      'try:',
      '    _DSH_FP0 = bpy.context.scene.render.filepath',
      'except Exception:',
      '    _DSH_FP0 = None',
    ].join(String.fromCharCode(10)),
  )
  if (a.engineMode !== 'keep')
    parts.push(
      GPU_PRELUDE.replace(
        /__DSH_ENGINE__/g,
        "'" + (a.engineMode === 'cycles' ? 'cycles' : 'eevee') + "'",
      ).replace(/__DSH_GPU_MANUAL__/g, 'False'),
    )
  // v0.8.9：作业层也支持 preload（与 headless 同一套：把 runtime/py/<domain>/<name>.py 源码拼到脚本开头）
  const jobMods = Array.isArray(opts.preload)
    ? opts.preload
    : opts.preload
      ? String(opts.preload).split(',')
      : []
  // v0.9.3：作业层同样支持 shots（as_job=true 时 headless 就是转到这里跑的）
  const jobShotsSpec: Record<string, unknown> | null = Array.isArray(opts.shots)
    ? { views: opts.shots }
    : opts.shots && typeof opts.shots === 'object'
      ? Object.assign({}, opts.shots as Record<string, unknown>)
      : null
  const jobShotsWanted = !!(
    jobShotsSpec &&
    Array.isArray(jobShotsSpec.views) &&
    jobShotsSpec.views.length
  )
  if (jobShotsWanted && !jobMods.some((m) => String(m).trim().replace(/\.py$/, '') === 'qc_render'))
    jobMods.push('qc_render')
  for (const m of jobMods) {
    const name = String(m).trim().replace(/\.py$/, '')
    if (!name) continue
    // 模块真实文件在 runtime/py/<domain>/ 下（唯一清单 runtime/py/modules.json）；
    // 注入通道必须是真实实现文件，否则 exec 出去的只是 import 语句。
    let f: string | null
    try {
      f = pyModuleFile(name)
    } catch (e2) {
      f = null
    }
    if (!f || !fs.existsSync(f)) {
      // 报错直接给可用模块名 —— 现场「要 preload=vehicle」只能靠试。
      throw new Error(
        'preload 找不到模块：' +
          name +
          '（可用模块名：' +
          pyModuleNames().join(', ') +
          '）—— preload 取模块名（runtime/py/<domain>/<name>.py，清单 runtime/py/modules.json），例如 preload="vehicle,audit"；离线（后端挂了）见 runtime/py/core/offline_bootstrap.py',
      )
    }
    parts.push(E.preloadChunk(name))
  }
  parts.push('_dsh_stage_emit("preload-done")')
  if (opts.workdir) parts.push(workdirBlock(slipFixPath(String(opts.workdir), null)))
  if (a.scriptFileInfo) parts.push(scriptFileBlock(a.scriptFileInfo.resolved))
  parts.push(a.script)
  parts.push('_dsh_stage_emit("script-end")')
  if (jobShotsWanted)
    parts.push(
      shotsBlock(
        Object.assign({ engine: 'keep', lock: true, warmup: true, tag: 'shot' }, jobShotsSpec, {
          outdir: a.outdirWin,
        }),
      ),
    )
  parts.push(PATH_GUARD)
  parts.push('_dsh_stage_emit("done")')
  // F-14：done 之后冲刷 stdout/stderr 再 os._exit(0)（跳过 Blender 退出拆解；hardExit=false 回旧行为）
  for (const hx of hardExitJobParts(opts)) parts.push(hx)
  const sp = writeHeadlessScript(parts.join('\n'))
  const timeoutMs = Math.max(1000, Math.min(86400000, Number(opts.timeoutMs) || 3600000))
  return { parts, jobShotsSpec, sp, timeoutMs }
}

/** 脚本写盘 + Blender 参数收尾 */
export function finalizeJobArgs(
  a: JobBuildArgs,
  built: JobParts,
  opts: Record<string, unknown>,
): void {
  a.args.push('--python', built.sp.win, '--')
  if (opts.outdir) a.args.push(a.outdirWin)
  if (Array.isArray(opts.args)) for (const v of opts.args) a.args.push(String(v))
}
