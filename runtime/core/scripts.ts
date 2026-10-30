// Python 脚本前导/收尾：workdir、script-file 上下文、env、PATH_GUARD、shots、文件指纹（T6）
// @imports
import { IS_MAC, IS_WIN, IS_WSL } from "../config.js";
import fs from "node:fs";
import { createHash } from "node:crypto";
import { errorMessage } from "./json.js";


function workdirBlock(p: unknown): string {
  return ['# ---- DSH workdir ----',
    'try:',
    '    import os as _os, sys as _sys',
    '    _wd = K.win_path(' + JSON.stringify(String(p)) + ')',
    '    _os.chdir(_wd)',
    '    if _wd not in _sys.path: _sys.path.insert(0, _wd)',
    '    print("DSH_WORKDIR " + _wd)',
    'except Exception as _e:',
    '    print("DSH_WORKDIR_ERR " + str(_e)[:120])'].join('\n');
}

/**
 * v0.9.6（现场反馈 #4 · MK1 冒烟测试踩到）：script_file 的内容是**内联**进 dsh_headless_*.py 的，
 * 于是脚本里 __file__ 指向那个临时包装文件、脚本自己的目录也不在 sys.path —— 多文件工程第一行
 * import 就 ModuleNotFoundError: No module named 'spec'。这里显式把原路径与目录补给脚本。
 */
function scriptFileBlock(origWinPath: unknown): string {
  return ['# ---- DSH script-file context（v0.9.6 · 反馈 #4）----',
    'try:',
    '    import os as _osf, sys as _sysf',
    '    _sf = K.win_path(' + JSON.stringify(String(origWinPath)) + ')',
    '    __file__ = _sf',
    '    _sfd = _osf.path.dirname(_sf)',
    '    if _sfd and _sfd not in _sysf.path:',
    '        _sysf.path.insert(0, _sfd)',
    '    print("DSH_SCRIPT_FILE " + _sf)',
    'except Exception as _e:',
    '    print("DSH_SCRIPT_FILE_ERR " + str(_e)[:120])'].join('\n');
}

function envPrelude(envPairs: unknown): string {
  return ['# ---- DSH env prelude（子进程内可见；绕开 WSL interop 的 env 过滤）----',
    'try:',
    '    import os as _os_pre, json as _json_pre, time as _time_pre',
    '    _os_pre.environ.update(' + JSON.stringify(envPairs) + ')',
    '    def _dsh_stage_emit(name, **extra):',
    '        _d = {"name": str(name), "t": round(_time_pre.time(), 3), "run": _os_pre.environ.get("DSH_RUN_ID")}',
    '        _d.update({str(k): v for k, v in extra.items()})',
    '        print("DSH_STAGE " + _json_pre.dumps(_d, ensure_ascii=False), flush=True)',
    'except Exception:',
    '    def _dsh_stage_emit(name, **extra):',
    '        pass',
        'try:',
    '    def _dsh_progress_emit(**fields):',
    '        _dp = {"t": round(_time_pre.time(), 3), "run": _os_pre.environ.get("DSH_RUN_ID")}',
    '        _dp.update({str(k): v for k, v in fields.items()})',
    '        print("DSH_PROGRESS " + _json_pre.dumps(_dp, ensure_ascii=False), flush=True)',
    '        return _dp',
    'except Exception:',
    '    def _dsh_progress_emit(**fields):',
    '        pass',
    'dsh_progress = _dsh_progress_emit',
'dsh_stage = _dsh_stage_emit',
    '_dsh_stage_emit("boot")'].join('\n');
}

/**
 * v0.9.3（D4.3）：路径体检 —— Windows Blender 把 '/home/x' 当"当前盘根下的相对路径"，
 * 静默写到 C:\home\x（证据：Saved: 'C:homesixtyseven67DSH…' 紧随其后 os.path.exists() -> false）。
 * 这里在脚本收尾时检查 scene.render.filepath；配合 stdout 里的 Saved: 扫描（pathAudit）。
 *
 * 这条体检的前提是「WSL 宿主 + Windows 版 Blender」：只有那种组合才把 POSIX 绝对路径读错。
 * 原生 Linux 上 Blender 与宿主同机同 OS，'/home/x' 本来就是对的 ⇒ 必须关掉，否则每个正常脚本
 * 都回一条 `render_filepath_posix` 假警告，还把人往"改成 Windows 形式"的错方向引（issue #11）。
 */
export const PATH_GUARD = (IS_MAC || IS_WIN || !IS_WSL) ? '' : ['# ---- DSH 路径体检（v0.9.3 / D4.3）----',
  'try:',
  '    import json as _dsh_pj',
  '    _dsh_pw = []',
  '    _dsh_fp = bpy.context.scene.render.filepath',
  '    _dsh_fp0 = globals().get("_DSH_FP0")',
  '    if isinstance(_dsh_fp, str) and _dsh_fp.startswith("/") and not _dsh_fp.startswith("//") and _dsh_fp != _dsh_fp0:',
  '        _dsh_pw.append({"code": "render_filepath_posix", "value": _dsh_fp[:200],',
  '                        "hint": "scene.render.filepath 是 POSIX 绝对路径；Windows Blender 会把它写到 C:\\\\home\\\\… —— 用 K.win_path(p) 换成 Windows 形式"})',
  '    if _dsh_pw:',
  '        print("DSH_PATH_WARN " + _dsh_pj.dumps({"warnings": _dsh_pw}, ensure_ascii=False), flush=True)',
  'except Exception:',
  '    pass'].join('\n');

function shotsBlock(args: unknown): string {
  // ⚠ JSON.stringify 出来的是 JS 字面量（true/false/null），**不是 Python 字面量** —— 直接内联会 NameError: true。
  // 所以走 json.loads(<JSON 字符串字面量>)：既安全又保留 true/false/null 的语义。
  return ['# ---- DSH shots（v0.9.3 / F1）：多视角渲染一体化 ----',
    'import json as _dsh_sjson, bpy as _dsh_sbpy',
    '_dsh_shots_args = _dsh_sjson.loads(' + JSON.stringify(JSON.stringify(args)) + ')',
    'try:',
    '    _dsh_sc = _dsh_sbpy.context.scene',
    '    _dsh_shots_args.setdefault("res", [int(_dsh_sc.render.resolution_x), int(_dsh_sc.render.resolution_y)])',
    '    _dsh_shots_args.setdefault("samples", int(getattr(getattr(_dsh_sc, "eevee", None), "taa_render_samples", 0)',
    '                                              or getattr(getattr(_dsh_sc, "cycles", None), "samples", 0) or 64))',
    '    _dsh_sr = qc_render_views(_dsh_shots_args)',
    '    _dsh_shots_res = _dsh_sjson.loads(_dsh_sr) if isinstance(_dsh_sr, str) else _dsh_sr',
    'except Exception as _dsh_se:',
    '    import traceback as _dsh_stb',
    '    _dsh_shots_res = {"ok": False, "error": "%s: %s" % (type(_dsh_se).__name__, _dsh_se), "traceback": _dsh_stb.format_exc()[-2000:]}',
    'print("DSH_SHOTS " + _dsh_sjson.dumps(_dsh_shots_res, ensure_ascii=False), flush=True)'].join('\n');
}

export interface FileFingerprint {
  path: string | null;
  wsl: string | null;
  size?: number;
  mtimeMs?: number;
  mtime?: string;
  md5?: string;
  md5Partial?: boolean;
  md5Note?: string;
  error?: string;
}
/** 文件指纹（F6）：返回 {path,size,mtimeMs,md5,md5Partial}；>512MB 只哈希前 64MB 并标注 partial */
function fileFingerprint(winPath: unknown, wslPath: string): FileFingerprint {
  const out: FileFingerprint = { path: winPath ? String(winPath) : null, wsl: wslPath ? String(wslPath) : null };
  try {
    const st = fs.statSync(wslPath);
    out.size = st.size; out.mtimeMs = st.mtimeMs; out.mtime = new Date(st.mtimeMs).toISOString();
    const LIMIT = 64 * 1024 * 1024;
    if (st.size > 512 * 1024 * 1024) { out.md5 = md5File(wslPath, LIMIT); out.md5Partial = true; out.md5Note = '文件 >512MB：只哈希前 64MB（配合 size/mtime 判断变更足够）'; }
    else out.md5 = md5File(wslPath);
  } catch (e) {
    out.error = errorMessage(e).slice(0, 160);
  }
  return out;
}
function md5File(p: string, limit?: number): string {
  const h = createHash('md5');
  const fd = fs.openSync(p, 'r');
  try {
    const buf = Buffer.alloc(1 << 20);
    let total = 0;
    for (;;) {
      const n = fs.readSync(fd, buf, 0, buf.length, null);
      if (!n) break;
      if (limit && total + n > limit) { h.update(buf.subarray(0, Math.max(0, limit - total))); break; }
      h.update(buf.subarray(0, n)); total += n;
    }
  } finally { try { fs.closeSync(fd); } catch (e) { /* ignore */ } }
  return h.digest('hex');
}

export { workdirBlock, scriptFileBlock, envPrelude, shotsBlock, fileFingerprint, md5File };
