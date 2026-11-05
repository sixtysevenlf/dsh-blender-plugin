// headless 选项：路径/脚本/输入文件解析
import fs from 'node:fs'
import { winToWsl, wslToWin } from '../../../config.js'
import { WIN_TMP } from '../../../paths.js'
import { describeMissingInput, validateFileOpt } from '../../../file_guard.js'
import { fileFingerprint } from '../../../core/scripts.js'
import { errorMessage } from '../../../core/json.js'
import { slipFixPath } from '../../../core/output/index.js'
import type { HeadlessDeps, HeadlessOpts, ScriptFileInfo } from '../types.js'

export interface HeadlessPaths {
  outdirArg: string | null
  outdirWsl: string | null
  outdirWin: string
  scriptFileInfo: ScriptFileInfo | null
  fileOpt: string | undefined
  fileLooksPython: boolean
  inputFileInfo: unknown
  script: string
}

export function resolveHeadlessPaths(opts: HeadlessOpts, deps: HeadlessDeps): HeadlessPaths {
  // ---- v0.9.3：outdir 接受 WSL 路径（/home/…、/mnt/d/…）与 Windows 路径。
  // 旧版把入参**原样**塞给 Windows 的 blender.exe → '/home/x' 被当相对盘根，静默写到 C:\home\x。
  // 现在：Node 侧用 outdirWsl 读写、Blender 侧用 outdirWin（/home/… → \\wsl.localhost\<distro>\home\…）。
  const outdirArg = opts.outdir ? slipFixPath(String(opts.outdir), null) : null
  const outdirWsl = outdirArg ? winToWsl(outdirArg) : null
  const outdirWin = outdirArg ? wslToWin(outdirArg) : WIN_TMP
  // v0.9.1（93-B3）：scriptFile= 显式收 .py；file= 传 .py 时自动改当脚本（老语义 file= 仍是 .blend）
  let scriptFileInfo: ScriptFileInfo | null = null
  let script = opts.script ? String(opts.script) : ''
  const fileOpt = validateFileOpt(opts.file) // F-09：空串/类型错显式报错（直连 /headless 的兜底；工具端另有 ALIAS 拦截）
  const fileLooksPython = !!(fileOpt && /\.py$/i.test(String(fileOpt)))
  if (opts.scriptFile || fileLooksPython) {
    const given = String(opts.scriptFile || fileOpt)
    try {
      const rp = deps.readScriptPath(given)
      script = fs.readFileSync(rp.wsl, 'utf8')
      scriptFileInfo = {
        given: given,
        resolved: rp.win,
        bytes: script.length,
        auto_from_file: !opts.scriptFile && fileLooksPython,
      }
    } catch (e) {
      throw Object.assign(
        new Error('读不到脚本文件：' + given + ' —— ' + errorMessage(e).slice(0, 160)),
        {
          hint: 'scriptFile 用 .py 路径（Windows D:\\… 或 WSL /home/… 都行）；file= 是 .blend',
        },
      )
    }
  }
  // ---- v0.9.3：inputFile 指纹 —— 探针跑在 02:55 版、04:07 才发现文件已被重存（浪费 4 次探针）
  // （必须在 fileLooksPython 之后：否则 TDZ ReferenceError —— 实测踩过）
  const inputFileInfo =
    fileOpt && !fileLooksPython
      ? fileFingerprint(wslToWin(String(fileOpt)), winToWsl(String(fileOpt)))
      : describeMissingInput(fileOpt, fileLooksPython) // F-09：null → {given,opened,reason}（形态冻结：truthy 分支一字未动）
  return {
    outdirArg,
    outdirWsl,
    outdirWin,
    scriptFileInfo,
    fileOpt,
    fileLooksPython,
    inputFileInfo,
    script,
  }
}
