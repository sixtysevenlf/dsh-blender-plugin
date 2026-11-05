import {
  PATH_GUARD,
  envPrelude,
  scriptFileBlock,
  shotsBlock,
  workdirBlock,
} from '../../core/scripts.js'
import { KERNEL_BOOTSTRAP } from '../../core/kernel/index.js'
import { hardExitSyncTail } from '../../hard_exit.js'
import { writeHeadlessScript } from '../../staging.js'
import { slipFixPath } from '../../core/output/index.js'
import { wslToWin } from '../../config.js'
import type { PreparedHeadless } from './types.js'

export interface BuiltHeadlessScript {
  body: string
  args: string[]
  sp: ReturnType<typeof writeHeadlessScript> | null
}

/**
 * Assemble the Python prelude/body and Blender argv. Pure string construction except for
 * the temporary script file written by writeHeadlessScript (which is the staging seam).
 */
export function buildHeadlessScript(p: PreparedHeadless): BuiltHeadlessScript {
  const opts = p.opts
  const headParts: string[] = []
  // ---- v0.9.1（93-B4）：env 注入必须在 K 内核之前 —— PATH_HELPERS 里的 K.args/K.run_id 是从 os.environ 读的，
  // 而 WSL→Windows 的 env 跨界不可靠（实测 DSH_* 会被吃掉）→ 直接在脚本里 setdefault 一遍。
  headParts.push(envPrelude(p.envPairs))
  if (opts.bootstrap !== false) headParts.push(KERNEL_BOOTSTRAP)
  headParts.push('_dsh_stage_emit("engine-prelude")')
  // 基线：记下进入脚本前的 render.filepath（--factory-startup 的默认值本身就是 '/tmp\\' 这类
  // POSIX 串）—— 只有**脚本把它改成了 POSIX 绝对路径**才值得报警，否则全是假阳性。
  headParts.push(
    [
      'try:',
      '    _DSH_FP0 = bpy.context.scene.render.filepath',
      'except Exception:',
      '    _DSH_FP0 = None',
    ].join(String.fromCharCode(10)),
  )
  if (p.gpuPre) headParts.push(p.gpuPre)
  // v0.8.10：workdir → 脚本内 chdir + sys.path 首位（Blender 是 Windows 进程，必须过 K.win_path）
  if (opts.workdir) headParts.push(workdirBlock(slipFixPath(String(opts.workdir), null)))
  if (p.scriptFileInfo) headParts.push(scriptFileBlock(p.scriptFileInfo.resolved))
  const expectPre = p.exp
    ? [
        '# ---- DSH expect: before ----',
        'import json as _dsh_ejson',
        '_DSH_EXP0 = {"objects": len(bpy.data.objects), "meshes": len(bpy.data.meshes), "materials": len(bpy.data.materials)}',
      ].join(String.fromCharCode(10))
    : ''
  const expectPost = p.exp
    ? [
        '# ---- DSH expect: after ----',
        'try:',
        '    _DSH_EXP1 = {"objects": len(bpy.data.objects), "meshes": len(bpy.data.meshes), "materials": len(bpy.data.materials)}',
        '    print("DSH_EXPECT " + _dsh_ejson.dumps({"before": _DSH_EXP0, "after": _DSH_EXP1, "delta": {k: _DSH_EXP1[k] - _DSH_EXP0[k] for k in _DSH_EXP1}}))',
        'except Exception as _e:',
        '    print("DSH_EXPECT " + _dsh_ejson.dumps({"error": str(_e)[:160]}))',
      ].join(String.fromCharCode(10))
    : ''
  const NL = String.fromCharCode(10)
  // v0.9.3：shots 走 qc_render_views —— outdir 用 Windows 形态（Blender 侧），其余键由调用方定
  const shotsPre = p.shotsWanted
    ? shotsBlock(
        Object.assign({ engine: 'keep', lock: true, warmup: true, tag: 'shot' }, p.shotsSpec, {
          outdir: p.outdirWin,
        }),
      ) + NL
    : ''
  let body: string =
    p.script || p.pre || p.gpuPre || shotsPre
      ? (headParts.length ? headParts.join(NL) + NL : '') +
        (expectPre ? expectPre + NL : '') +
        p.pre +
        '_dsh_stage_emit("preload-done")' +
        NL +
        p.script +
        NL +
        '_dsh_stage_emit("script-end")' +
        NL +
        shotsPre +
        PATH_GUARD +
        NL +
        (expectPost ? expectPost : '') +
        NL +
        '_dsh_stage_emit("done")' +
        NL
      : ''
  // F-14：done 之后冲刷 stdout/stderr 再 os._exit(0)（跳过 Blender 退出拆解；hardExit=false 回旧行为）。
  // 空 body 不追加（与 2745 的 `if (body)` 同守卫）：无脚本的运行不该凭空多出一个"只有退出行"的 --python。
  if (body) body += hardExitSyncTail(opts, NL)
  const args: string[] = ['-b']
  if (p.fileOpt && !p.fileLooksPython) args.push(wslToWin(String(p.fileOpt)))
  if (opts.factoryStartup !== false) args.push('--factory-startup')
  let sp: ReturnType<typeof writeHeadlessScript> | null = null
  if (body) {
    sp = writeHeadlessScript(body)
    args.push('--python', sp.win)
  }
  args.push('--')
  if (opts.outdir) args.push(p.outdirWin)
  if (Array.isArray(opts.args)) args.push(...opts.args.map(String))
  return { body, args, sp }
}
