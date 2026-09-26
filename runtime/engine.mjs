/**
 * Blender 实时交互引擎 —— 直连 addon socket（不经 MCP/python）。
 *
 *  - 会话：TCP 127.0.0.1:9876，JSON {type, params} → {status, result}，按序匹配 → 队列串行化
 *  - 取帧：addon 离屏渲染（GPUOffScreen）到**固定** Windows 路径（覆盖写，WSL 侧只读读回，无残留）
 *  - 执行：execute_code + 持久内核 K（sys.modules 承载状态，实现 REPL 语义）
 *  - 命令：addon 命令表全部可调（见 COMMAND_CATALOG / commands()），无 MCP 那层的改名与 schema 过滤
 *
 * 注：人肉面板（浏览器 UI + Windows 输入注入）已按需求移除，这里只保留 AI 实时通道。
 *
 * 另有两条辅助线：
 *  - 自定义视角捕获（runtime/view.py）：自建 view/proj 矩阵 + GPUOffScreen.draw_view3d，不动场景出任意角度图
 *  - 无头进程（blender.exe -b）：独立进程跑重活，不占 GUI 通道；与本通道互不阻塞
 * 并发保护：租约（lease）——写操作要持有租约，别的会话在用时会明确报"leased by X"，可 force 抢占。
 */
import net from 'node:net';
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { createHash } from 'node:crypto';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { CFG, PATHS, winToWsl, wslToWin, describeConfig, IS_WIN, IS_MAC, PKG_ROOT } from './config.mjs';
// 协议适配层：直连通道支持两种 addon 实现（ahujasid 扁平协议 / harveyxiacn category-action），
// 由 CFG.addonProtocol 选择，默认 auto 自动探测。差异与映射见 runtime/addon-protocol.mjs。
import { resolveProtocol, detectProtocol, resetProtocolCache } from './addon-protocol.mjs';

const HERE = path.dirname(fileURLToPath(import.meta.url));

/** v0.8.10（A0 版本自证）：插件版本 + runtime 模块指纹 —— 让"进程内跑的是哪一代"一眼可查 */
const PLUGIN_VERSION = (() => {
  try { return JSON.parse(fs.readFileSync(path.join(PKG_ROOT, 'package.json'), 'utf8')).version; } catch (e) { return null; }
})();
function runtimeFingerprint() {
  const out = {};
  try {
    for (const f of fs.readdirSync(HERE)) {
      if (!/\.(py|mjs)$/.test(f)) continue;
      const st = fs.statSync(path.join(HERE, f));
      out[f] = String(st.size) + '-' + String(Math.round(st.mtimeMs));
    }
  } catch (e) { /* ignore */ }
  return out;
}

// 分享版：端口 / 工作目录 / blender.exe 全部来自 config.mjs（env → 配置文件 → 自动探测）。
// 本机版曾把这些写死在这里；现在换机器只需改 dsh-blender.config.json 或设 DSH_BLENDER_* 环境变量。
export { winToWsl, wslToWin, describeConfig };

export const ADDRESS = { host: CFG.addonHost, port: CFG.addonPort };
export const WIN_TMP = CFG.workDirWin;
export const WSL_TMP = CFG.workDirWsl;
export const LIVE_PNG_WIN = PATHS.livePngWin;
export const LIVE_PNG_WSL = PATHS.livePngWsl;
/**
 * 把宿主侧路径表达成「Blender 进程侧」的形式。
 * Windows：path.win32.join（反斜杠 + 盘符）；WSL：wslToWin；macOS：identity（同机同 OS）。
 * macOS 支持：原先散落的 path.win32.join 在 mac 上会产出 \Users\... 这种垃圾串，
 * 统一收到这里，避免以后再漏。
 */
export function blenderJoin(...parts) {
  if (IS_MAC) return path.join(...parts);
  if (IS_WIN) return path.win32.join(...parts);
  return wslToWin(path.join(...parts));
}
export const RUNNER_PATH = path.join(HERE, 'runner.py');
export const PERF_PATH = path.join(HERE, 'perf.py');
export const VIEW_PATH = path.join(HERE, 'view.py');
/** 契约层（S1+S2）：组件/连接注册、包络/干涉/接口校验、破坏性门控、证据账本、三态判定 */
export const CONTRACT_PATH = path.join(HERE, 'contract.py');
/** 规划器（S3）：对象图 → 诊断 → 编译到 bpy → 图导出 */
export const PLANNER_PATH = path.join(HERE, 'planner.py');
export const WORKER_PATH = path.join(HERE, 'worker.py');
export const TXN_PATH = path.join(HERE, 'txn.py');
/** 内置 QC（v0.7.0）：掩膜 / IoU / 剖面 / 对照图 + 内环 measure 预置 */
export const QC_PATH = path.join(HERE, 'qc.py');
/** 渲染 harness（v0.8.8 / P2-1）：自动取景 + 固定三点光 + 逐张 jsonl 计时 + 预算判定 */
export const QC_RENDER_PATH = path.join(HERE, 'qc_render.py');
/** 配方库（v0.8.2）：参数组合的保存 / 套用 / 导出 */
export const PRESET_PATH = path.join(HERE, 'presets.py');
/** 网格体检（v0.9.0 接通）：audit_* 前缀 op 走这里。
 *  此前 audit.py 只存在于 preload 通道，docs 里文档化的 blender_rt_plan(op="audit_mesh") 其实没有路由。 */
export const AUDIT_PATH = path.join(HERE, 'audit.py');
/** 拼图（v0.9.0 接通）：montage op 走这里（证据分级 L2：裁切拼图给眼看的少数格） */
export const MONTAGE_PATH = path.join(HERE, 'montage.py');
/** 铰接/机构（v0.9.0）：关节实测 + 扫掠验证 + URDF/USDA 导出（motion_* 前缀 op） */
export const MOTION_PATH = path.join(HERE, 'motion.py');
/** 交付导出（v0.9.0）：单位盒归一化 + 多组 OBJ/MTL + manifest(md5)（deliver_* 前缀 op） */
export const DELIVER_PATH = path.join(HERE, 'deliver.py');
/** 生成器注册表（v0.9.0 · #18 代码化建模通道的 Blender 原生轻量版）：程序即形状 + 编译门 + 缓存 + diff */
export const GENERATOR_PATH = path.join(HERE, 'generator.py');
/**
 * 用户 Blender 配置目录（GPU 偏好所在）—— 无头进程默认读不到它，Cycles 会静默回落 CPU。
 * 分享版默认 null（用系统默认配置）；要继承某套配置就设 DSH_BLENDER_USER_CONFIG / 配置项 blenderUserConfig。
 */
export const USER_CONFIG_WIN = process.env.BLENDER_USER_CONFIG || CFG.blenderUserConfig || null;
export const USER_SCRIPTS_WIN = process.env.BLENDER_USER_SCRIPTS || CFG.blenderUserScripts || null;
/** GPU 前导：无头进程里把 Cycles 设备配好，并打印 DSH_GPU 回执（详见 README「GPU 语义」） */
/**
 * 引擎前导（v0.8.0）：默认 **EEVEE + 光追**，可选 cycles（OptiX 设备前导）/ keep（不动）。
 * 为什么默认换 EEVEE：① 它本来就是纯 GPU 渲染，走图形后端（本机 OPENGL / RTX 4060），
 *   **不依赖 compute_device_type 这类"偏好"** → 无头里不会像 Cycles 那样静默回落 CPU；
 * ② 实测（360 对象 / 350 网格 / 512×512 / 64 采样）：EEVEE+RT 预热帧 **1.35 s** vs Cycles GPU **3.13 s**（2.3×）；
 * ③ 代价：首帧着色器编译 ~16 s → 迭代请配 blender_rt_worker 热会话（编译一次，此后每帧 1.35 s）。
 * 回执标记沿用 DSH_GPU（兼容），内含 engine / backend / renderer / rt 状态。
 */
export const GPU_PRELUDE = [
  'def _dsh_engine_setup():',
  '    import bpy, json',
  '    mode = __DSH_ENGINE__',
  '    manual = __DSH_GPU_MANUAL__',
  '    info = {"mode": mode, "manual": manual}',
  '    try:',
  '        import gpu',
  '        info["backend"] = gpu.platform.backend_type_get()',
  '        info["renderer"] = gpu.platform.renderer_get()',
  '    except Exception as e:',
  '        info["backend"] = "n/a: %s" % str(e)[:60]',
  '    sc = bpy.context.scene',
  '    info["before"] = {"engine": sc.render.engine}',
  '    if mode == "keep":',
  '        info["ok"] = True',
  '        info["skipped"] = True',
  '        info["after"] = {"engine": sc.render.engine}',
  '        print("DSH_GPU " + json.dumps(info, ensure_ascii=False))',
  '        return',
  '    if mode == "cycles":',
  '        try:',
  '            prefs = bpy.context.preferences.addons["cycles"].preferences',
  '        except Exception as e:',
  '            info.update({"ok": False, "error": "no cycles prefs: %s" % e})',
  '            print("DSH_GPU " + json.dumps(info, ensure_ascii=False))',
  '            return',
  '        def snap():',
  '            try:',
  '                devs = [d.name for d in prefs.devices if d.use and d.type != "CPU"]',
  '            except Exception:',
  '                devs = []',
  '            return {"device_type": prefs.compute_device_type, "scene_device": getattr(sc.cycles, "device", None), "gpu_enabled": devs}',
  '        info["cycles_before"] = snap()',
  '        if info["cycles_before"]["device_type"] == "NONE" or not info["cycles_before"]["gpu_enabled"]:',
  '            chosen = None',
  '            errs = []',
  '            for t in ["OPTIX", "CUDA", "HIP", "ONEAPI", "METAL"]:',
  '                try:',
  '                    prefs.compute_device_type = t',
  '                    prefs.get_devices()',
  '                    if [d for d in prefs.devices if d.type != "CPU"]:',
  '                        for d in prefs.devices:',
  '                            d.use = (d.type != "CPU")',
  '                        chosen = t',
  '                        break',
  '                except Exception as e:',
  '                    errs.append("%s: %s" % (t, e))',
  '            info["tried"] = errs',
  '            info["configured"] = chosen',
  '            if not chosen:',
  '                info["error"] = "no GPU backend available (tried OPTIX/CUDA/HIP/ONEAPI/METAL)"',
  '        try:',
  '            sc.render.engine = "CYCLES"',
  '            if info.get("configured"):',
  '                sc.cycles.device = "GPU"',
  '        except Exception as e:',
  '            info["engine_set_err"] = str(e)[:80]',
  '        info["cycles_after"] = snap()',
  '        a = info["cycles_after"]',
  '        info["fell_back_to_cpu"] = bool(a["device_type"] == "NONE" or not a["gpu_enabled"])',
  '        info["ok"] = (not info["fell_back_to_cpu"]) or (not manual)',
  '        info["after"] = {"engine": sc.render.engine}',
  '        print("DSH_GPU " + json.dumps(info, ensure_ascii=False))',
  '        return',
  '    ee = getattr(sc, "eevee", None)',
  '    info["before"]["rt"] = getattr(ee, "use_raytracing", None)',
  '    info["before"]["samples"] = getattr(ee, "taa_render_samples", None)',
  '    try:',
  '        sc.render.engine = "BLENDER_EEVEE"',
  '        if ee is not None:',
  '            if hasattr(ee, "use_raytracing"):',
  '                ee.use_raytracing = True',
  '            if hasattr(ee, "ray_tracing_method"):',
  '                try:',
  '                    ee.ray_tracing_method = "SCREEN"',
  '                except Exception:',
  '                    pass',
  '            if hasattr(ee, "use_shadows"):',
  '                ee.use_shadows = True',
  '            for attr, val in (("shadow_ray_count", 2), ("shadow_step_count", 8)):',
  '                if hasattr(ee, attr):',
  '                    try:',
  '                        setattr(ee, attr, val)',
  '                    except Exception:',
  '                        pass',
  '            cur = getattr(ee, "taa_render_samples", 64) or 64',
  '            if hasattr(ee, "taa_render_samples") and cur < 64:',
  '                ee.taa_render_samples = 64',
  '        info["configured"] = "eevee-rt" if (ee is not None and getattr(ee, "use_raytracing", False)) else "eevee"',
  '    except Exception as e:',
  '        info["ok"] = False',
  '        info["error"] = "%s: %s" % (type(e).__name__, str(e)[:120])',
  '    info["after"] = {"engine": sc.render.engine, "rt": getattr(ee, "use_raytracing", None),',
  '                     "ray_method": str(getattr(ee, "ray_tracing_method", "")), "samples": getattr(ee, "taa_render_samples", None)}',
  '    info["fell_back_to_cpu"] = False',
  '    info.setdefault("ok", True)',
  '    print("DSH_GPU " + json.dumps(info, ensure_ascii=False))',
  '_dsh_engine_setup()',
  'del _dsh_engine_setup',
].join('\n');
/** 自定义视角出图（默认覆盖写这个文件） */
export const VIEW_PNG_WIN = PATHS.viewPngWin;
export const VIEW_PNG_WSL = PATHS.viewPngWsl;
/** 无头 Blender 可执行文件（WSL 侧路径；spawn 走 binfmt 直接起 Windows 进程）；找不到时由工具给出配置指引 */
export const BLENDER_EXE = CFG.blenderExe;
export { PKG_ROOT } from './config.mjs';

/**
 * 持久内核引导：addon 的 execute_code 每次都在**全新命名空间**里跑（实测跨调用变量不保留），
 * 但 sys.modules 是同一个进程 → 用一个常驻模块 K 承载状态，实现 REPL 语义：
 *   K.x = 41        # 这次调用
 *   print(K.x + 1)  # 下次调用仍能读到
 * 同时预置 bpy / math / mathutils，省掉每次重复 import。
 */
const PATH_HELPERS_TEMPLATE = [
'def _dsh_slashes(p):',
'    # macOS / Linux 用正斜杠；只有 Windows 才需要反转',
'    if __DSH_NATIVE_POSIX__: return str(p)',
'    return str(p).replace("/", chr(92))',
'def _dsh_blend_path(p):',
'    """相对路径 → 相对当前 .blend 的绝对路径（Windows 形式）"""',
'    import os as _os',
'    s = str(p).strip()',
'    fp = bpy.data.filepath',
'    base = _os.path.dirname(_dsh_slashes(fp)) if fp else ""',
'    if s.startswith("//"): s = s[2:]',
'    if s.startswith("./"): s = s[2:]',
'    if not s or s == ".": return base',
'    if _os.path.isabs(s) or (len(s) > 1 and s[1] == ":"): return _dsh_slashes(s)',
'    return _dsh_slashes(_os.path.join(base, s)) if base else s',
'def _dsh_win_path(p):',
'    """任何路径 → Blender 进程侧可用形式（/mnt/d/x → D:/x；WSL 内部 → UNC；macOS/Linux 原生 → 原样）"""',
'    if __DSH_NATIVE_POSIX__: return _dsh_blend_path(p)',
'    s = str(p).strip(); b = chr(92)',
'    if len(s) > 1 and s[1] == ":": return _dsh_slashes(s)',
'    if s.startswith("/mnt/") and len(s) > 6: return s[5].upper() + ":" + b + s[7:].replace("/", b)',
'    if s.startswith(b + b + "wsl"): return _dsh_slashes(s)',
'    # v0.8.4：正斜杠 UNC（//wsl.localhost/…）在 Blender 里会被当成「相对 .blend」而读不到 → 归一化',
'    if s.startswith("//wsl") or s.startswith("//WSL"): return b + _dsh_slashes(s[1:])',
'    if s.startswith("//") or s.startswith("./") or (s and not s.startswith("/")): return _dsh_blend_path(s)',
'    if s.startswith("/"): return b + b + "wsl.localhost" + b + __DISTRO__ + s.replace("/", b)',
'    return s',
'def _dsh_wsl_path(p):',
'    """Windows / UNC 路径 → WSL 侧可用形式（D:/x → /mnt/d/x；UNC → /...）；原生 POSIX 原样返回"""',
'    if __DSH_NATIVE_POSIX__: return _dsh_blend_path(p)',
'    s = str(p).strip(); b = chr(92)',
'    if len(s) > 1 and s[1] == ":": return "/mnt/" + s[0].lower() + "/" + s[2:].replace(b, "/").lstrip("/")',
'    if s.startswith(b + b + "wsl.localhost" + b):',
'        parts = s.split(b, 4)',
'        return "/" + (parts[4].replace(b, "/") if len(parts) > 4 else "")',
'    if s.startswith("/mnt/"): return s',
'    if s.startswith("//") or s.startswith("./") or (s and not s.startswith("/")): return _dsh_win_path(s)',
'    return s',
'def _dsh_run(path, reload_modules=True):',
'    """跑一个 .py 文件（WSL / Windows / 相对 .blend 三种路径都收）；可选重载同目录模块"""',
'    import os as _os',
'    p = _dsh_win_path(path)',
'    if not _os.path.isfile(p):',
'        alt = _dsh_wsl_path(p)',
'        if _os.path.isfile(alt): p = alt',
'        else: raise FileNotFoundError("找不到脚本: %s（也试过 %s）" % (path, alt))',
'    if reload_modules:',
'        import importlib as _il, sys as _sy',
'        d = _os.path.dirname(_os.path.abspath(p))',
'        _il.invalidate_caches()',
'        if d not in _sy.path: _sy.path.insert(0, d)',
'        for _n, _m in list(_sy.modules.items()):',
'            _f = getattr(_m, "__file__", None)',
'            if _f and _os.path.dirname(_os.path.abspath(_f)) == d:',
'                try: _il.reload(_m)',
'                except Exception: pass',
'    _g = globals(); _g["__file__"] = p',
'    with open(p, encoding="utf-8") as _fh: _src = _fh.read()',
'    exec(compile(_src, p, "exec"), _g)',
'    return p',
'def _dsh_stage(path, sub="stage"):',
'    """把文件复制到 Blender 一定读得到的本地路径（UNC/中文路径兜底）。返回 {ok, src, staged, bytes, reason}。"""',
'    import os as _os, shutil as _sh',
'    src = _dsh_win_path(path)',
'    info = {"src": src, "staged": src}',
'    if not _os.path.isfile(src):',
'        info["ok"] = False; info["reason"] = "源文件不存在"; return info',
'    try:',
'        if src.startswith(chr(92) * 2):',
'            base = str(getattr(K, "out_dir", "") or "")',
'            d = _os.path.join(base, str(sub))',
'            _os.makedirs(d, exist_ok=True)',
'            dst = _os.path.join(d, _os.path.basename(src))',
'            _sh.copyfile(src, dst)',
'            info["staged"] = dst; info["bytes"] = _os.path.getsize(dst); info["was_unc"] = True',
'    except Exception as _e:',
'        info["ok"] = False; info["reason"] = str(_e)[:120]; return info',
'    info["ok"] = True; return info',
'def _dsh_reload_modules(prefixes=None, root=None):',
'    """热重载：清掉指定前缀/目录下的用户模块（热会话里普通 import 会命中 sys.modules 旧代码）。"""',
'    import sys as _sys, importlib as _il, os as _os',
'    _il.invalidate_caches()',
'    pre = [str(p) for p in (prefixes or []) if str(p)]',
'    root_abs = _os.path.abspath(root) if root else None',
'    purged = []',
'    for _n, _m in list(_sys.modules.items()):',
'        _f = getattr(_m, "__file__", None)',
'        _hit = bool(pre) and any(_n == p or _n.startswith(p + ".") for p in pre)',
'        if not _hit and root_abs and _f:',
'            try:',
'                _hit = _os.path.abspath(_f).startswith(root_abs)',
'            except Exception:',
'                _hit = False',
'        if _hit and not _n.startswith("bpy"):',
'            _sys.modules.pop(_n, None); purged.append(_n)',
'    return {"purged": purged, "count": len(purged), "prefixes": pre, "root": root_abs}',
'def _dsh_stage_emit(name, **extra):',
'    """v0.9.3（D5）：阶段心跳 —— print("DSH_STAGE {单行 JSON}") 且 flush=True。',
'    插件侧在 blender_rt_job(op="status").stage / stdout 日志里读最后一条。',
'    用法：dsh_stage("building") / K.progress("render", i=3, total=12)。"""',
'    import json as _stj, time as _stt, os as _sto',
'    _d = {"name": str(name), "t": round(_stt.time(), 3), "run": _sto.environ.get("DSH_RUN_ID")}',
'    _d.update({str(k): v for k, v in extra.items()})',
'    print("DSH_STAGE " + _stj.dumps(_d, ensure_ascii=False), flush=True)',
'dsh_stage = _dsh_stage_emit',
'K.dsh_distro = __DISTRO__',
'K.win_path = _dsh_win_path',
'K.wsl_path = _dsh_wsl_path',
'K.blend_path = _dsh_blend_path',
'K.run = _dsh_run',
'K.stage = _dsh_stage',
'K.progress = _dsh_stage_emit',
'K.reload_modules = _dsh_reload_modules',
'K.out_dir = __OUTDIR__',
  '# v0.8.8：runtime 目录也注入（qc.py 据此现场加载同目录模块，如 qc_render.py）',
  'K.runtime_dir = __RUNTIME_DIR__',
  'K.workdir = K.out_dir',
  // v0.9.1（93-B4）：参数/环境契约 —— 子进程里 K.args / K.env / K.run_id 直接可读，
  // 不必再靠"把参数挤进 argv"或全局变量传参（headless 注入了 DSH_ARGS/DSH_OUTDIR/DSH_RUN_ID/DSH_SESSION）。
  'try:',
  '    import os as _os_env, json as _json_env',
  '    K.env = _os_env.environ',
  '    K.run_id = _os_env.environ.get("DSH_RUN_ID")',
  '    K.session = _os_env.environ.get("DSH_SESSION")',
  '    K.outdir_env = _os_env.environ.get("DSH_OUTDIR")',
  '    K.args = _json_env.loads(_os_env.environ.get("DSH_ARGS") or "[]")',
  'except Exception:',
  '    K.env = {}; K.args = []; K.run_id = None; K.session = None; K.outdir_env = None',
  '# v0.8.7：路径常量也注入脚本命名空间（独立模块 + exec(open()) 写法同样可用）',
  'DSH_OUT = K.out_dir',
  'DSH_WIN = _dsh_win_path',
  'DSH_WSL = _dsh_wsl_path',
].join('\n');
export const PATH_HELPERS = PATH_HELPERS_TEMPLATE
  // macOS 与「非 WSL 的原生 POSIX」都不需要跨 OS 路径改写（Blender 与宿主同机）。
  // WSL 判据沿用工程里已有的 /mnt/c 探测，不依赖可能未导出的 WSL_DISTRO_NAME。
  .replace(/__DSH_NATIVE_POSIX__/g, (IS_MAC || (!IS_WIN && !fs.existsSync('/mnt/c'))) ? 'True' : 'False')
  .replace(/__DISTRO__/g, JSON.stringify(process.env.WSL_DISTRO_NAME || 'Ubuntu'))
  .replace(/__OUTDIR__/g, JSON.stringify(WIN_TMP))
  .replace(/__RUNTIME_DIR__/g, JSON.stringify(HERE));
/** act 包装：异常也回传 partial stdout/stderr/traceback（v0.7.0） */
export const ACT_WRAPPER = (src) => [
  'import io as _dsh_io, contextlib as _dsh_ctx, traceback as _dsh_tb, json as _dsh_json',
  '_dsh_src = _dsh_json.loads(' + JSON.stringify(JSON.stringify(src)) + ')',
  '_dsh_o = _dsh_io.StringIO(); _dsh_e = _dsh_io.StringIO()',
  'def _dsh_epoch():',
  '    import bpy as _b',
  '    try:',
  '        h = 0',
  '        for _o in _b.data.objects:',
  '            for _c in _o.name:',
  '                h = (h * 131 + ord(_c)) & 4294967295',
  '            if _o.type == "MESH" and getattr(_o, "data", None) is not None:',
  '                h = (h * 131 + len(_o.data.vertices)) & 4294967295',
  '        return {"objects": len(_b.data.objects), "meshes": len(_b.data.meshes),',
  '                "materials": len(_b.data.materials), "nameHash": h}',
  '    except Exception as _e:',
  '        return {"error": str(_e)[:60]}',
  'def _dsh_epoch_json():',
  '    import json as _j',
  '    return _j.dumps(_dsh_epoch(), ensure_ascii=False)',
  'try:',
  '    with _dsh_ctx.redirect_stdout(_dsh_o), _dsh_ctx.redirect_stderr(_dsh_e):',
  '        exec(compile(_dsh_src, "<rt_do>", "exec"), globals())',
  'except BaseException as _dsh_exc:',
  '    print("DSH_ACT_ERR " + _dsh_json.dumps({"error": "%s: %s" % (type(_dsh_exc).__name__, _dsh_exc), "traceback": _dsh_tb.format_exc()[-6000:], "stdout": _dsh_o.getvalue()[-16000:], "stderr": _dsh_e.getvalue()[-8000:], "epoch": _dsh_epoch()}, ensure_ascii=False))',
  'else:',
  '    print("DSH_ACT_OK " + _dsh_json.dumps({"stdout": _dsh_o.getvalue()[-16000:], "stderr": _dsh_e.getvalue()[-8000:], "epoch": _dsh_epoch()}, ensure_ascii=False))',
].join('\n');

export const KERNEL_BOOTSTRAP = [
  'import sys as _sys, types as _types',
  'K = _sys.modules.get("dsh_rt_kernel")',
  'if K is None:',
  '    K = _types.ModuleType("dsh_rt_kernel")',
  '    _sys.modules["dsh_rt_kernel"] = K',
  'import bpy, math, mathutils',
  'Vector = mathutils.Vector',
  '# ---- 路径辅助（v0.7.0）：GUI 与无头两侧都能用，省掉手拼 UNC 与 chr(92)\n' + PATH_HELPERS,
].join('\n');

/** addon 命令目录：name → { d: 说明, gate: 需要的 scene 开关（null=常驻） } */
export const COMMAND_CATALOG = {
  ping: { d: '存活探测（不碰 bpy 数据）', gate: null },
  get_scene_info: { d: '场景概览（无参）', gate: null },
  get_world_state_snapshot: { d: '世界状态快照：对象/选中/帧', gate: null },
  get_addon_info: { d: 'addon 版本与协议号', gate: null },
  get_object_info: { d: '单对象详情（参数 name）', gate: null },
  get_viewport_screenshot: { d: '视口离屏截图（max_size, filepath, format）', gate: null },
  execute_code: { d: '执行任意 Python（万能通道，预置 K/bpy/math/mathutils）', gate: null },
  drain_human_activity: { d: '取走"人类操作"事件缓冲', gate: null },
  get_telemetry_consent: { d: '读遥测同意开关', gate: null },
  set_telemetry_consent: { d: '写遥测同意开关（参数 consent）', gate: null },
  get_polyhaven_status: { d: 'PolyHaven 集成状态', gate: null },
  get_hyper3d_status: { d: 'Hyper3D Rodin 集成状态', gate: null },
  get_sketchfab_status: { d: 'Sketchfab 集成状态', gate: null },
  get_polypizza_status: { d: 'Poly Pizza 集成状态', gate: null },
  get_hunyuan3d_status: { d: 'Hunyuan3D 集成状态', gate: null },
  get_polyhaven_categories: { d: 'PolyHaven 分类（参数 asset_type: hdris/textures/models）', gate: 'blendermcp_use_polyhaven' },
  search_polyhaven_assets: { d: 'PolyHaven 搜索（asset_type, categories）', gate: 'blendermcp_use_polyhaven' },
  download_polyhaven_asset: { d: '下载并导入 PolyHaven 资产（asset_id, asset_type, resolution）', gate: 'blendermcp_use_polyhaven' },
  set_texture: { d: '把已下载贴图应用到对象（object_name, texture_id）', gate: 'blendermcp_use_polyhaven' },
  create_rodin_job: { d: 'Hyper3D Rodin 生成任务（文本/图片）', gate: 'blendermcp_use_hyper3d' },
  poll_rodin_job_status: { d: 'Rodin 任务轮询', gate: 'blendermcp_use_hyper3d' },
  import_generated_asset: { d: '导入 Rodin 生成结果', gate: 'blendermcp_use_hyper3d' },
  search_sketchfab_models: { d: 'Sketchfab 搜索（query, categories, count）', gate: 'blendermcp_use_sketchfab' },
  get_sketchfab_model_preview: { d: 'Sketchfab 模型预览图', gate: 'blendermcp_use_sketchfab' },
  download_sketchfab_model: { d: '下载并导入 Sketchfab 模型（uid）', gate: 'blendermcp_use_sketchfab' },
  search_polypizza_models: { d: 'Poly Pizza 搜索（query）', gate: 'blendermcp_use_polypizza' },
  download_polypizza_model: { d: '下载并导入 Poly Pizza 模型（model_id）', gate: 'blendermcp_use_polypizza' },
  create_hunyuan_job: { d: 'Hunyuan3D 生成任务', gate: 'blendermcp_use_hunyuan3d' },
  poll_hunyuan_job_status: { d: 'Hunyuan3D 任务轮询', gate: 'blendermcp_use_hunyuan3d' },
  import_generated_asset_hunyuan: { d: '导入 Hunyuan3D 生成结果', gate: 'blendermcp_use_hunyuan3d' },
};

export const GATE_FLAGS = [
  'blendermcp_use_polyhaven',
  'blendermcp_use_hyper3d',
  'blendermcp_use_sketchfab',
  'blendermcp_use_polypizza',
  'blendermcp_use_hunyuan3d',
];

const REGION_CODE = [
  'import bpy, json',
  'w = bpy.context.window',
  'res = []',
  'for a in bpy.context.screen.areas:',
  '    if a.type == "VIEW_3D":',
  '        r = next(x for x in a.regions if x.type == "WINDOW")',
  '        res.append({"region": [r.x, r.y, r.width, r.height], "area": [a.x, a.y, a.width, a.height], "shading": a.spaces.active.shading.type})',
  'print(json.dumps({"window": [w.width, w.height], "views": res}))',
].join('\n');

const FLAGS_CODE = [
  'import bpy, json',
  's = bpy.context.scene',
  'flags = "blendermcp_use_polyhaven,blendermcp_use_hyper3d,blendermcp_use_sketchfab,blendermcp_use_polypizza,blendermcp_use_hunyuan3d".split(",")',
  'print(json.dumps({"scene": s.name, "file": bpy.data.filepath, "flags": {k: bool(getattr(s, k, None)) for k in flags}}))',
].join('\n');

function extractTag(out, tag) {
  const txt = (out && typeof out.result === 'string') ? out.result : '';
  const m = txt.match(new RegExp(tag + ' ([\\s\\S]*)'));
  if (!m) throw new Error(tag + ' 无回包: ' + txt.slice(0, 200));
  return JSON.parse(m[1]);
}

function extractLoop(out) {
  return extractTag(out, 'LOOP');
}

function jsonFromStdout(out) {
  const txt = (out && typeof out.result === 'string') ? out.result : (typeof out === 'string' ? out : JSON.stringify(out));
  const m = txt.match(/\{[\s\S]*\}/);
  return m ? JSON.parse(m[0]) : null;
}

/** 路径映射见 config.mjs：winToWsl / wslToWin（Windows 上原样返回） */

/** 无头运行用的 python 脚本落到 Windows 可见目录；/mnt 不可写时退回包内 tmp + UNC */
/** v0.8.10（A3）：长输出"头+尾+省略标记"—— 旧版只回尾部，长 JSON 的头被吃掉 */
function clipMiddle(s, head, tail) {
  const t = String(s == null ? '' : s);
  if (t.length <= head + tail) return t;
  return t.slice(0, head) + '\n…[省略 ' + String(t.length - head - tail) + ' 字符；全文见 logs 路径]…\n' + t.slice(-tail);
}

/**
 * v0.9.3（D5 / 外部反馈 2026-09-24）：子进程基础环境 —— 三处 spawn 统一走它。
 *
 * 旧版只注入 PYTHONIOENCODING。Blender 的 Python stdout 被重定向（pipe）时是**块缓冲**，
 * 于是 job 的 stdout.log / stderr.log 在任务运行期间**全程 0 字节**，只有进程退出才落盘
 * （Lead 实测：20 分钟渲染期间两个日志一直 0 字节；engine：「399 s 那次中间没有任何阶段信息，6 分钟纯黑盒」）。
 * PYTHONUNBUFFERED=1 让 print 立即写出 → stage 心跳与增量日志才有意义。
 */
function baseChildEnv(extra) {
  return Object.assign({}, process.env, { PYTHONIOENCODING: 'utf-8', PYTHONUNBUFFERED: '1' }, extra || {});
}

/** WSLENV 声明（v0.9.1 实测坑：WSL 侧 spawn Windows 进程时 env 不会自动跨界）—— headless 与 job 共用
 *  macOS / Windows 上父子进程同 OS，env 天然继承，不需要这一步。 */
function withWslEnv(childEnv, extraEnv) {
  if (IS_MAC || IS_WIN) return childEnv;   // 同 OS，env 直接继承；WSLENV 无意义且会污染子进程环境
  try {
    const pass = Object.keys(childEnv).filter((k) => k.indexOf('DSH_') === 0
      || (extraEnv && Object.prototype.hasOwnProperty.call(extraEnv, k)));
    const spec = pass.map((k) => k + '/w').join(':');
    childEnv.WSLENV = childEnv.WSLENV ? (childEnv.WSLENV + ':' + spec) : spec;
  } catch (e) { /* WSLENV 拼不出来就算了，脚本内注入是主路径 */ }
  return childEnv;
}

/** workdir 前导（headless 与 job 共用）：脚本内 chdir + sys.path 首位（Blender 是 Windows 进程，必须过 K.win_path） */
function workdirBlock(p) {
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
 * v0.9.3（D5/D6）：env 前导 —— headless 与 job 共用。
 * WSL→Windows 的 env 跨界不可靠（实测 DSH_* 会被吃掉）→ 脚本里再 update 一遍；
 * 顺带定义 stage 心跳函数，让脚本第一行就能用 dsh_stage()。
 */
function envPrelude(envPairs) {
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
    'dsh_stage = _dsh_stage_emit',
    '_dsh_stage_emit("boot")'].join('\n');
}

/**
 * v0.9.3（D4.3）：路径体检 —— Windows Blender 把 '/home/x' 当"当前盘根下的相对路径"，
 * 静默写到 C:\home\x（证据：Saved: 'C:homesixtyseven67DSH…' 紧随其后 os.path.exists() -> false）。
 * 这里在脚本收尾时检查 scene.render.filepath；配合 stdout 里的 Saved: 扫描（pathAudit）。
 */
export const PATH_GUARD = (IS_MAC || IS_WIN) ? '' : ['# ---- DSH 路径体检（v0.9.3 / D4.3）----',
  'try:',
  '    import json as _dsh_pj',
  '    _dsh_pw = []',
  '    _dsh_fp = bpy.context.scene.render.filepath',
  '    _dsh_fp0 = globals().get("_DSH_FP0")',
  '    if isinstance(_dsh_fp, str) and _dsh_fp.startswith("/") and not _dsh_fp.startswith("//") and _dsh_fp != _dsh_fp0:',
  '        _dsh_pw.append({"code": "render_filepath_posix", "value": _dsh_fp[:200],',
  '                        "hint": "scene.render.filepath 是 POSIX 绝对路径；Windows Blender 会把它写到 C:\\home\\… —— 用 K.win_path(p) 换成 Windows 形式"})',
  '    if _dsh_pw:',
  '        print("DSH_PATH_WARN " + _dsh_pj.dumps({"warnings": _dsh_pw}, ensure_ascii=False), flush=True)',
  'except Exception:',
  '    pass'].join('\n');

/**
 * v0.9.3（F1/F2）：shots 多视角渲染 —— 复用 runtime/qc_render.py 的 qc_render_views
 * （自动取景/三点光/渲染锁/设备回读/逐张 md5 都已在那里），只补"显式 from+look_at"与覆盖率判定。
 * 结果契约：打印 DSH_SHOTS <单行 JSON>（qc_render_views 的原样回执）。
 */
function shotsBlock(args) {
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

/** 文件指纹（F6）：返回 {path,size,mtimeMs,md5,md5Partial}；>512MB 只哈希前 64MB 并标注 partial */
function fileFingerprint(winPath, wslPath) {
  const out = { path: winPath ? String(winPath) : null, wsl: wslPath ? String(wslPath) : null };
  try {
    const st = fs.statSync(wslPath);
    out.size = st.size; out.mtimeMs = st.mtimeMs; out.mtime = new Date(st.mtimeMs).toISOString();
    const LIMIT = 64 * 1024 * 1024;
    if (st.size > 512 * 1024 * 1024) { out.md5 = md5File(wslPath, LIMIT); out.md5Partial = true; out.md5Note = '文件 >512MB：只哈希前 64MB（配合 size/mtime 判断变更足够）'; }
    else out.md5 = md5File(wslPath);
  } catch (e) {
    out.error = String((e && e.message) || e).slice(0, 160);
  }
  return out;
}
function md5File(p, limit) {
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

/**
 * v0.9.3（D5）：stage 心跳 —— 长任务的"可观测性"契约。
 * 脚本侧 `dsh_stage("building")` / `K.progress("building")` → 打印 `DSH_STAGE {单行 JSON}`；
 * 引擎侧在 stdout 流里抓最后一条写进 runRec.stage / job.stage，op=status 直接可读。
 */
const STAGE_TAG = 'DSH_STAGE ';
function scanStageLine(line, rec, now) {
  if (line.indexOf(STAGE_TAG) !== 0) return false;
  try {
    const obj = JSON.parse(line.slice(STAGE_TAG.length).trim());
    rec.stage = obj; rec.stageAt = now; rec.stageName = obj && obj.name ? String(obj.name) : null;
    return true;
  } catch (e) {
    rec.stage = { raw: line.slice(0, 200), parse_error: String((e && e.message) || e) };
    rec.stageAt = now;
    return false;
  }
}
/** 把一段 chunk 按行喂给 stage 扫描（跨 chunk 的半行用尾部缓冲兜住） */
function makeStageScanner(rec) {
  let tail = '';
  return (chunk) => {
    const now = Date.now();
    rec.lastOutputAt = now;
    const text = tail + String(chunk);
    const lines = text.split('\n');
    tail = lines.pop() || '';
    if (tail.length > 8192) tail = tail.slice(-2048);
    rec.lines = (rec.lines || 0) + lines.length;
    for (const l of lines) { const s = l.replace(/\r$/, ''); if (s.indexOf(STAGE_TAG) === 0) scanStageLine(s, rec, now); }
  };
}
/** pid 是否还活着（D6.3：状态与实际进程对账）—— 无权限也算活着（EPERM） */
function pidAlive(pid) {
  const n = Number(pid);
  if (!Number.isFinite(n) || n <= 0) return false;
  try { process.kill(n, 0); return true; } catch (e) { return !!(e && e.code === 'EPERM'); }
}
/** D4.3：脚本把 WSL/POSIX 路径交给 Windows API 时的**静默改写**检测（证据：Saved: 'C:homesixtyseven67DSH…'） */
function pathAudit(stdout, stderr) {
  const warns = [];
  const text = String(stdout || '') + '\n' + String(stderr || '');
  const re = /Saved:\s*'([^']*)'/g;
  let m;
  // macOS 上宿主与 Blender 同 OS，不存在「POSIX 路径被 Windows API 改写成 D:home…」这类问题
  while (!IS_MAC && (m = re.exec(text)) !== null) {
    const p = m[1];
    if (/^[A-Za-z]:(home|mnt|tmp|var|usr|root|opt)/i.test(p)) {
      warns.push({ code: 'posix_path_rewritten', evidence: p.slice(0, 200),
        hint: "Blender 是 Windows 进程：'/home/…' 被当成「当前盘根下的相对路径」→ 实际写到 C:\\home\\…。" +
              "脚本里用 K.win_path('/home/…')（或 dsh_win_path）换成 Windows 可用形式再交给 bpy。" });
    }
  }
  const re2 = /DSH_PATH_WARN (\{[^\n]*\})/g;
  while ((m = re2.exec(String(stdout || ''))) !== null) {
    try {
      const o = JSON.parse(m[1]);
      for (const w of (o && o.warnings) || []) warns.push(w);
    } catch (e) { /* ignore */ }
  }
  // 去重（同一路径可能被 Saved: 打多次）
  const seen = new Set();
  return warns.filter((w) => { const k = w.code + '|' + String(w.evidence || w.value || ''); if (seen.has(k)) return false; seen.add(k); return true; });
}

function writeHeadlessScript(code) {
  const name = 'dsh_headless_' + Date.now().toString(36) + '.py';
  const cands = [
    { dir: WSL_TMP, win: blenderJoin(WIN_TMP, name) },
    { dir: path.join(HERE, 'tmp'), win: null },
  ];
  const errs = [];
  for (const c of cands) {
    try {
      fs.mkdirSync(c.dir, { recursive: true });
      const wsl = path.join(c.dir, name);
      fs.writeFileSync(wsl, code, 'utf8');
      return { wsl: wsl, win: c.win || wslToWin(wsl) };
    } catch (e) { errs.push(c.dir + ': ' + String((e && e.message) || e)); }
  }
  throw new Error('无法写无头脚本：' + errs.join(' · '));
}

/* ────────────────────────────────────────────────────────────────────────────
 * v0.9.4（外部反馈《M1A1 分件建模》P1）：**一键拉起 GUI Blender 并自动 Connect addon**
 *
 * 反馈原文：「文档写的是按 N → MCP for Blender → Connect，但 agent 没有人手。
 * 我是自己拼 PowerShell Start-Process + --python-expr 才把 GUI 会话点亮的。」
 * 做法就是把那套自拼流程固化：写一个 boot 脚本（GUI 里 enable addon → 起 socket server），
 * detached spawn blender.exe，然后**轮询 addon 端口**（唯一可信判据，不靠 stdout 文本）。
 * 顺手把 boot 结论落盘成 launch-status.json —— GUI 进程的 stdout 我们拿不到，只能让它自己写。
 * ──────────────────────────────────────────────────────────────────────────── */
const LAUNCH_BOOT_PY = [
  '"""DSH launch boot (v0.9.4): enable the Blender MCP addon and start its socket server (GUI only)."""',
  'import importlib.util, json, os, sys',
  '',
  '_res = {',
  '    "ok": False, "background": None, "enabled": [], "loaded_by": None, "server_running": False,',
  '    "addon_file": None, "errors": [], "addon_modules_seen": [],',
  '    "env": {"BLENDER_USER_SCRIPTS": os.environ.get("BLENDER_USER_SCRIPTS", ""),',
  '            "DSH_BLENDER_ADDON_FILE": os.environ.get("DSH_BLENDER_ADDON_FILE", ""),',
  '            "DSH_BLENDER_ADDON_MODULE": os.environ.get("DSH_BLENDER_ADDON_MODULE", "")},',
  '}',
  '',
  '',
  'def _flush():',
  '    p = os.environ.get("DSH_BLENDER_LAUNCH_STATUS", "")',
  '    if not p:',
  '        return',
  '    try:',
  '        d = os.path.dirname(p)',
  '        if d and not os.path.isdir(d):',
  '            os.makedirs(d, exist_ok=True)',
  '        with open(p, "w", encoding="utf-8") as fh:',
  '            json.dump(_res, fh, ensure_ascii=False, indent=1)',
  '    except Exception as exc:',
  '        _res["status_write_error"] = repr(exc)',
  '',
  '',
  'def _report():',
  '    _res["server_running"] = bool(_server_running())',
  '    _res["ok"] = bool(_res["server_running"])',
  '    _flush()',
  '    print("DSH_LAUNCH " + json.dumps(_res, ensure_ascii=False), flush=True)',
  '',
  '',
  'def _server_running():',
  '    t = getattr(bpy.types, "blendermcp_server", None)',
  '    try:',
  '        return bool(t and getattr(t, "running", False))',
  '    except Exception:',
  '        return False',
  '',
  '',
  'def main():',
  '    _res["background"] = bool(bpy.app.background)',
  '    if bpy.app.background:',
  '        _res["errors"].append("background 模式：addon 明确拒绝在 -b 下起 server，请用 GUI（不带 -b）")',
  '        return',
  '    want = [m.strip() for m in (os.environ.get("DSH_BLENDER_ADDON_MODULE", "") or "").split(",") if m.strip()]',
  '    try:',
  '        import addon_utils',
  '        mods = list(addon_utils.modules())',
  '        _res["addon_modules_seen"] = [getattr(m, "__name__", "") for m in mods][:60]',
  '        names = want or [getattr(m, "__name__", "") for m in mods if "mcp" in getattr(m, "__name__", "").lower()]',
  '        for n in names:',
  '            try:',
  '                addon_utils.enable(n, default_set=True, persistent=True)',
  '                _res["enabled"].append(n)',
  '                if _server_running():',
  '                    _res["loaded_by"] = "addon_utils:" + n',
  '                    return',
  '            except Exception as exc:',
  '                _res["errors"].append("enable %s: %r" % (n, exc))',
  '    except Exception as exc:',
  '        _res["errors"].append("addon_utils: %r" % (exc,))',
  '    # 兜底：直接按文件 import + register()',
  '    # （Blender 5.x 换成 extension 布局后，addon_utils 可能扫不到老的 scripts/addons/*.py）',
  '    cands = []',
  '    given = os.environ.get("DSH_BLENDER_ADDON_FILE", "")',
  '    if given:',
  '        cands.append(given)',
  '    addons_dir = os.path.join(os.environ.get("BLENDER_USER_SCRIPTS", ""), "addons")',
  '    try:',
  '        for f in sorted(os.listdir(addons_dir)):',
  '            if f.lower().endswith(".py") and "mcp" in f.lower():',
  '                cands.append(os.path.join(addons_dir, f))',
  '    except Exception:',
  '        pass',
  '    for p in cands:',
  '        try:',
  '            spec = importlib.util.spec_from_file_location("dsh_launch_" + os.path.basename(p)[:-3], p)',
  '            mod = importlib.util.module_from_spec(spec)',
  '            sys.modules[spec.name] = mod',
  '            spec.loader.exec_module(mod)',
  '            if not getattr(bpy.types, "blendermcp_server", None) and hasattr(mod, "register"):',
  '                mod.register()',
  '            _res["addon_file"] = p',
  '            _res["loaded_by"] = "direct-import:" + os.path.basename(p)',
  '            if _server_running():',
  '                return',
  '        except Exception as exc:',
  '            _res["errors"].append("direct import %s: %r" % (p, exc))',
  '',
  '',
  'try:',
  '    import bpy',
  'except Exception as _e:',
  '    _res["errors"].append("no bpy: %r" % (_e,))',
  '    _report()',
  'else:',
  '    main()',
  '    _report()',
].join(String.fromCharCode(10));

/** 读一个 JSON 小文件（不存在/坏了都返回 null，绝不抛） */
function readJsonSafe(p) {
  try { return JSON.parse(fs.readFileSync(p, 'utf8')); } catch (e) { return null; }
}

/**
 * v0.9.4：拉起 GUI Blender 并自动把 addon 的 socket server 点起来。
 * 判据是**端口真的开了**（tcpProbe），不是"进程起来了"—— 进程起来但 addon 没 Connect
 * 正是 agent 手工流程最容易停在的半成品状态。
 */
export async function launchBlender(opts = {}) {
  const started = Date.now();
  const addonPort = Math.max(1, Math.min(65535, Number(opts.addonPort || CFG.addonPort) || 9876));
  const waitMs = Math.max(2000, Math.min(600000, Number(opts.waitMs || process.env.DSH_BLENDER_LAUNCH_WAIT_MS) || 90000));
  const exe = String(opts.exe || BLENDER_EXE || '');
  const steps = [];
  const statusWsl = path.join(winToWsl(WIN_TMP), 'launch-status.json');
  const base = { exe: exe || null, addonPort: addonPort, waitMs: waitMs, statusFile: statusWsl,
                 statusFileWin: wslToWin(statusWsl), configFrom: CFG.source ? CFG.source.blenderExe : null };

  // ① 已经在监听 → 什么都不做（幂等：agent 反复调用不会堆出第二个 Blender）
  if (await tcpProbe('127.0.0.1', addonPort, 800)) {
    const boot0 = readJsonSafe(statusWsl);
    return Object.assign({}, base, { ok: true, already: true, launched: false, waitedMs: Date.now() - started, steps: steps,
      boot: boot0, hint: 'addon 已在本机 ' + addonPort + ' 监听，无需启动（要重启先关掉那个 Blender，或用 force 另起一个）' });
  }
  if (!exe) {
    return Object.assign({}, base, { ok: false, launched: false, already: false, waitedMs: Date.now() - started, steps: steps,
      error: '没找到 blender.exe', hint: '设 DSH_BLENDER_EXE 或 dsh-blender.config.json 的 blenderExe（见 docs/配置参考.md）' });
  }

  const sp = writeHeadlessScript(LAUNCH_BOOT_PY);
  const args = ['--python', sp.win];
  if (opts.file) args.push(wslToWin(String(opts.file)));
  const childEnv = withWslEnv(baseChildEnv({}), null);
  if (USER_SCRIPTS_WIN) childEnv.BLENDER_USER_SCRIPTS = USER_SCRIPTS_WIN;
  if (USER_CONFIG_WIN) childEnv.BLENDER_USER_CONFIG = USER_CONFIG_WIN;
  if (opts.addonFile) childEnv.DSH_BLENDER_ADDON_FILE = String(opts.addonFile);
  if (opts.addonModule) childEnv.DSH_BLENDER_ADDON_MODULE = String(opts.addonModule);
  childEnv.DSH_BLENDER_LAUNCH_STATUS = wslToWin(statusWsl);
  try { fs.rmSync(statusWsl, { force: true }); } catch (e) { /* 旧状态删不掉也无所谓 */ }

  if (opts.dryRun) {
    return Object.assign({}, base, { ok: true, dryRun: true, launched: false, already: false, args: args, bootScript: sp.win,
      steps: ['dryRun：只给出将要执行的命令'], hint: '去掉 dry_run 即真正启动' });
  }

  // 先做一次能做的存在性检查（/mnt/... 这类 WSL 可见路径；Windows 原生路径查不了，交给下面的异步 error）
  if (exe.charAt(0) === '/' && !fs.existsSync(exe)) {
    return Object.assign({}, base, { ok: false, launched: false, already: false, waitedMs: Date.now() - started, steps: steps,
      error: 'blender.exe 不存在：' + exe, hint: '设 DSH_BLENDER_EXE 或 dsh-blender.config.json 的 blenderExe（当前值来自 ' + String(base.configFrom) + '）' });
  }
  let child = null;
  let spawnErr = null;
  try {
    child = spawn(exe, args, { env: childEnv, stdio: 'ignore', detached: true });
  } catch (e) {
    spawnErr = e;
  }
  if (child) {
    // ⚠ 实测坑（v0.9.4 自检抓到）：spawn 不存在的可执行文件**不会抛**，而是异步 emit 'error'。
    // 不挂 handler 的话整个后端进程会被 unhandled 'error' 打挂（自检第一版就是这样挂的）。
    child.on('error', (e) => { spawnErr = e; });
    try { child.unref(); } catch (e) { /* ignore */ }
  } else if (!spawnErr) {
    spawnErr = new Error('spawn 返回空句柄');
  }

  let listening = false;
  while (!spawnErr && Date.now() - started < waitMs) {
    if (await tcpProbe('127.0.0.1', addonPort, 700)) { listening = true; break; }
    await new Promise((r) => setTimeout(r, 500));
  }
  if (spawnErr) {
    return Object.assign({}, base, { ok: false, launched: false, already: false, waitedMs: Date.now() - started, steps: steps,
      pid: child ? child.pid : null,
      error: '起不来 Blender 进程（' + exe + '）：' + String((spawnErr && spawnErr.message) || spawnErr),
      hint: '可能是路径不存在 / 不可执行 / 不是 Windows 可执行文件。设 DSH_BLENDER_EXE 指到真的 blender.exe（WSL 形如 /mnt/d/.../blender.exe）' });
  }
  steps.push('已 spawn ' + exe + ' pid=' + String(child.pid) + '（detached，与后端进程解耦）');
  const boot = readJsonSafe(statusWsl);
  if (listening) steps.push('addon 端口 ' + addonPort + ' 已监听（' + String(Date.now() - started) + 'ms）');
  return Object.assign({}, base, {
    ok: listening, launched: true, already: false, listening: listening, pid: child.pid,
    waitedMs: Date.now() - started, steps: steps, boot: boot, bootScript: sp.win, args: args,
    hint: listening
      ? 'GUI Blender 已起来且 addon 已 Connect（端口 ' + addonPort + '）—— 现在可以用 rt_do / rt_see 了（写操作记得 blender_viewport op=lease 拿租约）'
      : '等待 ' + String(waitMs) + 'ms 端口仍未开：看 statusFile 里的 boot 结论（addon 没被扫到 / 不是 GUI 模式 / exe 起了但崩了）',
  });
}

/**
 * 三级诊断：把"连不上/超时"拆成可行动的原因（多 agent 场景的第一痛点）。
 *   TCP 不通            → blender-unreachable（Blender 没跑 / addon 没监听 9876）
 *   TCP 通 + ping 通     → main-thread-busy（addon 活着，但 bpy 命令被主线程占用挡住）
 *   TCP 通 + ping 不通   → addon-thread-stuck（客户端线程卡住，通常上一条长命令还在跑）
 * ping 是 addon 里唯一"不碰 bpy"的命令 → 它通不通能区分"传输层"还是"数据层"。
 */
export async function tcpProbe(host = ADDRESS.host, port = ADDRESS.port, timeoutMs = 2000) {
  return new Promise((resolve) => {
    const s = net.connect({ host, port });
    let settled = false;
    const done = (ok) => { if (settled) return; settled = true; try { s.destroy(); } catch (e) {} resolve(ok); };
    const t = setTimeout(() => done(false), timeoutMs);
    s.once("connect", () => { clearTimeout(t); done(true); });
    s.once("error", () => { clearTimeout(t); done(false); });
  });
}

export async function freshPing(host = ADDRESS.host, port = ADDRESS.port, timeoutMs = 2500) {
  const t0 = Date.now();
  const { proto } = await resolveProtocol({ host, port, prefer: CFG.addonProtocol, timeoutMs: Math.min(timeoutMs, 1500) });
  const plan = proto.plan("ping", {});
  return new Promise((resolve, reject) => {
    const s = net.connect({ host, port });
    let buf = "";
    const t = setTimeout(() => { try { s.destroy(); } catch (e) {} reject(new Error("ping timeout")); }, timeoutMs);
    s.once("error", (e) => { clearTimeout(t); try { s.destroy(); } catch (x) {} reject(e); });
    s.on("data", (d) => {
      buf += d.toString("utf8");
      let r = null;
      try { r = proto.decode(buf.trim()); } catch (e) { return; }   // 没攒够一整条 → 继续
      clearTimeout(t);
      try { s.destroy(); } catch (e) {}
      if (!r.ok) reject(new Error(r.message || "ping error"));
      else resolve(Date.now() - t0);
    });
    s.once("connect", () => s.write(proto.encode(plan.wire, { id: "ping", timeoutMs: timeoutMs })));
  });
}

export async function diagnose() {
  const proto = (await resolveProtocol({ host: ADDRESS.host, port: ADDRESS.port, prefer: CFG.addonProtocol }).catch(() => null));
  const panel = (proto && proto.proto.panelHint) || "「MCP for Blender」面板 → Connect";
  const tcp = await tcpProbe();
  if (!tcp) {
    return { kind: "blender-unreachable", tcp: false, ping: null,
      summary: "9876 端口没人监听 —— Blender 没在跑，或 addon 没连上",
      fix: "**agent 走这条路：blender_viewport(op=\"launch\") 一键拉起 GUI Blender 并自动 Connect addon**（v0.9.4 起，不必有人点面板）；"
        + "人工流程是：启动 Blender → 3D 视图按 N → " + panel + "（服务监听 127.0.0.1:" + String(CFG.addonPort) + "）",
      hint: "TCP " + CFG.addonHost + ":" + String(CFG.addonPort) + " 不通：Blender 没在运行，或 addon 面板没连上。"
        + "一键修：blender_viewport(op=\"launch\")（GUI + 自动 Connect，幂等）；人工修：N → " + panel };
  }
  try {
    const ms = await freshPing();
    return { kind: "main-thread-busy", tcp: true, ping: true, ping_ms: ms,
      summary: "addon 活着（ping 通），但 bpy 命令被主线程挡住 —— 正在渲染 / 模态操作 / 跑重活",
      fix: "等它空下来再调用；长渲染改走 blender_rt_headless（独立进程，不占 GUI 通道）",
      hint: "addon 活着、ping 通，但 bpy 命令超时 → Blender 主线程被占（渲染 / 模态操作 / 重活）。等它空下来，或把长渲染改用 blender_rt_headless（独立进程，不占 GUI 通道）" };
  } catch (e) {
    // TCP 通但 ping 不回：除了"addon 线程卡住"，还有一种高频原因是**协议不匹配**
    // （装了另一套 addon，或 addonProtocol 配错）。这里再探一次协议，把结论写进诊断。
    const detected = await detectProtocol(ADDRESS.host, ADDRESS.port, 1200).catch(() => null);
    const mismatch = detected && proto && detected.id !== proto.proto.id;
    if (!detected) {
      return { kind: "addon-thread-stuck", tcp: true, ping: false, error: String((e && e.message) || e),
        summary: "端口通但 ping 无响应 —— addon 客户端线程卡在上一条长命令上",
        fix: "不要连发；等它结束（agent 模式下可 blender_rt_loop op=stop 急停），仍无响应再重启 Blender。若 addon 面板显示的并不是「已连接」，先点一次 Connect",
        hint: "TCP 通但 ping 无响应 → addon 的客户端线程卡住（通常上一次长命令仍在执行）；不要连发，等它结束或重启 Blender" };
    }
    return { kind: "addon-thread-stuck", tcp: true, ping: false, error: String((e && e.message) || e),
      detected_protocol: detected.id, configured_protocol: CFG.addonProtocol,
      summary: mismatch
        ? "端口通、addon 也活着，但按 " + (proto && proto.proto.id) + " 协议发 ping 没有回包 —— 协议不匹配"
        : "端口通但 ping 无响应 —— addon 客户端线程卡在上一条长命令上",
      fix: mismatch
        ? "把 dsh-blender.config.json 的 addonProtocol 改成 \"" + detected.id + "\"（或设 \"auto\"）后重启后端：blender_viewport op=restart"
        : "不要连发；等它结束，仍无响应再重启 Blender",
      hint: mismatch
        ? "探测到的 addon 实际讲 " + detected.id + " 协议，与当前配置不一致 → 改 addonProtocol 或设 auto"
        : "TCP 通但 ping 无响应 → addon 的客户端线程卡住；不要连发，等它结束或重启 Blender" };
  }
}

/** 通道运行时实际使用的协议（给 /doctor、/who、工具输出用） */
export async function protocolInfo() {
  try {
    const r = await resolveProtocol({ host: ADDRESS.host, port: ADDRESS.port, prefer: CFG.addonProtocol });
    return { id: r.proto.id, label: r.proto.label, from: r.from, detected: r.detected || null, configured: CFG.addonProtocol };
  } catch (e) {
    return { id: null, error: String((e && e.message) || e), configured: CFG.addonProtocol };
  }
}

export class AddonClient {
  constructor(addr = ADDRESS) {
    this.addr = addr;
    this.sock = null;
    this.queue = Promise.resolve();
    this.buf = '';
    this.pending = null;
    this.proto = null;          // 首次使用时解析（显式配置 → 直取；auto → 探测一次并缓存）
    this.protocolInfo = null;
    this._seq = 0;
  }
  async protocol() {
    if (!this.proto) {
      this.protocolInfo = await resolveProtocol({ host: this.addr.host, port: this.addr.port, prefer: CFG.addonProtocol });
      this.proto = this.protocolInfo.proto;
    }
    return this.protocolInfo;
  }
  connect(timeoutMs = 4000) {
    return new Promise((resolve, reject) => {
      const s = net.connect({ host: this.addr.host, port: this.addr.port });
      const t = setTimeout(() => { s.destroy(); reject(new Error('connect timeout')); }, timeoutMs);
      s.once('connect', () => { clearTimeout(t); this.sock = s; resolve(true); });
      s.once('error', (e) => { clearTimeout(t); this.sock = null; reject(e); });
      s.on('data', (d) => this._onData(d));
      s.on('close', () => { this.sock = null; if (this.pending) { this.pending.reject(new Error('socket closed')); this.pending = null; } });
    });
  }
  _onData(d) {
    this.buf += d.toString('utf8');
    if (!this.pending) return;
    const proto = this.pending.proto;
    let r = null;
    try { r = proto.decode(this.buf.trim()); } catch (e) { return; }   // 未收全 → 继续攒
    this.buf = '';
    const p = this.pending; this.pending = null;
    clearTimeout(p.timer);
    if (!r.ok) { p.reject(new Error(r.message)); return; }
    try { p.resolve(p.post ? p.post(r.data) : r.data); }
    catch (err) { p.reject(err); }
  }
  async ensure() { if (!this.sock) await this.connect(); }
  send(type, params = {}, timeoutMs = 120000) {
    const run = async () => {
      const info = await this.protocol();
      const proto = info.proto;
      const plan = proto.plan(type, params);          // 可能抛：未知命令 / 缺参数
      if (plan.local) return plan.local();            // 本地桩（遥测 / 集成 status）不占 socket
      await this.ensure();
      return new Promise((resolve, reject) => {
        const timer = setTimeout(() => { this.pending = null; this.buf = ''; reject(new Error('addon timeout: ' + type)); }, timeoutMs);
        this.pending = { resolve: resolve, reject: reject, timer: timer, post: plan.post, proto: proto };
        this.sock.write(proto.encode(plan.wire, { id: 'rt' + String(++this._seq), timeoutMs: timeoutMs }));
      });
    };
    const p = this.queue.then(run, run);
    this.queue = p.catch(() => {});
    return p;
  }
  close() { if (this.sock) { this.sock.end(); this.sock = null; } }
}

export function createEngine(opts = {}) {
  const addon = new AddonClient(opts.address || ADDRESS);
  const injected = new Set();
  /** 通道侧指标：谁在写 / 忙不忙 / 队列多深（供 /status、/who 用） */
  const metrics = { calls: 0, errors: 0, timeouts: 0, inflight: 0, lastCmd: null, lastCmdAt: null, lastOkAt: null, lastError: null, lastDiagnosis: null, views: 0, headlessRuns: 0, lastHeadlessMs: null, workerStarts: 0, workerExecs: 0 };
  const rawSend = addon.send.bind(addon);
  addon.send = async (type, params, timeoutMs) => {
    metrics.calls++;
    metrics.inflight++;
    metrics.lastCmd = type;
    metrics.lastCmdAt = Date.now();
    try {
      const r = await rawSend(type, params, timeoutMs);
      metrics.lastOkAt = Date.now();
      metrics.lastDiagnosis = null;
      return r;
    } catch (e) {
      const msg = String((e && e.message) || e);
      metrics.errors++;
      metrics.lastError = msg;
      const transport = /timeout|closed|ECONN|ECONNRESET|socket/i.test(msg);
      if (transport) metrics.timeouts++;
      // v0.7.0：只有**传输/超时类**错误才做三级判定；执行类错误（代码抛错/编译错）不该被误判成 main-thread-busy
      if (transport) {
        try { e.diagnosis = await diagnose(); metrics.lastDiagnosis = e.diagnosis; } catch (x) { /* 诊断自身失败不影响原错误 */ }
      } else {
        e.kind = 'execution';
      }
      throw e;
    } finally {
      metrics.inflight--;
    }
  };
  /** 把 Blender 侧 python 模块注入运行中的 Blender（模块自己把 API 挂到 K 上） */
  const MODULE_ATTR = { RUNNER_READY: 'dsh_loop_api', PERF_READY: 'dsh_perf_api', VIEW_READY: 'dsh_view_api',
                        CONTRACT_READY: 'dsh_contract_api', PLAN_READY: 'dsh_plan_api',
                        TXN_READY: 'dsh_txn_api', QC_READY: 'dsh_qc_api',
                        QC_RENDER_READY: 'dsh_qc_render_api',
                        PRESET_READY: 'dsh_preset_api',
                        AUDIT_READY: 'dsh_audit_api', MONTAGE_READY: 'dsh_montage_api',
                        MOTION_READY: 'dsh_motion_api', DELIVER_READY: 'dsh_deliver_api',
                        GENERATOR_READY: 'dsh_generator_api' };
  /** 读 runtime 下的 python 模块源码（preload / 作业脚本拼接用） */
  const readModuleSource = (name) => fs.readFileSync(path.join(HERE, String(name).replace(/\.py$/, '') + '.py'), 'utf8');
  /**
   * v0.9.1（93-B3）：脚本路径解析。headless 的 `file=` 语义是 **.blend**（老成员误传 .py 会拿到
   * `File format is not supported`），现在：显式 `scriptFile=` 收 .py；`file=` 传 .py 时自动改当脚本并给提示。
   * Windows 路径（D:\ 或 \\wsl.localhost\）与 WSL 路径（/home/...）都能收。
   */
  function readScriptPath(p) {
    const s = String(p);
    const isWin = /^[A-Za-z]:[\\/]/.test(s) || s.slice(0, 2) === '\\\\';
    return { win: isWin ? s : wslToWin(s), wsl: isWin ? winToWsl(s) : s };
  }
  /** 会话名：默认进程级（多会话/多成员并存时用来隔离默认产物名），可用 DSH_SESSION 覆盖 */
  const SESSION_NAME = String(process.env.DSH_SESSION || ('plugin-pid-' + process.pid));
  /** 大结果落盘目录（v0.9.1 93-A3）：结构化结果不再只能从 stdout 里切片 */
  const RESULTS_DIR = path.join(winToWsl(WIN_TMP), 'results');
  function dumpResult(obj, runId2) {
    try {
      const s = JSON.stringify(obj);
      fs.mkdirSync(RESULTS_DIR, { recursive: true });
      const f = path.join(RESULTS_DIR, String(runId2 || ('r-' + Date.now().toString(36))) + '.json');
      fs.writeFileSync(f, s, 'utf8');
      return { path: wslToWin(f), bytes: s.length };
    } catch (e) {
      return { path: null, bytes: 0, error: String((e && e.message) || e).slice(0, 200) };
    }
  }
  /**
   * v0.9.0：preload 多个模块必须各占一个命名空间。
   * 旧实现把多个模块源码**拼进同一个 globals**，而每个 runtime 模块都定义 _j/_kernel/_store/_now →
   * 后一个模块把前一个的 helper 顶掉。实测 `preload="txn,deliver"` 直接把 txn 打崩（KeyError: 'marks'）。
   * 做法：每个模块 exec 到自己的 dict；私有名（_ 开头）不外泄，公开名与常用 import 照旧拷回 globals
   * （向后兼容：以前直接调 `dsh_loop_start` / `audit_mesh` 这类公开函数的写法仍然可用）。
   * 模块尾部 `K.dsh_x_api = {...}` 走的是真实的 dsh_rt_kernel 模块对象 → API 照旧落在 K 上。
   */
  function preloadChunk(name) {
    const clean = String(name).trim().replace(/\.py$/, '');
    const src = readModuleSource(clean);
    const ns = '__dsh_ns_' + clean.replace(/[^A-Za-z0-9_]/g, '_');
    return [
      '# ---- preload ' + clean + '.py（独立命名空间：私有 helper 不互相覆写）----',
      ns + ' = {"__name__": ' + JSON.stringify(clean) + '}',
      'exec(compile(' + JSON.stringify(src) + ', ' + JSON.stringify(clean + '.py') + ', "exec"), ' + ns + ')',
      'globals().update({k: v for k, v in ' + ns + '.items() if not k.startswith("_")})',
    ].join(String.fromCharCode(10));
  }
  async function injectModule(file, marker, versionExpr = '1') {
    const attr = MODULE_ATTR[marker] || ('dsh_' + String(marker).toLowerCase() + '_api');
    const hashAttr = attr + '_fp';
    // 内容指纹（size + mtime）：模块文件一改，下次调用自动重新注入 —— 开发闭环必需。
    // 注意：K 跨后端重启保留，所以"只查 hasattr"会让改了文件却不生效（v0.7.0 修）。
    let fp = 'x';
    try { const st = fs.statSync(file); fp = String(st.size) + '-' + String(Math.round(st.mtimeMs)); } catch (e) { fp = 'missing'; }
    // K 才是真相：Blender 重启后 K 会清空，仅靠本地 Set 会误判"已注入"
    try {
      const chk = await addon.send('execute_code', { code: KERNEL_BOOTSTRAP + '\nprint("HAVE " + str(hasattr(K, "' + attr + '")) + " FP " + str(getattr(K, "' + hashAttr + '", "none")))' }, 30000);
      const t = (chk && typeof chk.result === 'string') ? chk.result : '';
      if (t.includes('HAVE True') && t.includes(fp)) { injected.add(file); return true; }
      injected.delete(file);
    } catch (e) { /* 探测失败 → 走重新注入 */ }
    const src = fs.readFileSync(file, 'utf8');
    const tail = '\nK.' + hashAttr + ' = ' + JSON.stringify(fp) + '\nprint("' + marker + ' v%d" % ' + versionExpr + ')';
    const out = await addon.send('execute_code', { code: KERNEL_BOOTSTRAP + '\n' + src + tail }, 60000);
    const txt = (out && typeof out.result === 'string') ? out.result : '';
    if (!txt.includes(marker)) throw new Error(path.basename(file) + ' 注入失败: ' + txt.slice(0, 200));
    injected.add(file);
    return true;
  }
  const ensureRunner = () => injectModule(RUNNER_PATH, 'RUNNER_READY', 'RUNNER_VERSION');
  const ensurePerf = () => injectModule(PERF_PATH, 'PERF_READY', 'PERF_VERSION');
  const ensureView = () => injectModule(VIEW_PATH, 'VIEW_READY', 'VIEW_VERSION');
  const ensureContract = () => injectModule(CONTRACT_PATH, 'CONTRACT_READY', 'CONTRACT_VERSION');
  const ensurePlanner = () => injectModule(PLANNER_PATH, 'PLAN_READY', 'PLAN_VERSION');
  const ensureTxn = () => injectModule(TXN_PATH, 'TXN_READY', 'TXN_VERSION');
  const ensureQc = () => injectModule(QC_PATH, 'QC_READY', 'QC_VERSION');
  const ensureQcRender = () => injectModule(QC_RENDER_PATH, 'QC_RENDER_READY', 'QC_RENDER_VERSION');
  const ensurePreset = () => injectModule(PRESET_PATH, 'PRESET_READY', 'PRESET_VERSION');
  const ensureAudit = () => injectModule(AUDIT_PATH, 'AUDIT_READY', 'AUDIT_VERSION');
  const ensureMontage = () => injectModule(MONTAGE_PATH, 'MONTAGE_READY', 'MONTAGE_VERSION');
  /** 新模块（motion/deliver）在开发期可能还没落盘 —— 给一条清楚的错，而不是 ENOENT */
  const needFile = (f, what) => {
    if (!fs.existsSync(f)) throw new Error(what + ' 模块还没就位：' + path.basename(f) + '（v0.9.0 开发中）');
    return f;
  };
  const ensureMotion = () => injectModule(needFile(MOTION_PATH, 'motion'), 'MOTION_READY', 'MOTION_VERSION');
  const ensureDeliver = () => injectModule(needFile(DELIVER_PATH, 'deliver'), 'DELIVER_READY', 'DELIVER_VERSION');
  const ensureGenerator = () => injectModule(needFile(GENERATOR_PATH, 'generator'), 'GENERATOR_READY', 'GENERATOR_VERSION');
  /** perf/opt 通用调用：op 是 K.dsh_perf_api 里的函数名 */
  async function perfCall(op, payload) {
    await ensurePerf();
    const arg = payload === undefined ? '' : '_json.loads(' + JSON.stringify(JSON.stringify(payload)) + ')';
    const body = 'import json as _json' + '\n' + 'print("LOOP " + K.dsh_perf_api[' + JSON.stringify(op) + '](' + arg + '))';
    return extractLoop(await addon.send('execute_code', { code: KERNEL_BOOTSTRAP + '\n' + body }, 300000));
  }
  /**
   * 契约层 / 规划器统一调用：op = 契约 op（status、register_component、check_envelope、destructive_guard、verify、flip …）
   * 或 plan_<op>（load/validate/order/build/graph/status/help）。
   * 两个 python 模块都提供 dispatch(op, args)，因此这里只发 {op, args}。
   */
  async function planCall(op, payload) {
    const o = String(op || 'status');
    const isPlan = o.startsWith('plan_');
    if (o === 'evidence') {
      await ensureView();
      // 契约层的 evidence 需要 view.path：这里补默认工作目录下的证据文件（与 view.py 的默认出图分开）
      const p = payload && typeof payload === 'object' ? payload : {};
      p.view = p.view && typeof p.view === 'object' ? p.view : {};
      if (!p.view.path) p.view.path = blenderJoin(WIN_TMP, 'dsh_evidence_' + SESSION_NAME.replace(/[^A-Za-z0-9_.-]/g, '_') + '.png');
      payload = p;
    }            // 证据要出图 → 先注入 view.py
    if (isPlan) {
      await ensurePlanner();
      const body = 'print("LOOP " + K.dsh_plan_api["dispatch"](' + JSON.stringify(o.slice(5)) + ', _json.dumps(_json.loads('
        + JSON.stringify(JSON.stringify(payload || {})) + '))))';
      return extractLoop(await addon.send('execute_code', { code: KERNEL_BOOTSTRAP + '\nimport json as _json\n' + body }, 300000));
    }
    if (o.indexOf('qc_render') === 0) {
      // v0.8.8（P2-1）：渲染 harness —— 自动取景 + 三点光 + 逐张 jsonl + 预算判定。
      // 长活可 asJob=true 直接转作业层：无头进程天然隔离（不改用户场景），日志/产物落 outdir/jobs/<id>/。
      const p = (payload && typeof payload === 'object') ? payload : {};
      if (p.asJob) {
        const mods = ['qc', 'qc_render'].map(readModuleSource).join(String.fromCharCode(10));
        const driver = 'import json as _json' + String.fromCharCode(10)
          + 'print("HEADLESS " + K.dsh_qc_render_api["render_views"](_json.loads('
          + JSON.stringify(JSON.stringify(p)) + ')))';
        const j = jobStart({ file: p.file, outdir: p.outdir || WIN_TMP,
                             engine: (p.engine && String(p.engine) !== 'keep') ? String(p.engine) : 'eevee',
                             timeoutMs: Number(p.timeoutMs) || 3600000,
                             script: mods + String.fromCharCode(10) + driver });
        return { ok: true, mode: 'job', jobId: j.id, ms: 0, outdir: j.outdir, logDir: j.logDir,
                 jsonl: blenderJoin(String(p.outdir || WIN_TMP), 'render_views.jsonl'), job: j,
                 hint: '长活已转作业层：blender_rt_job(op="status"/"collect", id="' + j.id + '") 跟进；产物与日志在 outdir/jobs/ 下' };
      }
      await ensureQcRender();
      const body = 'print("LOOP " + K.dsh_qc_render_api["dispatch"](' + JSON.stringify(o.slice(3)) + ', _json.dumps(_json.loads('
        + JSON.stringify(JSON.stringify(p)) + '))))';
      return extractLoop(await addon.send('execute_code', { code: KERNEL_BOOTSTRAP + '\nimport json as _json\n' + body }, 900000));
    }
    if (o.indexOf('qc_') === 0) {
      // QC 走 blender_rt_plan 的 qc_* 前缀（验证与证据同属契约层；不新增工具）
      await ensureQc();
      const body = 'print("LOOP " + K.dsh_qc_api["dispatch"](' + JSON.stringify(o.slice(3)) + ', _json.dumps(_json.loads('
        + JSON.stringify(JSON.stringify(payload || {})) + '))))';
      return extractLoop(await addon.send('execute_code', { code: KERNEL_BOOTSTRAP + '\nimport json as _json\n' + body }, 300000));
    }
    if (o.indexOf('audit_') === 0) {
      // v0.9.0：网格体检接通。此前 audit.py 只在 preload 通道可达，docs 里文档化的
      // blender_rt_plan(op="audit_mesh") 会掉进契约层报 unknown contract op。
      await ensureAudit();
      const body = 'print("LOOP " + K.dsh_audit_api["dispatch"](' + JSON.stringify(o.slice(6)) + ', _json.dumps(_json.loads('
        + JSON.stringify(JSON.stringify(payload || {})) + '))))';
      return extractLoop(await addon.send('execute_code', { code: KERNEL_BOOTSTRAP + '\nimport json as _json\n' + body }, 600000));
    }
    if (o.indexOf('montage') === 0) {
      // v0.9.0：拼图接通（证据分级 L2：异常只给 3 个最大偏差区域）
      await ensureMontage();
      const body = 'print("LOOP " + K.dsh_montage_api["montage"](_json.loads('
        + JSON.stringify(JSON.stringify(payload || {})) + ')))';
      return extractLoop(await addon.send('execute_code', { code: KERNEL_BOOTSTRAP + '\nimport json as _json\n' + body }, 300000));
    }
    if (o.indexOf('motion_') === 0) {
      // v0.9.0（#17）：铰接/机构 —— 关节实测 + 扫掠验证 + URDF/USDA 导出
      await ensureMotion();
      const name = o.slice(7);
      const body = [
        'import json as _json',
        'A = K.dsh_motion_api',
        'P = _json.loads(' + JSON.stringify(JSON.stringify(payload || {})) + ')',
        'if isinstance(P, dict) and isinstance(P.get("args"), dict): P = P["args"]',
        'print("LOOP " + (A["dispatch"](' + JSON.stringify(name) + ', _json.dumps(P)) if A.get("dispatch") else A[' + JSON.stringify(name) + '](**P)))',
      ].join(String.fromCharCode(10));
      return extractLoop(await addon.send('execute_code', { code: KERNEL_BOOTSTRAP + '\n' + body }, 900000));
    }
    if (o.indexOf('deliver_') === 0) {
      // v0.9.0（#13）：交付导出 —— 单位盒 + 多组 OBJ/MTL + manifest(md5)
      await ensureDeliver();
      const name = o.slice(8);
      const body = [
        'import json as _json',
        'A = K.dsh_deliver_api',
        'P = _json.loads(' + JSON.stringify(JSON.stringify(payload || {})) + ')',
        'if isinstance(P, dict) and isinstance(P.get("args"), dict): P = P["args"]',
        'print("LOOP " + (A["dispatch"](' + JSON.stringify(name) + ', _json.dumps(P)) if A.get("dispatch") else A[' + JSON.stringify(name) + '](**P)))',
      ].join(String.fromCharCode(10));
      return extractLoop(await addon.send('execute_code', { code: KERNEL_BOOTSTRAP + '\n' + body }, 300000));
    }
    if (o.indexOf('generator_') === 0) {
      // v0.9.0（#18）：生成器注册表 —— 程序即形状 + 编译门（全新无头进程复现）+ 缓存 + diff
      await ensureGenerator();
      const body = 'print("LOOP " + K.dsh_generator_api["dispatch"](' + JSON.stringify(o.slice(10)) + ', _json.dumps(_json.loads('
        + JSON.stringify(JSON.stringify(payload || {})) + '))))';
      return extractLoop(await addon.send('execute_code', { code: KERNEL_BOOTSTRAP + '\nimport json as _json\n' + body }, 600000));
    }
    if (o.indexOf('gui_') === 0) {
      // v0.9.1（93-D1）：GUI 原语 —— rt_do 里 bpy.context.screen 为 None，这几条 op 由插件侧在真 UI 上下文执行
      await ensureView();
      const name = o.slice(4);
      const body = 'print("LOOP " + K.dsh_view_api[' + JSON.stringify('gui_' + name) + '](**_json.loads('
        + JSON.stringify(JSON.stringify(payload || {})) + ')))';
      return extractLoop(await addon.send('execute_code', { code: KERNEL_BOOTSTRAP + '\nimport json as _json\n' + body }, 120000));
    }
    if (o.indexOf('render_') === 0) {
      // v0.9.1（93-E1）：渲染锁/队列（render_lock / render_status…）—— 落在 qc_render 模块，跨进程文件锁
      await ensureQcRender();
      const body = 'print("LOOP " + K.dsh_qc_render_api["dispatch"](' + JSON.stringify(o.slice(7)) + ', _json.dumps(_json.loads('
        + JSON.stringify(JSON.stringify(payload || {})) + '))))';
      return extractLoop(await addon.send('execute_code', { code: KERNEL_BOOTSTRAP + '\nimport json as _json\n' + body }, 900000));
    }
    await ensureContract();
    const body = 'print("LOOP " + K.dsh_contract_api["dispatch"](' + JSON.stringify(o) + ', _json.dumps(_json.loads('
      + JSON.stringify(JSON.stringify(payload || {})) + '))))';
    return extractLoop(await addon.send('execute_code', { code: KERNEL_BOOTSTRAP + '\nimport json as _json\n' + body }, 300000));
  }
  /** v0.8.7：把 Blender 侧常见坑的报错补一句可执行提示（外部反馈 #4 的 P2-3） */
  function enrichError(msg, tb) {
    const s = String(msg || '');
    const t = String(tb || '');
    const both = s + ' ' + t;
    if (/已使用的库|in use|Cannot overwrite/i.test(both)) {
      return s + '  ← 提示：该 .blend 在本会话被当作库加载过。可先 bpy.data.libraries.remove(<该库>) 释放，或改用 bpy.ops.wm.save_as_mainfile(filepath=..., copy=True) 写副本（copy=True 不动当前 filepath）。';
    }
    if (/Cannot render, no camera/i.test(both)) {
      return s + '  ← 提示：场景没有相机（可能被清理/灯光重建循环删掉）。无头渲染前先设 scene.camera，或用 blender_rt_see 的自定义视角出图。';
    }
    // v0.8.9（外部会话反馈）：open_mainfile 之后旧引用全失效 + GUI 上下文指针失效
    if (/StructRNA of type .* has been removed|ReferenceError: StructRNA/i.test(both)) {
      return s + '  ← 提示：这是一枚「失效引用」—— bpy.ops.wm.open_mainfile() 会重建 bpy.data，换文件前抓到的 Object/Collection/材质引用全部作废。修法：open 之后重新从 bpy.data.objects[...] / bpy.context.scene 取对象，别复用旧变量。';
    }
    if (/context is incorrect/i.test(both)) {
      return s + '  ← 提示：bpy.ops 的上下文不满足。换过文件/换过模式后：先 bpy.context.view_layer.objects.active = obj、obj.select_set(True)，再调 op；仍报错就用 with bpy.context.temp_override(view_layer=vl, active_object=obj, selected_objects=[obj]): bpy.ops....（实测 open 后 temp_override 可正常 join）；实在不行拆成两次 rt_do 调用（每次调用都是新命名空间 = 重新取上下文）。';
    }
    if (/No such file or directory|无法读取/i.test(both) && /wsl\.localhost|\\\\wsl/i.test(both)) {
      return s + '  ← 提示：UNC 路径形态问题 —— //wsl.localhost/... 会被 Blender 当成「相对 .blend」；用反斜杠 UNC，或先 K.stage(path) 拷到本地再读。';
    }
    return s;
  }
  // ---------------------------------------------------------------- 作业层（v0.8.5）
  // 长任务后台化：start 立刻返回 job id（不占客户端连接）；status/collect/kill 轮询；日志与产物落在 outdir/jobs/<id>/。
  const jobs = new Map();
  function jobDir(id, outdir) {
    const base = outdir ? winToWsl(String(outdir)) : winToWsl(WIN_TMP);
    const d = path.join(base, 'jobs', id);
    fs.mkdirSync(d, { recursive: true });
    return d;
  }
  function jobArtifacts(j) {
    const dir = j.outdir ? winToWsl(String(j.outdir)) : null;
    if (!dir) return [];
    try {
      const isNoise = (n) => n.indexOf('__pycache__') === 0 || /\.pyc$/.test(n) || /\.blend[12]$/.test(n) || n.indexOf('tmp') === 0;
      return fs.readdirSync(dir).map((n) => { const p = path.join(dir, n); let st = null; try { st = fs.statSync(p); } catch (e) {} return st && st.isFile() && !isNoise(n) ? { name: n, bytes: st.size, mtimeMs: st.mtimeMs } : null; })
        .filter(Boolean).sort((a, b) => b.mtimeMs - a.mtimeMs).slice(0, 40);
    } catch (e) { return []; }
  }
  function jobSnapshot(j) {
    // v0.8.9：作业结束后 ms 必须冻结（旧版用 Date.now()，done 的作业放一会儿再看会显示成分钟级）
    const now = Date.now();
    const running = j.status === 'running';
    const outWsl = j.outdirWsl || (j.outdir ? winToWsl(String(j.outdir)) : winToWsl(WIN_TMP));
    let logBytes = j.logBytes || 0;
    if (running) { try { logBytes = fs.statSync(path.join(j.dir, 'stdout.log')).size; } catch (e) { /* 还没建 */ } }
    return { kind: 'job', id: j.id, runId: j.runId || null, status: j.status, pid: j.pid,
             pidAlive: running ? pidAlive(j.pid) : false,
             ms: (j.finishedAt || now) - j.startedAt, timeoutMs: j.timeoutMs,
             outdir: j.outdir || WIN_TMP, outdirWsl: outWsl,
             logDir: wslToWin(j.dir), logDirWsl: j.dir,
             stdoutLog: wslToWin(path.join(j.dir, 'stdout.log')), stderrLog: wslToWin(path.join(j.dir, 'stderr.log')),
             stdoutLogWsl: path.join(j.dir, 'stdout.log'), stderrLogWsl: path.join(j.dir, 'stderr.log'),
             exitCode: j.exitCode, signal: j.signal, engine: j.engineMode,
             // ---- v0.9.3（D5）：运行中就能看到进度（不再"6 分钟纯黑盒"）
             stage: j.stage || null, stageName: j.stageName || null, stageAt: j.stageAt || null,
             stageAgoMs: j.stageAt ? (now - j.stageAt) : null,
             lastOutputAt: j.lastOutputAt || null,
             idleMs: j.lastOutputAt ? (now - j.lastOutputAt) : (now - j.startedAt),
             lines: j.lines || 0, logBytes: logBytes,
             // ---- v0.9.3（D2/D3）：结构化结果（不必再手写正则从 stdout 里抠）
             resultJson: j.parsed || null, result: j.parsed || null, resultParseError: j.parseError || null,
             resultPath: j.resultPath || null, resultPathWsl: j.resultPathWsl || null,
             resultBytes: j.resultBytes || 0, resultTruncated: !!j.resultTruncated,
             stdoutTail: j.stdoutTail || null, stderrTail: j.stderrTail || null,
             pathWarnings: j.pathWarnings || [], shots: j.shots || null,
             inputFile: j.inputFile || null, scriptFile: j.scriptFile || null,
             lastException: j.lastException || null,
             artifacts: running ? [] : jobArtifacts(j) };
  }
  /**
   * 起一个后台作业。
   * v0.9.3（D6.1）：与 headless 同形参 —— script_file / args / env / factory_startup / bootstrap /
   *   workdir / include_noise 都收（旧版只认 script，靠 DSH_ARGS 取参的 preview.py 无法复用）。
   * v0.9.3（D5）：env 前导 + PYTHONUNBUFFERED + stage 心跳扫描（stdout.log 运行期就有增量）。
   */
  function jobStart(opts = {}) {
    const id = 'job-' + Date.now().toString(36) + Math.random().toString(36).slice(2, 5);
    const outdirWsl = opts.outdir ? winToWsl(String(opts.outdir)) : winToWsl(WIN_TMP);
    const outdirWin = opts.outdir ? wslToWin(String(opts.outdir)) : WIN_TMP;
    const dir = jobDir(id, outdirWsl);
    const outPath = path.join(dir, 'stdout.log'), errPath = path.join(dir, 'stderr.log');
    const engineMode = String(opts.engine === undefined || opts.engine === null ? 'eevee' : opts.engine).toLowerCase();
    const runId = /^[A-Za-z0-9_-]{1,64}$/.test(String(opts.runId || '')) ? String(opts.runId)
      : ('run-' + Date.now().toString(36) + Math.random().toString(36).slice(2, 5));
    // ---- script_file（与 headless 同一套语义：显式收 .py；file= 传 .py 自动改当脚本）
    let scriptFileInfo = null;
    let script = opts.script ? String(opts.script) : '';
    const fileLooksPython = !!(opts.file && /\.py$/i.test(String(opts.file)));
    if (opts.scriptFile || fileLooksPython) {
      const given = String(opts.scriptFile || opts.file);
      try {
        const rp = readScriptPath(given);
        script = fs.readFileSync(rp.wsl, 'utf8');
        scriptFileInfo = { given: given, resolved: rp.win, bytes: script.length,
                           auto_from_file: !opts.scriptFile && fileLooksPython };
      } catch (e) {
        throw Object.assign(new Error('读不到脚本文件：' + given + ' —— ' + String((e && e.message) || e).slice(0, 160)),
                            { hint: 'script_file 用 .py 路径（Windows D:\\… 或 WSL /home/… 都行）；file= 是 .blend' });
      }
    }
    const inputFileInfo = (opts.file && !fileLooksPython)
      ? fileFingerprint(wslToWin(String(opts.file)), winToWsl(String(opts.file))) : null;
    const args = ['-b'];
    if (opts.file && !fileLooksPython) args.push(wslToWin(String(opts.file)));
    if (opts.factoryStartup !== false) args.push('--factory-startup');
    const envPairs = { DSH_RUN_ID: runId, DSH_OUTDIR: outdirWin, DSH_SESSION: SESSION_NAME,
                       DSH_PLUGIN_VERSION: PLUGIN_VERSION,
                       DSH_ARGS: JSON.stringify(Array.isArray(opts.args) ? opts.args.map(String) : []) };
    if (opts.env && typeof opts.env === 'object') for (const k of Object.keys(opts.env)) envPairs[String(k)] = String(opts.env[k]);
    const parts = [envPrelude(envPairs)];
    if (opts.bootstrap !== false) parts.push(KERNEL_BOOTSTRAP);
    parts.push('_dsh_stage_emit("engine-prelude")');
    parts.push(['try:', '    _DSH_FP0 = bpy.context.scene.render.filepath', 'except Exception:', '    _DSH_FP0 = None'].join(String.fromCharCode(10)));
    if (engineMode !== 'keep') parts.push(GPU_PRELUDE.replace(/__DSH_ENGINE__/g, "'" + (engineMode === 'cycles' ? 'cycles' : 'eevee') + "'").replace(/__DSH_GPU_MANUAL__/g, 'False'));
    // v0.8.9：作业层也支持 preload（与 headless 同一套：把 runtime/<name>.py 源码拼到脚本开头）
    const jobMods = Array.isArray(opts.preload) ? opts.preload : (opts.preload ? String(opts.preload).split(',') : []);
    // v0.9.3（F1）：作业层同样支持 shots（as_job=true 时 headless 就是转到这里跑的）
    const jobShotsSpec = Array.isArray(opts.shots) ? { views: opts.shots }
      : (opts.shots && typeof opts.shots === 'object' ? Object.assign({}, opts.shots) : null);
    const jobShotsWanted = !!(jobShotsSpec && Array.isArray(jobShotsSpec.views) && jobShotsSpec.views.length);
    if (jobShotsWanted && !jobMods.some((m) => String(m).trim().replace(/\.py$/, '') === 'qc_render')) jobMods.push('qc_render');
    for (const m of jobMods) {
      const name = String(m).trim().replace(/\.py$/, '');
      if (!name) continue;
      const f = path.join(HERE, name + '.py');
      if (!fs.existsSync(f)) throw new Error('preload 找不到模块：' + f);
      parts.push(preloadChunk(name));
    }
    parts.push('_dsh_stage_emit("preload-done")');
    if (opts.workdir) parts.push(workdirBlock(opts.workdir));
    parts.push(script);
    parts.push('_dsh_stage_emit("script-end")');
    if (jobShotsWanted) parts.push(shotsBlock(Object.assign({ engine: 'keep', lock: true, warmup: true, tag: 'shot' }, jobShotsSpec, { outdir: outdirWin })));
    parts.push(PATH_GUARD);
    parts.push('_dsh_stage_emit("done")');
    const sp = writeHeadlessScript(parts.join('\n'));
    args.push('--python', sp.win, '--');
    if (opts.outdir) args.push(outdirWin);
    if (Array.isArray(opts.args)) args.push.apply(args, opts.args.map(String));
    const timeoutMs = Math.max(1000, Math.min(86400000, Number(opts.timeoutMs) || 3600000));
    const so = fs.createWriteStream(outPath), se = fs.createWriteStream(errPath);
    const j = { kind: 'job', id: id, runId: runId, child: null, pid: null, startedAt: Date.now(), status: 'running',
                exitCode: null, signal: null, timeoutMs: timeoutMs, outdir: outdirWin, outdirWsl: outdirWsl,
                dir: dir, engineMode: engineMode, parsed: null, parseError: null, lastException: null,
                script: sp.win, scriptFile: scriptFileInfo, inputFile: inputFileInfo, args: args,
                stage: null, stageAt: null, stageName: null, lastOutputAt: Date.now(), lines: 0, logBytes: 0,
                resultPath: null, resultBytes: 0, resultTruncated: false, pathWarnings: [], shots: null };
    const child = spawn(BLENDER_EXE, args, { env: withWslEnv(baseChildEnv(Object.assign({}, envPairs)), opts.env),
                                             stdio: ['ignore', 'pipe', 'pipe'], detached: false });
    j.child = child; j.pid = child.pid;
    // v0.9.3（D5）：stage 扫描 + 行数/字节计数（stdout 仍然 pipe 到日志文件）
    const scanOut = makeStageScanner(j), scanErr = makeStageScanner(j);
    child.stdout.on('data', (d) => { try { j.logBytes += d.length; scanOut(d.toString('utf8')); } catch (e) { /* ignore */ } });
    child.stderr.on('data', (d) => { try { scanErr(d.toString('utf8')); } catch (e) { /* ignore */ } });
    child.stdout.pipe(so); child.stderr.pipe(se);
    jobs.set(id, j); if (jobs.size > 20) { const k = jobs.keys().next().value; if (k !== id) jobs.delete(k); }
    ledgerAppend({ id: id, kind: 'job', status: 'running', startedAt: j.startedAt, pid: j.pid, script: sp.win,
                   runId: runId, outdir: outdirWin, engine: engineMode, timeoutMs: timeoutMs, stage: null });
    j.timer = setTimeout(() => { j.status = 'killed'; try { child.kill('SIGKILL'); } catch (e) {} }, timeoutMs);
    child.on('close', (code, sig) => {
      clearTimeout(j.timer); j.exitCode = code; j.signal = sig; j.finishedAt = Date.now();
      if (j.status !== 'killed') j.status = (code === 0) ? 'done' : 'failed';
      try {
        const txt = fs.readFileSync(outPath, 'utf8');
        // v0.9.3（D3）：解析失败不再覆盖 raw —— 单独记 resultParseError，stdoutTail 保留原文
        const lines = txt.split('\n').filter((l) => l.indexOf('HEADLESS ') === 0);
        if (lines.length) {
          const lastHead = lines[lines.length - 1].slice('HEADLESS '.length).trim();
          try { j.parsed = JSON.parse(lastHead); }
          catch (e) { j.parseError = { message: String((e && e.message) || e).slice(0, 200), line: lastHead.slice(0, 400) }; }
        }
        const sl = txt.split('\n').filter((l) => l.indexOf('DSH_SHOTS ') === 0);
        if (sl.length) { try { j.shots = JSON.parse(sl[sl.length - 1].slice('DSH_SHOTS '.length).trim()); } catch (e) { /* ignore */ } }
        j.stdoutTail = txt.length > 4000 ? txt.slice(-4000) : txt;
        j.pathWarnings = pathAudit(txt, '');
      } catch (e) { /* ignore */ }
      try {
        const er = fs.readFileSync(errPath, 'utf8');
        j.stderrTail = er.length > 2000 ? er.slice(-2000) : er;
        const m = er.match(/^[\w.]*(?:Error|Exception|Warning)\b.*$/gm);
        if (m && m.length) j.lastException = m[m.length - 1].slice(0, 300);
        if (j.pathWarnings.length === 0) j.pathWarnings = pathAudit('', er);
      } catch (e) { /* ignore */ }
      // v0.9.3（D2）：作业结果也走 out_json 落盘（>4KB 自动落），与 headless 同一套
      try {
        if (j.parsed !== null && j.parsed !== undefined) {
          const s = JSON.stringify(j.parsed);
          j.resultBytes = s.length;
          if (opts.outJson) {
            const t = readScriptPath(String(opts.outJson));
            fs.mkdirSync(path.dirname(t.wsl), { recursive: true });
            fs.writeFileSync(t.wsl, s, 'utf8');
            j.resultPath = t.win; j.resultPathWsl = t.wsl;
          } else if (s.length > 4000) {
            const d = dumpResult(j.parsed, id);
            j.resultPath = d.path; j.resultPathWsl = d.path ? winToWsl(d.path) : null;
          }
          j.resultTruncated = !!(j.resultPath && s.length > 4000);
        }
      } catch (e) { /* ignore */ }
      // v0.8.10（D4）：终态摘要落盘 + 台账追加（后端重启后仍可查）
      try {
        const arts = jobArtifacts(j).slice(0, 20);
        const summary = { id: id, kind: 'job', status: j.status, exitCode: j.exitCode, signal: j.signal,
                          startedAt: j.startedAt, finishedAt: Date.now(), ms: Date.now() - j.startedAt,
                          outdir: outdirWin, script: sp.win, engine: engineMode, runId: runId,
                          stage: j.stageName || null,
                          artifacts: arts.map((a) => a.name + '(' + a.bytes + 'B)'),
                          resultSummary: j.parsed ? JSON.stringify(j.parsed).slice(0, 400) : null,
                          lastException: j.lastException || null, pluginVersion: PLUGIN_VERSION };
        try { fs.writeFileSync(path.join(j.dir, 'summary.json'), JSON.stringify(summary, null, 1), 'utf8'); } catch (e) { /* ignore */ }
        ledgerAppend(Object.assign({}, summary, { artifacts: arts.slice(0, 8).map((a) => a.name) }));
      } catch (e) { /* ignore */ }
    });
    child.on('error', (e) => { j.status = 'failed'; j.lastException = String((e && e.message) || e); });
    return jobSnapshot(j);
  }
  // ---- v0.8.10（A2/D4）：无头运行台账 + 磁盘台账（后端重启后仍可查）
  const runs = new Map();
  const LEDGER = path.join(winToWsl(WIN_TMP), 'jobs', 'index.jsonl');
  // ---- v0.9.3（D4/D8 同源）：workDir 可写性 —— 旧版台账/日志写失败是**静默**的
  // （实测：workDir 落在只读挂载上时，磁盘台账一条都写不进去，而 op=list/status 只会说"没有记录"）。
  let ledgerWarn = null;
  let workdirProbeCache = null;
  function workdirProbe() {
    if (workdirProbeCache) return workdirProbeCache;
    const out = { wsl: WSL_TMP, win: WIN_TMP, writable: false, error: null };
    try {
      fs.mkdirSync(WSL_TMP, { recursive: true });
      const probe = path.join(WSL_TMP, '.dsh_write_probe_' + process.pid);
      fs.writeFileSync(probe, 'ok', 'utf8');
      fs.unlinkSync(probe);
      out.writable = true;
    } catch (e) { out.error = String((e && e.code) || (e && e.message) || e).slice(0, 200); }
    workdirProbeCache = out;
    return out;
  }
  function ledgerAppend(rec) {
    try {
      fs.mkdirSync(path.dirname(LEDGER), { recursive: true });
      fs.appendFileSync(LEDGER, JSON.stringify(Object.assign({ pluginVersion: PLUGIN_VERSION, at: Date.now() }, rec)) + String.fromCharCode(10), 'utf8');
      ledgerWarn = null;
    } catch (e) {
      // 静默失败是这一版要消灭的东西：记下来，让 /status 与 op=list 能说出来
      ledgerWarn = { path: LEDGER, error: String((e && e.code) || (e && e.message) || e).slice(0, 200),
        hint: 'workDir 不可写 → 磁盘台账/日志落不下去（内存台账与产物不受影响）。改 dsh-blender.config.json 的 workDir 或 DSH_BLENDER_WORKDIR 到一个两端都可写的目录（WSL 路径也可，如 /home/<user>/dsh-blender-work）。' };
    }
  }
  function ledgerState() {
    const p = workdirProbe();
    return { path: LEDGER, writable: !!p.writable && !ledgerWarn, workDir: p,
             lastError: ledgerWarn ? ledgerWarn.error : (p.writable ? null : p.error),
             hint: ledgerWarn ? ledgerWarn.hint : (p.writable ? null : ('workDir 不可写（' + String(p.error) + '）：台账/日志会静默失败 —— 换一个可写 workDir（WSL 路径也行）。')) };
  }
  function ledgerList(limit = 40) {
    try {
      const lines = fs.readFileSync(LEDGER, 'utf8').trim().split(String.fromCharCode(10)).filter(Boolean).slice(-limit * 4);
      const by = new Map();
      for (const l of lines) {
        try { const o = JSON.parse(l); by.set(o.id, Object.assign(by.get(o.id) || {}, o)); } catch (e) { /* skip */ }
      }
      return Array.from(by.values()).sort((a, b) => (b.startedAt || 0) - (a.startedAt || 0)).slice(0, limit);
    } catch (e) { return []; }
  }
  /**
   * v0.9.3（D2）：run（blender_rt_headless 的同步执行）与 job 同一份结构化回执形状 ——
   * collect/status 收 run-… 与 job-… 都是同一套字段，调用方不必分辨两者。
   */
  function runSnapshot(r) {
    const now = Date.now();
    const running = r.status === 'running';
    return { kind: 'headless', id: r.id, status: r.status, pid: r.pid,
             pidAlive: running ? pidAlive(r.pid) : false,
             ms: (r.finishedAt || now) - r.startedAt, timeoutMs: r.timeoutMs || null,
             outdir: r.outdir || WIN_TMP, outdirWsl: r.outdirWsl || null,
             logs: r.logs || null, script: r.script || null, exitCode: r.exitCode, signal: r.signal || null,
             timedOut: r.timedOut || false, failureClass: r.failureClass || null,
             stage: r.stage || null, stageName: r.stageName || null, stageAt: r.stageAt || null,
             stageAgoMs: r.stageAt ? (now - r.stageAt) : null,
             lastOutputAt: r.lastOutputAt || null,
             idleMs: r.lastOutputAt ? (now - r.lastOutputAt) : (now - r.startedAt),
             lines: r.lines || 0,
             logBytes: (() => { try { const p = r.logs && r.logs.stdoutWsl; return p ? fs.statSync(p).size : 0 } catch (e) { return 0 } })(),
             resultJson: r.result || null, result: r.result || null, resultParseError: r.parseError || null,
             resultPath: r.resultPath || null, resultPathWsl: r.resultPathWsl || null,
             resultBytes: r.resultBytes || 0, resultTruncated: !!r.resultTruncated,
             stdoutTail: r.stdoutTail || null, stderrTail: r.stderrTail || null,
             pathWarnings: r.pathWarnings || [], shots: r.shots || null,
             inputFile: r.inputFile || null,
             artifacts: (r.artifacts || []).map((n) => (typeof n === 'string' ? { name: n } : n)),
             artifactCount: (r.artifacts || []).length,
             expect: r.expect || null, pluginVersion: PLUGIN_VERSION,
             hint: '这是 blender_rt_headless 的运行台账（run 与 job 同一 id 空间）：产物在 outdir，日志见 logs；'
                 + '结果在 resultJson / resultPath（运行中则用 stage / idleMs 看进展）' };
  }
  /** 磁盘台账记录（后端重启后的历史）：running 但 pid 已不存在 → 收敛成 stale，不再谎报 running（D6.3） */
  function reconcileLedger(rec) {
    if (!rec || rec.status !== 'running') return rec;
    if (rec.pid && pidAlive(rec.pid)) return rec;
    return Object.assign({}, rec, { status: 'stale', wasStatus: 'running', pidAlive: false,
      staleReason: rec.pid ? ('pid ' + rec.pid + ' 已不存在（进程被外部杀掉，或后端重启后丢了句柄）')
                           : '台账里没有 pid（后端重启前只记了 running）',
      hint: '这条只是**磁盘台账**里的历史记录（内存句柄已丢）。要确认进程是否还在：PowerShell Get-Process blender；'
          + '要清孤儿进程：Stop-Process。新作业用 op=kill 收。' });
  }
  function jobStatus(id) {
    const key = String(id);
    const j = jobs.get(key);
    if (j) { const s = jobSnapshot(j); s.stale = false; return s; }
    const r = runs.get(key);
    if (r) { const s = runSnapshot(r); s.stale = false; return s; }
    const led = ledgerList(200).find((x) => x.id === key);
    return led ? Object.assign({ kind: led.kind || 'ledger', fromLedger: true }, reconcileLedger(led))
               : { id: key, status: 'unknown', ok: false, hint: '没有这个 id 的记录（内存台账 + 磁盘台账都查过了）' };
  }
  /** 从日志文件里取尾部（run 与 job 通用） */
  function tailFile(p, tail) {
    try { return fs.readFileSync(p, 'utf8').slice(-tail); } catch (e) { return null; }
  }
  function jobCollect(id, tail = 4000) {
    const key = String(id);
    const j = jobs.get(key);
    if (j) {
      const snap = jobSnapshot(j);
      const so = tailFile(path.join(j.dir, 'stdout.log'), tail);
      const se = tailFile(path.join(j.dir, 'stderr.log'), tail);
      if (so !== null) snap.stdoutTail = so;
      if (se !== null) snap.stderrTail = se;
      return snap;
    }
    const r = runs.get(key);
    if (r) {
      const snap = runSnapshot(r);
      const soP = r.logs && r.logs.stdoutWsl ? r.logs.stdoutWsl : null;
      const seP = r.logs && r.logs.stderrWsl ? r.logs.stderrWsl : null;
      // v0.9.3（D5）：日志是边跑边写的 → 运行中也能 tail（旧版 collect 对 run 直接回 unknown）
      if (soP) { const t = tailFile(soP, tail); if (t !== null) snap.stdoutTail = t; }
      if (seP) { const t = tailFile(seP, tail); if (t !== null) snap.stderrTail = t; }
      snap.hint = 'run 与 job 同一 id 空间；本条目来自 blender_rt_headless 的同步执行';
      return snap;
    }
    const led = ledgerList(200).find((x) => x.id === key);
    if (!led) return { id: key, status: 'unknown', ok: false, hint: '没有这个 id 的记录；op=list 看全部' };
    const rec = reconcileLedger(led);
    if (rec.status === 'running' || rec.status === 'stale') {
      rec.stdoutTail = rec.stdoutLog ? tailFile(winToWsl(rec.stdoutLog), tail) : null;
      rec.stderrTail = rec.stderrLog ? tailFile(winToWsl(rec.stderrLog), tail) : null;
    }
    return Object.assign({ kind: led.kind || 'ledger', fromLedger: true }, rec);
  }
  /**
   * v0.9.3（D6.2）：阻塞等待 —— 旧版只能循环 op=status，会撞外层 harness 的
   * "Repeated tool call detected (consecutive_calls: 5/8)"；op=collect 又不等。
   * 语义：等到终态或 timeout_ms 到点（默认 120s，上限 600s/次，可反复调）。
   */
  async function jobWait(id, timeoutMs = 120000) {
    const key = String(id);
    const budget = Math.max(1000, Math.min(600000, Number(timeoutMs) || 120000));
    const t0 = Date.now();
    let waited = 0;
    for (;;) {
      const st = jobStatus(key);
      if (st.status !== 'running') return Object.assign(jobCollect(key), { waitedMs: Date.now() - t0, waitTimedOut: false });
      if (Date.now() - t0 >= budget) {
        return Object.assign(jobCollect(key), { waitedMs: Date.now() - t0, waitTimedOut: true,
          hint: '等待窗口到点，任务**仍在跑**（不是失败）：再调一次 op=wait，或 op=collect 看 stage/idleMs' });
      }
      await new Promise((r2) => setTimeout(r2, 500));
      waited++;
      if (waited > 2400) break;                      // 安全上限（>20min 的极端轮次）
    }
    return Object.assign(jobCollect(key), { waitedMs: Date.now() - t0, waitTimedOut: true });
  }
  function jobKill(id) {
    const key = String(id);
    const j = jobs.get(key);
    if (j) {
      if (j.status === 'running') { j.status = 'killed'; try { j.child.kill('SIGKILL'); } catch (e) { /* 可能已退出 */ } }
      return jobSnapshot(j);
    }
    const r = runs.get(key);
    if (r) {
      if (r.status === 'running') {
        r.status = 'killed'; r.finishedAt = Date.now();
        try { if (r.child) r.child.kill('SIGKILL'); } catch (e) { /* 可能已退出 */ }
        ledgerAppend({ id: key, kind: 'headless', status: 'killed', killedBy: 'op=kill', ms: Date.now() - r.startedAt });
      }
      return runSnapshot(r);
    }
    const led = ledgerList(200).find((x) => x.id === key);
    const rec = led ? reconcileLedger(led) : null;
    // v0.9.3（D6.4）：未知 id **不抛错** —— 旧版回 unknown 让调用方流程中断
    return { id: key, ok: true, status: (rec && rec.status) || 'unknown', alreadyFinished: !(rec && rec.status === 'running'),
             fromLedger: !!rec,
             note: rec ? '这条来自磁盘台账，内存里没有在跑的句柄（已结束或后端重启过）；无需 kill'
                       : '没有这个 id 的记录（可能已结束/被清理）—— 不需要 kill，op=list 看当前作业' };
  }
  function jobList() {
    const mem = Array.from(jobs.values()).map(jobSnapshot).concat(Array.from(runs.values()).map(runSnapshot));
    const ids = new Set(mem.map((x) => x.id));
    const disk = ledgerList(60).filter((x) => !ids.has(x.id)).map((x) => Object.assign({ kind: x.kind || 'ledger', fromLedger: true }, reconcileLedger(x)));
    return mem.concat(disk);
  }
  /** v0.8.7：场景世代号（对象数/网格数/材质数/名字哈希）—— 用于发现「别人的重建把我的装配清掉了」 */
  async function sceneEpoch() {
    try {
      const out = await addon.send('execute_code', { code: KERNEL_BOOTSTRAP + '\nprint("EPOCH " + _dsh_epoch_json())' }, 20000);
      const t = (out && typeof out.result === 'string') ? out.result : '';
      const l = t.split('\n').filter((x) => x.indexOf('EPOCH ') === 0);
      return l.length ? JSON.parse(l[l.length - 1].slice('EPOCH '.length)) : null;
    } catch (e) { return null; }
  }

  // ---------------------------------------------------------------- 热无头 worker
  // 常驻 blender -b：阻塞 accept 跑在主线程（无头下 timers 不触发，主线程执行 bpy 才安全），
  // 复用持久内核 K；一次只处理一个请求（长代码会占住 worker —— 这是"热"的代价，也是安全的来源）。
  const WORKER_PORT = Number(process.env.DSH_BLENDER_WORKER_PORT) || CFG.workerPort || 9879;
  let worker = { child: null, port: WORKER_PORT, ready: false, startedAt: null, gpu: null, pid: null,
                 lastError: null, stdoutTail: '', stderrTail: '' };
  function workerAlive() { return !!(worker.child && worker.child.exitCode === null && !worker.child.signalCode); }
  function workerSnapshot() {
    return { alive: workerAlive(), ready: worker.ready, pid: worker.pid, port: worker.port,
             uptimeMs: worker.startedAt ? Date.now() - worker.startedAt : null, gpu: worker.gpu,
             lastError: worker.lastError, stdoutTail: worker.stdoutTail ? worker.stdoutTail.slice(-1200) : null };
  }
  async function workerStart(opts = {}) {
    if (workerAlive() && worker.ready) return workerSnapshot();
    if (worker.child) { try { worker.child.kill('SIGKILL'); } catch (e) {} worker.child = null; }
    const port = Number(opts.port) || WORKER_PORT;
    const gpuMode = String(opts.gpu === undefined || opts.gpu === null ? 'auto' : opts.gpu);
    const engineMode = String(opts.engine === undefined || opts.engine === null ? 'eevee' : opts.engine);
    const args = ['-b', '--factory-startup', '--python', wslToWin(WORKER_PATH), '--',
                  '--port', String(port), '--gpu', gpuMode, '--engine', engineMode];
    const childEnv = Object.assign({}, process.env, { PYTHONIOENCODING: 'utf-8' });
    if (opts.useUserConfig) {
      if (USER_CONFIG_WIN) childEnv.BLENDER_USER_CONFIG = USER_CONFIG_WIN;
      if (USER_SCRIPTS_WIN) childEnv.BLENDER_USER_SCRIPTS = USER_SCRIPTS_WIN;
    }
    const child = spawn(BLENDER_EXE, args, { env: childEnv, stdio: ['ignore', 'pipe', 'pipe'] });
    worker = { child: child, port: port, ready: false, startedAt: Date.now(), gpu: null, pid: child.pid,
               lastError: null, stdoutTail: '', stderrTail: '' };
    const info = await new Promise((resolve) => {
      let so = '', se = '';
      const t = setTimeout(() => resolve({ timedOut: true }), Math.max(20000, Number(opts.startTimeoutMs) || 90000));
      child.stdout.on('data', (d) => {
        so += d.toString('utf8'); worker.stdoutTail = so.slice(-4000);
        const m = so.match(/DSH_WORKER (\{[\s\S]*?\})\s*(\r?\n|$)/);
        if (m) { try { clearTimeout(t); resolve({ ok: JSON.parse(m[1]) }); } catch (e) { /* 继续攒 */ } }
      });
      child.stderr.on('data', (d) => { se += d.toString('utf8'); worker.stderrTail = se.slice(-4000); });
      child.on('close', (code, sig) => { clearTimeout(t); resolve({ exited: true, code: code, signal: sig }); });
      child.on('error', (e) => { clearTimeout(t); resolve({ spawnErr: e }); });
    });
    if (info.spawnErr) {
      const e = new Error('worker 起不来（' + BLENDER_EXE + '）：' + String((info.spawnErr && info.spawnErr.message) || info.spawnErr));
      e.hint = '检查 blenderExe 配置与 runtime/worker.py 是否存在';
      throw e;
    }
    if (!info.ok) {
      const e = new Error('worker 未就绪：' + JSON.stringify(info).slice(0, 200));
      e.hint = 'worker stderr 尾部：' + String(worker.stderrTail || '').slice(-500);
      try { child.kill('SIGKILL'); } catch (x) {}
      throw e;
    }
    worker.ready = true; worker.gpu = info.ok.gpu || null; worker.pid = info.ok.pid || child.pid;
    metrics.workerStarts++;
    return Object.assign(workerSnapshot(), { hello: info.ok });
  }
  /** 与 worker 的单次请求-应答（每次新建短连接，避免残留状态） */
  function workerCall(req, timeoutMs = 120000) {
    return new Promise((resolve, reject) => {
      const id = Math.floor(Math.random() * 1e9);
      const s = net.connect({ host: '127.0.0.1', port: worker.port });
      let buf = ''; let settled = false;
      const fin = (fn, v) => { if (settled) return; settled = true; try { s.destroy(); } catch (e) {} fn(v); };
      const t = setTimeout(() => fin(reject, new Error('worker 响应超时 ' + timeoutMs + 'ms（长代码会占住 worker；可 op=status 看状态）')), timeoutMs);
      s.once('connect', () => s.write(JSON.stringify(Object.assign({ id: id }, req)) + '\n'));
      s.once('error', (e) => { clearTimeout(t); fin(reject, e); });
      s.on('data', (d) => {
        buf += d.toString('utf8');
        let nl;
        while ((nl = buf.indexOf('\n')) >= 0) {
          const line = buf.slice(0, nl); buf = buf.slice(nl + 1);
          if (!line.trim()) continue;
          let obj = null; try { obj = JSON.parse(line); } catch (e) { continue; }
          if (obj.id !== undefined && obj.id !== null && obj.id !== id) continue;
          clearTimeout(t); fin(resolve, obj); return;
        }
      });
    });
  }
  async function workerExec(code, timeoutMs = 120000, purgePrefixes = null) {
    if (!workerAlive() || !worker.ready) await workerStart({});
    metrics.workerExecs++;
    try { return await workerCall({ op: 'exec', code: String(code || ''), purgePrefixes: purgePrefixes || undefined }, timeoutMs); }
    catch (e) { worker.lastError = String((e && e.message) || e); throw e; }
  }
  async function workerStatus() {
    if (!workerAlive() || !worker.ready) return workerSnapshot();
    try { const r = await workerCall({ op: 'status' }, 15000); return Object.assign(workerSnapshot(), { status: r.status || null }); }
    catch (e) { worker.lastError = String((e && e.message) || e); return Object.assign(workerSnapshot(), { statusError: worker.lastError }); }
  }
  async function workerStop() {
    if (!workerAlive()) { worker.ready = false; return { stopped: false, wasAlive: false }; }
    let bye = null;
    try { bye = await workerCall({ op: 'shutdown' }, 8000); } catch (e) { /* 直接杀 */ }
    await new Promise((r) => setTimeout(r, 300));
    try { if (workerAlive()) worker.child.kill('SIGKILL'); } catch (e) {}
    worker.ready = false;
    return { stopped: true, wasAlive: true, bye: bye };
  }
  /** 事务：snapshot / restore / list / prune / mark / revert / marks / drop / help */
  async function txnCall(op, payload) {
    await ensureTxn();
    const body = 'print("LOOP " + K.dsh_txn_api["dispatch"](' + JSON.stringify(String(op || 'list')) + ', _json.dumps(_json.loads('
      + JSON.stringify(JSON.stringify(payload || {})) + '))))';
    return extractLoop(await addon.send('execute_code', { code: KERNEL_BOOTSTRAP + '\nimport json as _json\n' + body }, 300000));
  }
  /** 配方库：save / list / get / delete / apply / export / import / help */
  async function presetCall(op, payload) {
    await ensurePreset();
    const body = 'print("LOOP " + K.dsh_preset_api["dispatch"](' + JSON.stringify(String(op || 'list')) + ', _json.dumps(_json.loads('
      + JSON.stringify(JSON.stringify(payload || {})) + '))))';
    return extractLoop(await addon.send('execute_code', { code: KERNEL_BOOTSTRAP + '\nimport json as _json\n' + body }, 300000));
  }
  // ---- v0.9.0（Procedura 融合 #15）：轨迹 JSONL ----
  // 每次顶层调用记一行（route / op / ms / ok / 参数摘要）→ 复盘、对照、审计都有底账。
  // 默认不记全量参数（代码可能几 MB）：只记字节数 + 摘要 + 前 300 字符；DSH_TRAJ_FULL=1 记前 8000。
  // DSH_TRAJ=0 关闭。轨迹写盘失败绝不影响主流程（按天一个文件，超 16 MB 自动改名轮转）。
  const TRAJ_DIR = path.join(winToWsl(WIN_TMP), 'trajectory');
  const TRAJ_OFF = process.env.DSH_TRAJ === '0' || process.env.DSH_TRAJ === 'false';
  const TRAJ_FULL = process.env.DSH_TRAJ_FULL === '1';
  const TRAJ_MAX_BYTES = Number(process.env.DSH_TRAJ_MAX_BYTES || 16000000);
  const TRAJ_SKIP = new Set(['metrics', 'status', 'protocol', 'sceneEpoch']);
  function trajShortHash(s) {
    let h = 0;
    for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0;
    return (h >>> 0).toString(16).padStart(8, '0');
  }
  function trajRecord(route, op, args, ms, ok, err) {
    if (TRAJ_OFF) return;
    try {
      fs.mkdirSync(TRAJ_DIR, { recursive: true });
      const d = new Date();
      const key = String(d.getFullYear()) + String(d.getMonth() + 1).padStart(2, '0') + String(d.getDate()).padStart(2, '0');
      const f = path.join(TRAJ_DIR, 'blender_rt-' + key + '.jsonl');
      try {
        const st = fs.statSync(f);
        if (st.size > TRAJ_MAX_BYTES) fs.renameSync(f, path.join(TRAJ_DIR, 'blender_rt-' + key + '-' + Date.now() + '.jsonl'));
      } catch (e) { /* 首次写 */ }
      const s = args === undefined || args === null ? '' : (typeof args === 'string' ? args : JSON.stringify(args));
      fs.appendFileSync(f, JSON.stringify({
        t: d.toISOString(), route: route, op: op === undefined || op === null ? null : String(op),
        ms: ms, ok: !!ok, err: err ? String(err).slice(0, 300) : null,
        args_bytes: s.length, args_digest: trajShortHash(s),
        args_head: s ? s.slice(0, TRAJ_FULL ? 8000 : 300) : null,
      }) + String.fromCharCode(10), 'utf8');
    } catch (e) { /* 轨迹是旁路：出错就丢这一行 */ }
  }
  /** 给返回对象裹一层记录：this 仍指向原对象（内部 this.xxx() 不会重复记账） */
  function withTrajectory(obj) {
    const out = {};
    for (const k of Object.keys(obj)) {
      const v = obj[k];
      if (typeof v === 'function') {
        out[k] = function (...a) {
          const t0 = Date.now();
          const rawOp = a.length ? a[0] : null;
          const opArg = (typeof rawOp === 'string' || typeof rawOp === 'number') ? rawOp : null;
          const argsArg = a.length > 1 ? a[1] : a[0];
          const rec = (ok, err) => { if (!TRAJ_SKIP.has(k)) trajRecord(k, opArg, argsArg, Date.now() - t0, ok, err); };
          let r;
          try { r = v.apply(obj, a); } catch (e) { rec(false, e && e.message); throw e; }
          if (r && typeof r.then === 'function') {
            return r.then((x) => { rec(true, null); return x; },
                          (e) => { rec(false, e && e.message); throw e; });
          }
          rec(true, null);
          return r;
        };
      } else if (v && typeof v === 'object' && !Array.isArray(v)) {
        out[k] = withTrajectory(v);
      } else {
        out[k] = v;
      }
    }
    return out;
  }
  return withTrajectory({
    addon: addon,
    plan: (op, payload) => planCall(op, payload),
    txn: (op, payload) => txnCall(op, payload),
    preset: (op, payload) => presetCall(op, payload),
    worker: { start: workerStart, exec: workerExec, status: workerStatus, stop: workerStop, snapshot: workerSnapshot },
    job: { start: jobStart, status: jobStatus, collect: jobCollect, kill: jobKill, list: jobList, wait: jobWait },
    /** v0.9.4：拉起 GUI Blender + 自动 Connect addon（server.mjs /launch 路由用） */
    launchBlender: (opts) => launchBlender(opts || {}),
    sceneEpoch: sceneEpoch,
    /** 通道指标快照（inflight / last_cmd / 超时计数） */
    metrics() {
      return { ...metrics, lastCmdAgeMs: metrics.lastCmdAt ? Date.now() - metrics.lastCmdAt : null };
    },
    /** 通道实际用的 addon 协议（显式配置 or auto 探测结果） */
    async protocol() { return await protocolInfo(); },
    /** 现场体检：真跑一次 bpy 往返（status），通了才算 ok；不通才去三级判定 */
    async doctor() {
      const t0 = Date.now();
      if (!(await tcpProbe())) {
        const d = await diagnose();
        metrics.lastDiagnosis = d;
        return { ok: false, addon: d, metrics: this.metrics() };
      }
      try {
        const s = await this.status();
        const pingMs = await freshPing().catch(() => null);
        const pinfo = await protocolInfo();
        return {
          ok: true,
          addon: { kind: 'ok', tcp: true, ping: true, ping_ms: pingMs, roundTripMs: Date.now() - t0,
            summary: '通道健康：TCP + ping + bpy 往返都通',
            protocol: pinfo.id, protocol_label: pinfo.label, protocol_from: pinfo.from,
            fix: '无需处理', region: s.region },
          metrics: this.metrics(),
        };
      } catch (e) {
        const d = await diagnose();
        metrics.lastDiagnosis = d;
        return { ok: false, addon: d, metrics: this.metrics(), error: String((e && e.message) || e) };
      }
    },
    /** 3D 视口区域 rect / 窗口尺寸 / 着色模式 */
    async status() {
      // v0.9.3：**本地信息不该被「Blender 没连上」挡住** —— 旧版这里第一句就 throw，
      // 于是 workDir 可写性 / 台账 / 版本自证在没连 addon 时全看不到（恰恰是最需要它们的时候）。
      let region = null, addonError = null;
      try { const out = await addon.send('execute_code', { code: REGION_CODE }); region = jsonFromStdout(out); }
      catch (e) { addonError = String((e && e.message) || e).slice(0, 300); }
      // v0.8.10（A0/D4）：状态里带插件版本 + runtime 指纹 + 最近运行台账
      return { region: region, addonError: addonError,
               plugin: { version: PLUGIN_VERSION, runtimeDir: wslToWin(HERE), runtime: runtimeFingerprint() },
               // v0.9.1（93-E2）：会话名 —— 多会话/多成员并存时，默认产物名与 evidence 路径按它隔离
               session: SESSION_NAME,
               resultsDir: wslToWin(RESULTS_DIR),
               trajectory: { dir: wslToWin(TRAJ_DIR), off: TRAJ_OFF, full: TRAJ_FULL,
                             note: 'v0.9.0（#15）：每次顶层调用一行 JSONL（route/op/ms/ok/参数摘要）；DSH_TRAJ=0 关，DSH_TRAJ_FULL=1 记更多参数' },
               runs: Array.from(runs.values()).slice(-5).map(runSnapshot),
               jobs: Array.from(jobs.values()).slice(-5).map(jobSnapshot),
               // v0.9.3（D4 同源）：台账/工作目录的**可写性**必须自证 —— 旧版写失败是静默的
               ledger: Object.assign({ path: wslToWin(LEDGER), pathWsl: LEDGER,
                                       recent: ledgerList(5).map((x) => ({ id: x.id, kind: x.kind || 'job', status: x.status, startedAt: x.startedAt, ms: x.ms })) },
                                     ledgerState()) };
    },
    /**
     * 执行 Python（持久内核 K）。v0.7.0 起：异常也回传 **partial stdout / stderr / traceback**
     * （用 DSH_ACT_OK / DSH_ACT_ERR 标记包裹；解析不到标记就回落旧行为）。
     * file 给定时改为执行该文件（K.run，支持 WSL / Windows / 相对 .blend 三种路径）。
     */
    async act(code, timeoutMs = 120000, file = null) {
      const src = file ? ('K.run(' + JSON.stringify(String(file)) + ')')
        : (code === undefined || code === null ? '' : String(code));
      const wrapped = KERNEL_BOOTSTRAP + '\n' + ACT_WRAPPER(src);
      const t0 = Date.now();
      const res = await addon.send('execute_code', { code: wrapped }, timeoutMs);
      const raw = (res && typeof res.result === 'string') ? res.result : '';
      const ms = Date.now() - t0;
      const pick = (tag) => { const ls = raw.split('\n').filter((x) => x.indexOf(tag + ' ') === 0); return ls.length ? ls[ls.length - 1] : null; };
      const errLine = pick('DSH_ACT_ERR');
      const okLine = pick('DSH_ACT_OK');
      let p = null;
      try { p = JSON.parse((errLine || okLine).slice((errLine ? 'DSH_ACT_ERR ' : 'DSH_ACT_OK ').length).trim()); }
      catch (e) { p = null; }
      if (p) {
        return { ok: !errLine, executed: true, ms: ms, mainThreadMs: ms,
                 stdout: p.stdout || '', stderr: p.stderr || '',
                 error: p.error ? enrichError(p.error, p.traceback) : null, traceback: p.traceback || null,
                 sceneEpoch: p.epoch || null, file: file || null };
      }
      return { ok: true, executed: true, ms: ms, mainThreadMs: ms, stdout: raw, stderr: '',
               error: null, traceback: null, marker_missing: true, file: file || null };
    },
    /** 通用命令透传：任意 addon 命令名 + 参数（MCP 那层不暴露的也能调） */
    async cmd(name, params = {}, timeoutMs = 120000) {
      if (!name || typeof name !== 'string') throw new Error('cmd: name required');
      return addon.send(name, params || {}, timeoutMs);
    },
    /** 命令清单：目录 + 当前可用性（受 scene 开关与其 API key 影响） */
    async commands() {
      const out = await addon.send('execute_code', { code: FLAGS_CODE });
      const meta = jsonFromStdout(out) || { flags: {}, scene: null, file: null };
      const flags = meta.flags || {};
      const available = [];
      const disabled = [];
      for (const name of Object.keys(COMMAND_CATALOG)) {
        const gate = COMMAND_CATALOG[name].gate;
        if (!gate || flags[gate]) available.push(name);
        else disabled.push(name);
      }
      // 集成"开关已开但仍不可用"的实情（缺 key 等）由各自 status 命令回答
      const integrations = {};
      for (const t of ['get_polyhaven_status', 'get_hyper3d_status', 'get_sketchfab_status', 'get_polypizza_status', 'get_hunyuan3d_status']) {
        try {
          const r = await addon.send(t, {}, 15000);
          integrations[t.replace('get_', '').replace('_status', '')] = { enabled: !!(r && r.enabled), message: String((r && r.message) || '').split('\n')[0].slice(0, 160) };
        } catch (e) {
          integrations[t] = { enabled: false, message: String((e && e.message) || e).slice(0, 120) };
        }
      }
      return {
        scene: meta.scene,
        file: meta.file,
        total: available.length,
        available: available,
        disabled: disabled,
        gates: flags,
        integrations: integrations,
      };
    },
    /** 内环：启动（spec 见 runner.py 顶部契约） */
    async loopStart(spec) {
      await ensureRunner();
      const j = JSON.stringify(spec || {});
      const body = 'import json as _json' + '\n' + 'print("LOOP " + K.dsh_loop_api["start"](_json.loads(' + JSON.stringify(j) + ')))';
      return extractLoop(await addon.send('execute_code', { code: KERNEL_BOOTSTRAP + '\n' + body }, 60000));
    },
    async loopStatus(history = 8) {
      await ensureRunner();
      const body = 'print("LOOP " + K.dsh_loop_api["status"](' + String(Number(history) || 8) + '))';
      return extractLoop(await addon.send('execute_code', { code: KERNEL_BOOTSTRAP + '\n' + body }, 60000));
    },
    async loopStop() {
      await ensureRunner();
      const body = 'print("LOOP " + K.dsh_loop_api["stop"]())';
      return extractLoop(await addon.send('execute_code', { code: KERNEL_BOOTSTRAP + '\n' + body }, 60000));
    },
    async loopBench(iterations = 2000) {
      await ensureRunner();
      const body = 'print("LOOP " + K.dsh_loop_api["bench"](' + String(Number(iterations) || 2000) + '))';
      return extractLoop(await addon.send('execute_code', { code: KERNEL_BOOTSTRAP + '\n' + body }, 60000));
    },
    /** 候选表：top_k / 分组（跨对象批量）结果 */
    async loopBoard(limit = 10, groups = true) {
      await ensureRunner();
      const body = 'print("LOOP " + K.dsh_loop_api["board"](' + String(Number(limit) || 10) + ', ' + (groups === false ? 'False' : 'True') + '))';
      return extractLoop(await addon.send('execute_code', { code: KERNEL_BOOTSTRAP + '\n' + body }, 60000));
    },
    /** 导出可复用脚本（内嵌 setup 源码 + 最优参数） */
    async loopExport(opts = {}) {
      await ensureRunner();
      const pathLit = opts.path ? JSON.stringify(String(opts.path)) : 'None';
      const top = String(Number(opts.top) || 1);
      const variants = opts.includeVariants ? 'True' : 'False';
      const note = JSON.stringify(String(opts.note || ''));
      const body = 'print("LOOP " + K.dsh_loop_api["export"](path=' + pathLit + ', top=' + top + ', include_variants=' + variants + ', note=' + note + '))';
      return extractLoop(await addon.send('execute_code', { code: KERNEL_BOOTSTRAP + '\n' + body }, 120000));
    },
    /** 内环契约速查（spec 字段 + ns 预置 + 辅助函数） */
    async loopHelp() {
      await ensureRunner();
      const body = 'print("LOOP " + K.dsh_loop_api["help"]())';
      return extractLoop(await addon.send('execute_code', { code: KERNEL_BOOTSTRAP + '\n' + body }, 60000));
    },
    /** 整窗口/区域截图（screenshot_area 路径，保留原 CLI 通道的 screenshot_area 能力） */
    async frameArea(areaIndex = 0) {
      const code = [
        'import bpy',
        'p = r"' + LIVE_PNG_WIN + '"',
        'scr = bpy.context.screen',
        'areas = list(scr.areas)',
        'idx = min(max(0, int(' + String(Number(areaIndex) || 0) + ')), len(areas) - 1)',
        'a = areas[idx]',
        'win = next(w for w in bpy.context.window_manager.windows if w.screen == scr)',
        'with bpy.context.temp_override(window=win, area=a):',
        '    bpy.ops.screen.screenshot_area(filepath=p)',
        'print("AREA_SHOT " + a.type + " " + p)',
      ].join('\n');
      const out = await addon.send('execute_code', { code: KERNEL_BOOTSTRAP + '\n' + code }, 60000);
      const txt = (out && typeof out.result === 'string') ? out.result : '';
      if (!txt.includes('AREA_SHOT')) throw new Error('区域截图失败: ' + txt.slice(0, 200));
      const png = fs.readFileSync(LIVE_PNG_WSL);
      return { png: png, meta: { method: 'screenshot_area', info: txt.trim() } };
    },
    /** 渲染性能：status / apply / revert / analyze / help */
    async perf(op, payload) { return perfCall(op, payload); },
    /** 对象精简：opt_analyze / opt_join */
    async opt(op, payload) { return perfCall(op, payload); },
    /** 取一帧视口 PNG（覆盖写固定文件） */
    async frame(maxSize = 560) {
      const res = await addon.send('get_viewport_screenshot', { max_size: maxSize, filepath: LIVE_PNG_WIN, format: 'png' });
      if (res && res.error) throw new Error(res.error);
      const png = fs.readFileSync(LIVE_PNG_WSL);
      return { png: png, meta: res };
    },
    /** 自定义视角捕获：from/look_at/lens/ortho/...（view.py）——不动物体、不动用户视口 */
    async view(spec = {}) {
      await ensureView();
      metrics.views++;
      // 未指定输出路径时用配置里的工作目录（保证 Blender 与宿主两端都能访问）
      const spec2 = Object.assign({}, spec || {});
      if (!spec2.path) spec2.path = VIEW_PNG_WIN;
      const j = JSON.stringify(spec2);
      const body = 'import json as _json' + '\n' + 'print("VIEW " + K.dsh_view_api["capture"](_json.dumps(_json.loads(' + JSON.stringify(j) + '))))';
      const info = extractTag(await addon.send('execute_code', { code: KERNEL_BOOTSTRAP + '\n' + body }, Math.max(30000, Number(spec.timeoutMs) || 120000)), 'VIEW');
      if (!info || info.ok === false) throw new Error('view 失败: ' + JSON.stringify(info).slice(0, 300));
      metrics.lastView = { at: Date.now(), ms: info.ms, mode: info.mode, path: info.path };
      return { png: fs.readFileSync(winToWsl(info.path)), meta: info };
    },
    /**
     * 无头 Blender：独立进程跑脚本（重渲染 / 批量几何 / 校验都走它）。
     * 语义要点（按外部反馈加固）：
     *  - 默认注入 GPU 前导（gpu:'auto'）：无头进程读不到用户偏好，Cycles 会静默回落 CPU（实测 15.4×）
     *  - gpu:'true' 时若确实没有 GPU 后端 → ok=false（不静默）
     *  - use_user_config:true 时透传 BLENDER_USER_CONFIG/SCRIPTS（配合 factory_startup:false 才有意义）
     *  - 全量 stdout/stderr 落盘（logs 路径返回）、抽 last_exception / traceback
     *  - outdir 产物默认过滤 __pycache__ / *.pyc / *.blend1|2 / tmp*（include_noise:true 关闭过滤）
     */
    async headless(opts = {}) {
      const t0 = Date.now();
      // ---- v0.9.3（D4）：outdir 接受 WSL 路径（/home/…、/mnt/d/…）与 Windows 路径。
      // 旧版把入参**原样**塞给 Windows 的 blender.exe → '/home/x' 被当相对盘根，静默写到 C:\home\x。
      // 现在：Node 侧用 outdirWsl 读写、Blender 侧用 outdirWin（/home/… → \\wsl.localhost\<distro>\home\…）。
      const outdirWsl = opts.outdir ? winToWsl(String(opts.outdir)) : null;
      const outdirWin = opts.outdir ? wslToWin(String(opts.outdir)) : WIN_TMP;
      // v0.9.1（93-B3）：scriptFile= 显式收 .py；file= 传 .py 时自动改当脚本（老语义 file= 仍是 .blend）
      let scriptFileInfo = null;
      let script = opts.script ? String(opts.script) : '';
      const fileLooksPython = !!(opts.file && /\.py$/i.test(String(opts.file)));
      if (opts.scriptFile || fileLooksPython) {
        const given = String(opts.scriptFile || opts.file);
        try {
          const rp = readScriptPath(given);
          script = fs.readFileSync(rp.wsl, 'utf8');
          scriptFileInfo = { given: given, resolved: rp.win, bytes: script.length,
                             auto_from_file: !opts.scriptFile && fileLooksPython };
        } catch (e) {
          throw Object.assign(new Error('读不到脚本文件：' + given + ' —— ' + String((e && e.message) || e).slice(0, 160)),
                              { hint: 'scriptFile 用 .py 路径（Windows D:\\… 或 WSL /home/… 都行）；file= 是 .blend' });
        }
      }
      // ---- v0.9.3（F6）：inputFile 指纹 —— 探针跑在 02:55 版、04:07 才发现文件已被重存（浪费 4 次探针）
      // （必须在 fileLooksPython 之后：否则 TDZ ReferenceError —— 实测踩过）
      const inputFileInfo = (opts.file && !fileLooksPython)
        ? fileFingerprint(wslToWin(String(opts.file)), winToWsl(String(opts.file))) : null;
      // preload: 把 runtime 里的 python 模块（view/perf/runner/contract/planner...）源码拼进脚本开头
      const mods = Array.isArray(opts.preload) ? opts.preload : (opts.preload ? String(opts.preload).split(',') : []);
      // ---- v0.9.3（F1）：shots=[…] 多视角渲染一体化 —— 自动带上 qc_render（渲染 harness 就在里面）
      const shotsSpec = Array.isArray(opts.shots) ? { views: opts.shots }
        : (opts.shots && typeof opts.shots === 'object' ? Object.assign({}, opts.shots) : null);
      const shotsWanted = !!(shotsSpec && Array.isArray(shotsSpec.views) && shotsSpec.views.length);
      if (shotsWanted && !mods.some((m) => String(m).trim().replace(/\.py$/, '') === 'qc_render')) mods.push('qc_render');
      let pre = '';
      for (const m of mods) {
        const name = String(m).trim().replace(/\.py$/, '');
        if (!name) continue;
        const f = path.join(HERE, name + '.py');
        if (!fs.existsSync(f)) throw new Error('preload 找不到模块：' + f);
        pre += preloadChunk(name) + '\n';
      }
      // ---- 引擎前导（v0.8.0）：默认 eevee + 光追；可选 cycles / keep（向后兼容 gpu:"false"=keep）
      const engineMode = String(opts.engine === undefined || opts.engine === null ? 'eevee' : opts.engine).toLowerCase();
      const legacyGpu = String(opts.gpu === undefined || opts.gpu === null ? '' : opts.gpu).toLowerCase();
      const gpuOff = (engineMode === 'keep' || engineMode === 'off' || engineMode === 'none'
        || legacyGpu === 'false' || legacyGpu === 'off' || legacyGpu === '0');
      const gpuManual = (legacyGpu === 'true' || legacyGpu === 'required');
      let gpuPre = '';
      if (!gpuOff) {
        const modeLit = (engineMode === 'cycles' || engineMode === 'cycle') ? 'cycles' : 'eevee';
        gpuPre = '# ---- DSH engine prelude (' + modeLit + ') ----\n' + GPU_PRELUDE
          .replace(/__DSH_ENGINE__/g, "'" + modeLit + "'")
          .replace(/__DSH_GPU_MANUAL__/g, gpuManual ? 'True' : 'False') + '\n';
      }
      const headParts = [];
      // ---- v0.9.1（93-B4）：env 注入必须在 K 内核之前 —— PATH_HELPERS 里的 K.args/K.run_id 是从 os.environ 读的，
      // 而 WSL→Windows 的 env 跨界不可靠（实测 DSH_* 会被吃掉）→ 直接在脚本里 setdefault 一遍。
      // v0.9.3（D1）：**runId 先定**（客户端可以自带一个 id）—— 客户端等待窗口到点时要能立刻报出
      // "用这个 id 去 collect"，所以 id 必须在发请求前就存在，不能等服务端生成完再回传。
      const runId = /^[A-Za-z0-9_-]{1,64}$/.test(String(opts.runId || '')) ? String(opts.runId)
        : ('run-' + Date.now().toString(36) + Math.random().toString(36).slice(2, 5));
      const envPairs = {
        DSH_RUN_ID: runId, DSH_OUTDIR: outdirWin, DSH_SESSION: SESSION_NAME,
        DSH_PLUGIN_VERSION: PLUGIN_VERSION,
        DSH_ARGS: JSON.stringify(Array.isArray(opts.args) ? opts.args.map(String) : []),
      };
      if (opts.env && typeof opts.env === 'object') for (const k of Object.keys(opts.env)) envPairs[String(k)] = String(opts.env[k]);
      headParts.push(envPrelude(envPairs));
      if (opts.bootstrap !== false) headParts.push(KERNEL_BOOTSTRAP);
      headParts.push('_dsh_stage_emit("engine-prelude")');
      // D4.3 基线：记下进入脚本前的 render.filepath（--factory-startup 的默认值本身就是 '/tmp\\' 这类
      // POSIX 串）—— 只有**脚本把它改成了 POSIX 绝对路径**才值得报警，否则全是假阳性。
      headParts.push(['try:', '    _DSH_FP0 = bpy.context.scene.render.filepath', 'except Exception:', '    _DSH_FP0 = None'].join(String.fromCharCode(10)));
      if (gpuPre) headParts.push(gpuPre);
      // v0.8.10（D2）：workdir → 脚本内 chdir + sys.path 首位（Blender 是 Windows 进程，必须过 K.win_path）
      if (opts.workdir) headParts.push(workdirBlock(opts.workdir));
      // v0.8.10（B2）：expect 前后哨兵 —— "build 返回空却不抛异常"必须被判失败
      const exp = (opts.expect && typeof opts.expect === 'object') ? opts.expect : null;
      const expectPre = exp ? ['# ---- DSH expect: before ----', 'import json as _dsh_ejson',
        '_DSH_EXP0 = {"objects": len(bpy.data.objects), "meshes": len(bpy.data.meshes), "materials": len(bpy.data.materials)}'].join(String.fromCharCode(10)) : '';
      const expectPost = exp ? ['# ---- DSH expect: after ----', 'try:',
        '    _DSH_EXP1 = {"objects": len(bpy.data.objects), "meshes": len(bpy.data.meshes), "materials": len(bpy.data.materials)}',
        '    print("DSH_EXPECT " + _dsh_ejson.dumps({"before": _DSH_EXP0, "after": _DSH_EXP1, "delta": {k: _DSH_EXP1[k] - _DSH_EXP0[k] for k in _DSH_EXP1}}))',
        'except Exception as _e:', '    print("DSH_EXPECT " + _dsh_ejson.dumps({"error": str(_e)[:160]}))'].join(String.fromCharCode(10)) : '';
      const NL = String.fromCharCode(10);
      // v0.9.3（F1）：shots 走 qc_render_views —— outdir 用 Windows 形态（Blender 侧），其余键由调用方定
      const shotsPre = shotsWanted
        ? shotsBlock(Object.assign({ engine: 'keep', lock: true, warmup: true, tag: 'shot' }, shotsSpec, { outdir: outdirWin })) + NL
        : '';
      const body = (script || pre || gpuPre || shotsPre)
        ? (headParts.length ? headParts.join(NL) + NL : '')
          + (expectPre ? expectPre + NL : '')
          + pre
          + '_dsh_stage_emit("preload-done")' + NL
          + script
          + NL + '_dsh_stage_emit("script-end")' + NL
          + shotsPre
          + PATH_GUARD + NL
          + (expectPost ? expectPost : '')
          + NL + '_dsh_stage_emit("done")' + NL
        : '';
      const args = ['-b'];
      if (opts.file && !fileLooksPython) args.push(wslToWin(String(opts.file)));
      if (opts.factoryStartup !== false) args.push('--factory-startup');
      let sp = null;
      if (body) { sp = writeHeadlessScript(body); args.push('--python', sp.win); }
      args.push('--');
      if (opts.outdir) args.push(outdirWin);
      if (Array.isArray(opts.args)) args.push.apply(args, opts.args.map(String));
      const timeoutMs = Math.max(1000, Math.min(1800000, Number(opts.timeoutMs) || 180000));
      metrics.headlessRuns++;
      // ---- v0.9.3（D1/D5）：日志**边跑边写** + 运行台账登记（id 与作业同空间：op=status/collect id=run-…）
      const logDirWsl = outdirWsl || winToWsl(WIN_TMP);
      const stamp = new Date(t0).toISOString().replace(/[:.]/g, '-');
      let logs = null, soStream = null, seStream = null;
      try {
        fs.mkdirSync(logDirWsl, { recursive: true });
        const soPath = path.join(logDirWsl, 'dsh_headless_' + stamp + '.stdout.log');
        const sePath = path.join(logDirWsl, 'dsh_headless_' + stamp + '.stderr.log');
        soStream = fs.createWriteStream(soPath); seStream = fs.createWriteStream(sePath);
        logs = { stdout: wslToWin(soPath), stderr: wslToWin(sePath), stdoutWsl: soPath, stderrWsl: sePath };
      } catch (e) { logs = null; }
      const runRec = { id: runId, child: null, pid: null, startedAt: t0, status: 'running',
                       outdir: outdirWin, outdirWsl: outdirWsl, logs: logs, script: sp ? sp.win : null,
                       exitCode: null, timedOut: false, artifacts: [], expect: exp, workdir: opts.workdir || null,
                       stage: null, stageAt: null, stageName: null, lastOutputAt: t0, lines: 0,
                       result: null, resultPath: null, resultBytes: 0, resultTruncated: false, pathWarnings: [] };
      runs.set(runId, runRec);
      if (runs.size > 20) { const k = runs.keys().next().value; if (k !== runId) runs.delete(k); }
      ledgerAppend({ id: runId, kind: 'headless', status: 'running', startedAt: t0, script: runRec.script,
                     outdir: runRec.outdir, timeoutMs: timeoutMs, stage: null });
      // v0.8.7（外部反馈 #4 P0-2）/ v0.9.3（D1）：长任务转作业层 —— 客户端超时不再把结果丢掉。
      // 用法：as_job=true 强制转；或给 auto_job_ms（如 120000）表示预计超过它就走作业。
      const autoJobMs = Number(opts.autoJobMs) || 0;
      if (opts.asJob || (autoJobMs > 0 && timeoutMs >= autoJobMs)) {
        runs.delete(runId);                       // 转作业后这次 run 不存在了，别留一个假 running
        const j = jobStart(Object.assign({}, opts, { timeoutMs: Math.max(timeoutMs, 3600000), runId: runId }));
        return { ok: true, mode: 'job', kind: 'job', ms: Date.now() - t0, jobId: j.id, runId: runId, job: j,
                 logs: { stdout: j.stdoutLog, stderr: j.stderrLog },
                 reason: null,
                 hint: '已转作业层（长任务）：用 blender_rt_job(op="status"/"collect"/"wait", id="' + j.id + '") 跟进' };
      }
      // ---- 子进程环境：可选透传用户 Blender 配置（GPU 偏好在里面）
      // v0.9.1（93-B4）：显式 env 入参 + 插件注入的契约变量 —— 参数不必再挤 argv（DSH_ARGS/DSH_OUTDIR/DSH_RUN_ID）
      // v0.9.3（D5）：三处 spawn 统一 baseChildEnv()（PYTHONIOENCODING + **PYTHONUNBUFFERED=1**）
      const childEnv = baseChildEnv({
        DSH_RUN_ID: runId,
        DSH_OUTDIR: outdirWin,
        DSH_ARGS: JSON.stringify(Array.isArray(opts.args) ? opts.args.map(String) : []),
        DSH_SESSION: SESSION_NAME,
        DSH_PLUGIN_VERSION: PLUGIN_VERSION,
      });
      if (opts.env && typeof opts.env === 'object') {
        for (const k of Object.keys(opts.env)) childEnv[String(k)] = String(opts.env[k]);
      }
      // ⚠ 实测坑（v0.9.1）：从 WSL 侧 spawn Windows 的 blender.exe 时，**env 不会自动跨界** ——
      // Node 把 DSH_* 传给了 spawn，但子进程 os.environ 里一个都看不到（要靠 WSLENV 声明）。
      // 这里自动声明；同时脚本里还会再注入一次（双保险，见 DSH_ENV_PRELUDE）。
      withWslEnv(childEnv, opts.env);
      if (opts.useUserConfig) {
        if (USER_CONFIG_WIN) childEnv.BLENDER_USER_CONFIG = USER_CONFIG_WIN;
        if (USER_SCRIPTS_WIN) childEnv.BLENDER_USER_SCRIPTS = USER_SCRIPTS_WIN;
      }
      const res = await new Promise((resolve) => {
        let so = '', se = '', exitCode = null, signal = null, spawnErr = null, timedOut = false;
        const CAP = 262144;
        let child = null;
        try {
          child = spawn(BLENDER_EXE, args, { env: childEnv, stdio: ['ignore', 'pipe', 'pipe'] });
          runRec.child = child; runRec.pid = child.pid;
        } catch (e) {
          return resolve({ spawnErr: e });
        }
        // v0.9.3（D5）：stage 心跳扫描 + 增量落盘（run 运行期间 op=status.stage / 日志就有内容）
        const scanOut = makeStageScanner(runRec), scanErr = makeStageScanner(runRec);
        const push = (buf, which) => {
          const t = buf.toString('utf8');
          if (which === 'o') {
            so += t; if (so.length > CAP) so = so.slice(-CAP);
            scanOut(t);
            try { if (soStream) soStream.write(t); } catch (e) { /* ignore */ }
          } else {
            se += t; if (se.length > CAP) se = se.slice(-CAP);
            scanErr(t);
            try { if (seStream) seStream.write(t); } catch (e) { /* ignore */ }
          }
        };
        child.stdout.on('data', (d) => push(d, 'o'));
        child.stderr.on('data', (d) => push(d, 'e'));
        child.on('error', (e) => { spawnErr = e; });
        const timer = setTimeout(() => { timedOut = true; try { child.kill('SIGKILL'); } catch (e) {} }, timeoutMs);
        child.on('close', (code, sig) => { clearTimeout(timer); exitCode = code; signal = sig; resolve({ so: so, se: se, exitCode: exitCode, signal: signal, spawnErr: spawnErr, timedOut: timedOut }); });
      });
      try { if (soStream) soStream.end(); if (seStream) seStream.end(); } catch (e) { /* ignore */ }
      const ms = Date.now() - t0;
      metrics.lastHeadlessMs = ms;
      if (res.spawnErr) {
        const msg = '起不来 Blender 进程（' + BLENDER_EXE + '）：' + String((res.spawnErr && res.spawnErr.message) || res.spawnErr);
        const e = new Error(msg); e.code = 'blender-exe-missing';
        e.hint = '自动探测没找到 blender 可执行文件。三种配置方式（任选一种）：'
          + '① 环境变量 DSH_BLENDER_EXE=' + (IS_WIN ? 'D:\\...\\blender.exe' : (IS_MAC ? '/Applications/Blender.app/Contents/MacOS/Blender' : '/mnt/d/.../blender.exe'))
          + ' ② 包根写 dsh-blender.config.json: {"blenderExe":"..."}'
          + ' ③ ~/.dsh/dsh-blender.config.json。当前探测结果见 blender_viewport op=doctor 的 config 字段。';
        throw e;
      }
      const stdout = String(res.so || '');
      const stderr = String(res.se || '');
      // ---- 结果契约：HEADLESS <单行 JSON>（取最后一条）
      // v0.9.3（D3）：末行不是合法 JSON 时**不再覆盖 raw**（旧版回 {_parse_error, raw} 顶掉真实输出，
      // 调用方只能看到 "_parse_error: Unexpected token 'O'"）。现在 parsed 保持 null，解析失败单独记在
      // resultParseError 里，stdout 原文（stdoutTail / logs）原样保留。
      let parsed = null, parseError = null;
      const lines = stdout.split('\n').filter((l) => l.indexOf('HEADLESS ') === 0);
      if (lines.length) {
        const lastHead = lines[lines.length - 1].slice('HEADLESS '.length).trim();
        try { parsed = JSON.parse(lastHead); }
        catch (e) { parseError = { message: String((e && e.message) || e).slice(0, 200), line: lastHead.slice(0, 400) }; }
      }
      // ---- v0.9.3（F1）：shots 回执（DSH_SHOTS <单行 JSON>）
      let shotsRes = null;
      const shotsLines = stdout.split('\n').filter((l) => l.indexOf('DSH_SHOTS ') === 0);
      if (shotsLines.length) {
        try { shotsRes = JSON.parse(shotsLines[shotsLines.length - 1].slice('DSH_SHOTS '.length).trim()); }
        catch (e) { shotsRes = { ok: false, parse_error: String((e && e.message) || e).slice(0, 200) }; }
      }
      // ---- v0.9.3（D4.3）：路径静默改写体检
      const pathWarnings = pathAudit(stdout, stderr);
      // ---- GPU 回执（前导打印 DSH_GPU）
      let gpu = null;
      const gl = stdout.split('\n').filter((l) => l.indexOf('DSH_GPU ') === 0);
      if (gl.length) { try { gpu = JSON.parse(gl[gl.length - 1].slice('DSH_GPU '.length).trim()); } catch (e) { gpu = { _parse_error: String((e && e.message) || e) }; } }
      // ---- 日志兜底：正常路径已在 push() 里边跑边写（D5）；流没建起来时这里补一次全量
      if (!soStream) {
        try {
          fs.mkdirSync(logDirWsl, { recursive: true });
          const soPath = path.join(logDirWsl, 'dsh_headless_' + stamp + '.stdout.log');
          const sePath = path.join(logDirWsl, 'dsh_headless_' + stamp + '.stderr.log');
          fs.writeFileSync(soPath, stdout, 'utf8');
          fs.writeFileSync(sePath, stderr, 'utf8');
          logs = { stdout: wslToWin(soPath), stderr: wslToWin(sePath), stdoutWsl: soPath, stderrWsl: sePath };
        } catch (e) { logs = null; }
      }
      runRec.logs = logs;
      // ---- 失败结构化：traceback 段 + 最后一条异常行 + 转义坑提示
      let traceback = null, lastException = null, hint = null;
      const tbIdx = stderr.lastIndexOf('Traceback (most recent call last)');
      if (tbIdx >= 0) traceback = stderr.slice(tbIdx, tbIdx + 3000);
      const em = stderr.match(/^[\w.]*(?:Error|Exception|Warning)\b.*$/gm);
      if (em && em.length) lastException = enrichError(em[em.length - 1].slice(0, 300), traceback);
      if (lastException && /line continuation character|invalid syntax/i.test(lastException) && /\\n/.test(script)) {
        hint = '脚本里出现字面量 \\n（两个字符）而不是真正换行 —— 在 JSON/TS 里请写成 \\n，或用 String.fromCharCode(10) 拼。';
      }
      // ---- 产物清单：默认过滤噪音
      const isNoise = (n) => n.indexOf('__pycache__') === 0 || /\.pyc$/.test(n) || /\.blend[12]$/.test(n) || n.indexOf('tmp') === 0 || n === '.DS_Store';
      let artifacts = [];
      let noiseFiltered = 0;
      if (outdirWsl) {
        try {
          const all = fs.readdirSync(outdirWsl)
            .map((n) => { const p = path.join(outdirWsl, n); let st = null; try { st = fs.statSync(p); } catch (e) {} return st && st.isFile() ? { name: n, bytes: st.size, mtimeMs: st.mtimeMs, fresh: st.mtimeMs >= t0 - 2000, noise: isNoise(n) } : null; })
            .filter(Boolean);
          noiseFiltered = all.filter((a) => a.noise).length;
          artifacts = (opts.includeNoise ? all : all.filter((a) => !a.noise))
            .sort((a, b) => b.mtimeMs - a.mtimeMs).slice(0, 40)
            .map((a) => ({ name: a.name, bytes: a.bytes, mtimeMs: a.mtimeMs, fresh: a.fresh }));
        } catch (e) { artifacts = []; }
      }
      const gpuFailed = gpuManual && gpu && gpu.ok === false;
      // ---- v0.8.10（B2）：expect 判定（"0 对象却 ok=true"这类静默空产出必须失败）
      let expectEval = null;
      if (exp) {
        let observed = null;
        const el = stdout.split('\n').filter((l) => l.indexOf('DSH_EXPECT ') === 0);
        if (el.length) { try { observed = JSON.parse(el[el.length - 1].slice('DSH_EXPECT '.length).trim()); } catch (e) { observed = null; } }
        expectEval = { spec: exp, observed: observed, ok: true, warnings: [] };
        const minNew = Number(exp.minNewObjects !== undefined ? exp.minNewObjects : exp.min_new_objects);
        if (!observed || observed.error) {
          expectEval.ok = false;
          expectEval.warnings.push('expect 观测失败：' + ((observed && observed.error) || '没有 DSH_EXPECT 行'));
        } else if (Number.isFinite(minNew) && Number(observed.delta && observed.delta.objects || 0) < minNew) {
          expectEval.ok = false;
          expectEval.warnings.push('对象数未达预期：before=' + observed.before.objects + ' → after=' + observed.after.objects +
                                   '（要求新增 ≥' + minNew + '）—— 典型的 build 空跑');
        }
      }
      const runOk = !res.timedOut && res.exitCode === 0 && !gpuFailed && (!expectEval || expectEval.ok);
      // ---- v0.9.1（93-A4）：失败语义分档 —— "客户端超时"不等于"任务失败"，且要能按 runId 回收
      const failureClass = res.spawnErr ? 'blender_exe_missing'
        : res.timedOut ? 'timeout'
          : gpuFailed ? 'gpu_required_missing'
            : (res.exitCode !== 0 ? 'blender_error' : (lastException ? 'script_error' : 'finished'));
      const FAIL_HINT = {
        finished: null,
        script_error: '脚本抛异常了（退出码可能仍是 0）—— 看 traceback/lastException 指的那一行；结构化结果可能不存在',
        blender_error: 'Blender 进程退出码非 0（崩溃/参数错）—— 看 stderr 尾巴与日志路径',
        timeout: '超时被 SIGKILL：脚本没跑完。改小批量、或 asJob=true 转作业层（不占客户端连接）',
        gpu_required_missing: '要求 GPU 但设备不可用（gpu="true"/engine="cycles" 时）—— 见 gpu 段回执',
        blender_exe_missing: '起不来 Blender 进程：先配 blenderExe / DSH_BLENDER_EXE',
      }[failureClass] || null;
      // ---- v0.9.3（F1/F2）：shots 行 + 取景自诊断（主体占画面 <5% → warning）
      let shotsSummary = null;
      if (shotsRes) {
        const rowsIn = Array.isArray(shotsRes.views) ? shotsRes.views : [];
        const rows = rowsIn.map((e) => {
          const cov = (e && e.frame && typeof e.frame.coverage === 'number') ? e.frame.coverage : null;
          const row = { name: (e && e.view) || null, path: (e && e.path) || null, ms: (e && e.ms) || null,
                        bytes: (e && e.bytes) || null, md5: (e && e.hash) || null, res: (e && e.res) || null,
                        device: (e && e.device) || null, coverage_estimate: cov };
          if (cov !== null && cov < 0.05) {
            row.warning = { code: 'subject_too_small', coverage_estimate: cov,
              hint: '主体只占画面 ' + (cov * 100).toFixed(2) + '% —— 这个机位看不清细节：from 拉近 / 换 look_at / '
                  + '给这一项加 targets=[名字] 或 focus="x,y,z,r" 缩小取景范围（rt_see 同类判据）' };
          }
          return row;
        });
        const covs = rows.map((r) => r.coverage_estimate).filter((x) => typeof x === 'number');
        shotsSummary = { ok: !!shotsRes.ok, count: rows.length, rows: rows,
                         coverage_min: covs.length ? Math.min.apply(null, covs) : null,
                         outdir: shotsRes.outdir || null, jsonl: shotsRes.jsonl || null,
                         total_ms: shotsRes.total_ms || null, render_ms: shotsRes.render_ms || null,
                         engine: shotsRes.engine || null, samples: shotsRes.samples || null,
                         device_line: shotsRes.device_line || null,
                         unknown_views: shotsRes.unknown_views || null,
                         scope_warnings: shotsRes.scope_warnings || null,
                         error: shotsRes.error || (shotsRes.ok === false ? (shotsRes.hint || 'shots 失败（见 error/traceback）') : null) };
        if (parsed === null) {
          parsed = { ok: !!shotsRes.ok, shots: rows, outdir: shotsSummary.outdir, jsonl: shotsSummary.jsonl,
                     count: rows.length, coverage_min: shotsSummary.coverage_min, error: shotsSummary.error };
        }
      }
      // ---- v0.9.1（93-A3）：结构化结果落盘（>4KB 自动落，或 outJson= 指定路径）—— 不必再从 stdout 里 indexOf 切片
      let outJsonInfo = { path: null, bytes: 0, wsl: null };
      try {
        if (parsed !== null && parsed !== undefined) {
          const s = JSON.stringify(parsed);
          outJsonInfo.bytes = s.length;
          if (opts.outJson) {
            const t = readScriptPath(String(opts.outJson));
            fs.mkdirSync(path.dirname(t.wsl), { recursive: true });
            fs.writeFileSync(t.wsl, s, 'utf8');
            outJsonInfo.path = t.win; outJsonInfo.wsl = t.wsl;      // D4.2：Windows 形态 + WSL 可见形态各一份
          } else if (s.length > 4000) {
            outJsonInfo = Object.assign(outJsonInfo, dumpResult(parsed, runId));
            outJsonInfo.wsl = outJsonInfo.path ? winToWsl(outJsonInfo.path) : null;   // D4.2：两种形态都给
          }
        }
      } catch (e) { /* 落盘失败不影响主结果 */ }
      // ---- v0.8.10（A2/D4）：台账终态
      // v0.9.3（D2）：结构化回执的"真值"落在 runRec 上 —— op=collect/status 与同步回执同一份数据
      const resultTruncated = !!(parsed !== null && outJsonInfo.path && outJsonInfo.bytes > 4000);
      const stdoutTail = stdout.length > 4000 ? stdout.slice(-4000) : stdout;
      const stderrTail = stderr.length > 2000 ? stderr.slice(-2000) : stderr;
      try {
        runRec.status = res.timedOut ? 'killed' : (res.exitCode === 0 ? 'done' : 'failed');
        runRec.failureClass = failureClass;
        runRec.exitCode = res.exitCode; runRec.signal = res.signal; runRec.timedOut = !!res.timedOut; runRec.finishedAt = Date.now();
        runRec.ms = runRec.finishedAt - runRec.startedAt;
        runRec.artifacts = artifacts.slice(0, 20).map((a) => a.name);
        runRec.logs = logs; runRec.expect = expectEval;
        runRec.result = parsed; runRec.parseError = parseError;
        runRec.resultPath = outJsonInfo.path; runRec.resultPathWsl = outJsonInfo.wsl || null;
        runRec.resultBytes = outJsonInfo.bytes; runRec.resultTruncated = resultTruncated;
        runRec.pathWarnings = pathWarnings; runRec.shots = shotsSummary;
        runRec.inputFile = inputFileInfo;
        ledgerAppend({ id: runId, kind: 'headless', status: runRec.status, exitCode: res.exitCode,
                       ms: runRec.ms, outdir: runRec.outdir, script: runRec.script, pid: runRec.pid,
                       stage: runRec.stageName || null, resultPath: outJsonInfo.path,
                       stageMs: runRec.stageAt ? (runRec.stageAt - runRec.startedAt) : null,
                       artifacts: runRec.artifacts.slice(0, 8), expectOk: (expectEval ? expectEval.ok : null) });
      } catch (e) { /* ignore */ }
      return { ok: runOk, kind: 'headless', runId: runId, pluginVersion: PLUGIN_VERSION, expect: expectEval,
               stdoutTruncated: stdout.length > 8000,
        status: failureClass, resumable: true, session: SESSION_NAME,
        failure_hint: FAIL_HINT,
        how_to_recover: '客户端超时/断连不代表失败：这是独立子进程。用 blender_rt_job(op="status"|"collect"|"wait", id="' + runId +
                        '") 按 runId 回收结果与产物（台账：' + wslToWin(LEDGER) + '）',
        scriptFile: scriptFileInfo,
        inputFile: inputFileInfo,                                  // v0.9.3（F6）
        outJson: outJsonInfo.path, outJsonWsl: outJsonInfo.wsl || null,
        resultBytes: outJsonInfo.bytes,
        resultPath: outJsonInfo.path, resultPathWsl: outJsonInfo.wsl || null,
        resultTruncated: resultTruncated,
        resultJson: parsed, resultParseError: parseError,          // v0.9.3（D2/D3）
        stdoutTail: stdoutTail, stderrTail: stderrTail,
        stage: runRec.stage, stageName: runRec.stageName, stageAt: runRec.stageAt,
        lastOutputAt: runRec.lastOutputAt, lines: runRec.lines,
        shots: shotsSummary, pathWarnings: pathWarnings,           // v0.9.3（F1/F2/D4.3）
        outdir: { win: outdirWin, wsl: outdirWsl },
        childEnvKeys: Object.keys(childEnv).filter((k) => k.indexOf('DSH_') === 0),
        exitCode: res.exitCode, signal: res.signal, timedOut: !!res.timedOut,
        ms: ms, timeoutMs: timeoutMs, blender: BLENDER_EXE, script: sp ? sp.win : null, args: args,
        preload: mods.length ? mods : undefined, gpu: gpu, engine: (gpu && gpu.after && gpu.after.engine) || null,
        engineMode: (gpu && gpu.mode) || null, useUserConfig: !!opts.useUserConfig,
        logs: logs, lastException: lastException, traceback: traceback, hint: hint,
        noiseFiltered: noiseFiltered,
        reason: res.timedOut ? ('killed after timeout ' + timeoutMs + 'ms')
          : (gpuFailed ? 'gpu required but unavailable' : (res.exitCode === 0 ? null : 'exit code ' + String(res.exitCode))),
        result: parsed, stdout: clipMiddle(stdout, 4000, 4000), stderr: clipMiddle(stderr, 2000, 2000), artifacts: artifacts };
    },
    async start() { await addon.ensure(); return true; },
    stop() { addon.close(); try { if (workerAlive()) worker.child.kill('SIGKILL'); } catch (e) {} },
  });
}
