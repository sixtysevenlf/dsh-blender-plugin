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
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { CFG, PATHS, winToWsl, wslToWin, describeConfig, IS_WIN, PKG_ROOT } from './config.mjs';
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
/** Blender 侧 python 模块：相对本文件定位 → 整个 runtime 目录可以搬到任意位置 */
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
'    """任何路径 → Windows 侧可用形式（/mnt/d/x → D:/x 的 Windows 形式；WSL 内部 → UNC；相对 → 相对 .blend）"""',
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
'    """Windows / UNC 路径 → WSL 侧可用形式（D:/x → /mnt/d/x；UNC → /...）"""',
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
'K.dsh_distro = __DISTRO__',
'K.win_path = _dsh_win_path',
'K.wsl_path = _dsh_wsl_path',
'K.blend_path = _dsh_blend_path',
'K.run = _dsh_run',
'K.stage = _dsh_stage',
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
const PATH_HELPERS = PATH_HELPERS_TEMPLATE
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

function writeHeadlessScript(code) {
  const name = 'dsh_headless_' + Date.now().toString(36) + '.py';
  const cands = [
    { dir: WSL_TMP, win: path.win32.join(WIN_TMP, name) },
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
      fix: "先启动 Blender，再在 3D 视图按 N 打开侧栏 → " + panel + "（服务监听 127.0.0.1:" + String(CFG.addonPort) + "）",
      hint: "TCP " + CFG.addonHost + ":" + String(CFG.addonPort) + " 不通：Blender 没在运行，或 addon 面板没连上（N → " + panel + "）" };
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
      if (!p.view.path) p.view.path = path.win32.join(WIN_TMP, 'dsh_evidence_' + SESSION_NAME.replace(/[^A-Za-z0-9_.-]/g, '_') + '.png');
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
                 jsonl: path.win32.join(String(p.outdir || WIN_TMP), 'render_views.jsonl'), job: j,
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
    return { id: j.id, status: j.status, pid: j.pid, ms: (j.finishedAt || Date.now()) - j.startedAt, timeoutMs: j.timeoutMs,
             outdir: j.outdir || WIN_TMP, logDir: wslToWin(j.dir),
             stdoutLog: wslToWin(path.join(j.dir, 'stdout.log')), stderrLog: wslToWin(path.join(j.dir, 'stderr.log')),
             exitCode: j.exitCode, signal: j.signal, engine: j.engineMode,
             result: j.parsed || null, lastException: j.lastException || null,
             artifacts: j.status === 'running' ? [] : jobArtifacts(j) };
  }
  function jobStart(opts = {}) {
    const id = 'job-' + Date.now().toString(36) + Math.random().toString(36).slice(2, 5);
    const dir = jobDir(id, opts.outdir);
    const outPath = path.join(dir, 'stdout.log'), errPath = path.join(dir, 'stderr.log');
    const engineMode = String(opts.engine === undefined || opts.engine === null ? 'eevee' : opts.engine).toLowerCase();
    const args = ['-b'];
    if (opts.file) args.push(wslToWin(String(opts.file)));
    if (opts.factoryStartup !== false) args.push('--factory-startup');
    const parts = [];
    if (opts.bootstrap !== false) parts.push(KERNEL_BOOTSTRAP);
    if (engineMode !== 'keep') parts.push(GPU_PRELUDE.replace(/__DSH_ENGINE__/g, "'" + (engineMode === 'cycles' ? 'cycles' : 'eevee') + "'").replace(/__DSH_GPU_MANUAL__/g, 'False'));
    // v0.8.9：作业层也支持 preload（与 headless 同一套：把 runtime/<name>.py 源码拼到脚本开头）
    const jobMods = Array.isArray(opts.preload) ? opts.preload : (opts.preload ? String(opts.preload).split(',') : []);
    for (const m of jobMods) {
      const name = String(m).trim().replace(/\.py$/, '');
      if (!name) continue;
      const f = path.join(HERE, name + '.py');
      if (!fs.existsSync(f)) throw new Error('preload 找不到模块：' + f);
      parts.push(preloadChunk(name));
    }
    parts.push(String(opts.script || ''));
    const sp = writeHeadlessScript(parts.join('\n'));
    args.push('--python', sp.win, '--');
    if (opts.outdir) args.push(String(opts.outdir));
    if (Array.isArray(opts.args)) args.push.apply(args, opts.args.map(String));
    const timeoutMs = Math.max(1000, Math.min(86400000, Number(opts.timeoutMs) || 3600000));
    const so = fs.createWriteStream(outPath), se = fs.createWriteStream(errPath);
    const child = spawn(BLENDER_EXE, args, { env: Object.assign({}, process.env, { PYTHONIOENCODING: 'utf-8' }), stdio: ['ignore', 'pipe', 'pipe'], detached: false });
    child.stdout.pipe(so); child.stderr.pipe(se);
    const j = { id, child, pid: child.pid, startedAt: Date.now(), status: 'running', exitCode: null, signal: null,
                timeoutMs, outdir: opts.outdir || null, dir, engineMode, parsed: null, lastException: null, script: sp.win, args };
    jobs.set(id, j); if (jobs.size > 20) { const k = jobs.keys().next().value; if (k !== id) jobs.delete(k); }
    j.startedAt = j.startedAt || Date.now();
    ledgerAppend({ id: id, kind: 'job', status: 'running', startedAt: j.startedAt, pid: j.pid, script: sp.win,
                   outdir: opts.outdir || WIN_TMP, engine: engineMode, timeoutMs: timeoutMs });
    j.timer = setTimeout(() => { j.status = 'killed'; try { child.kill('SIGKILL'); } catch (e) {} }, timeoutMs);
    child.on('close', (code, sig) => {
      clearTimeout(j.timer); j.exitCode = code; j.signal = sig; j.finishedAt = Date.now();
      if (j.status !== 'killed') j.status = (code === 0) ? 'done' : 'failed';
      try {
        const txt = fs.readFileSync(outPath, 'utf8');
        const lines = txt.split('\n').filter((l) => l.indexOf('HEADLESS ') === 0);
        if (lines.length) { try { j.parsed = JSON.parse(lines[lines.length - 1].slice('HEADLESS '.length).trim()); } catch (e) { j.parsed = { _parse_error: String((e && e.message) || e) }; } }
      } catch (e) {}
      try {
        const er = fs.readFileSync(errPath, 'utf8');
        const m = er.match(/^[\w.]*(?:Error|Exception|Warning)\b.*$/gm);
        if (m && m.length) j.lastException = m[m.length - 1].slice(0, 300);
      } catch (e) {}
      // v0.8.10（D4）：终态摘要落盘 + 台账追加（后端重启后仍可查）
      try {
        const arts = jobArtifacts(j).slice(0, 20);
        const summary = { id: id, kind: 'job', status: j.status, exitCode: j.exitCode, signal: j.signal,
                          startedAt: j.startedAt, finishedAt: Date.now(), ms: Date.now() - j.startedAt,
                          outdir: j.outdir || WIN_TMP, script: sp.win, engine: engineMode,
                          artifacts: arts.map((a) => a.name + '(' + a.bytes + 'B)'),
                          resultSummary: j.parsed ? JSON.stringify(j.parsed).slice(0, 400) : null,
                          lastException: j.lastException || null, pluginVersion: PLUGIN_VERSION };
        try { fs.writeFileSync(path.join(j.dir, 'summary.json'), JSON.stringify(summary, null, 1), 'utf8'); } catch (e) {}
        ledgerAppend(Object.assign({}, summary, { artifacts: arts.slice(0, 8).map((a) => a.name) }));
      } catch (e) { /* ignore */ }
    });
    child.on('error', (e) => { j.status = 'failed'; j.lastException = String((e && e.message) || e); });
    return jobSnapshot(j);
  }
  // ---- v0.8.10（A2/D4）：无头运行台账 + 磁盘台账（后端重启后仍可查）
  const runs = new Map();
  const LEDGER = path.join(winToWsl(WIN_TMP), 'jobs', 'index.jsonl');
  function ledgerAppend(rec) {
    try {
      fs.mkdirSync(path.dirname(LEDGER), { recursive: true });
      fs.appendFileSync(LEDGER, JSON.stringify(Object.assign({ pluginVersion: PLUGIN_VERSION, at: Date.now() }, rec)) + String.fromCharCode(10), 'utf8');
    } catch (e) { /* ignore */ }
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
  function runSnapshot(r) {
    return { id: r.id, kind: 'headless', status: r.status, pid: r.pid,
             ms: (r.finishedAt || Date.now()) - r.startedAt, outdir: r.outdir || WIN_TMP,
             logs: r.logs || null, script: r.script || null, exitCode: r.exitCode, timedOut: r.timedOut || false,
             artifacts: r.artifacts || [], artifactCount: (r.artifacts || []).length,
             expect: r.expect || null, pluginVersion: PLUGIN_VERSION,
             hint: '这是 blender_rt_headless 的运行台账：产物在 outdir，日志见 logs' };
  }
  function jobStatus(id) {
    const j = jobs.get(String(id));
    if (j) return jobSnapshot(j);
    const r = runs.get(String(id));
    if (r) return runSnapshot(r);
    const led = ledgerList(200).find((x) => x.id === String(id));
    return led || { id: String(id), status: 'unknown' };
  }
  function jobCollect(id, tail = 4000) {
    const j = jobs.get(String(id));
    if (!j) return { id: String(id), status: 'unknown' };
    const snap = jobSnapshot(j);
    try { snap.stdoutTail = fs.readFileSync(path.join(j.dir, 'stdout.log'), 'utf8').slice(-tail); } catch (e) { snap.stdoutTail = ''; }
    try { snap.stderrTail = fs.readFileSync(path.join(j.dir, 'stderr.log'), 'utf8').slice(-tail); } catch (e) { snap.stderrTail = ''; }
    return snap;
  }
  function jobKill(id) {
    const j = jobs.get(String(id));
    if (!j) return { id: String(id), status: 'unknown' };
    if (j.status === 'running') { j.status = 'killed'; try { j.child.kill('SIGKILL'); } catch (e) {} }
    return jobSnapshot(j);
  }
  function jobList() {
    const mem = Array.from(jobs.values()).map(jobSnapshot).concat(Array.from(runs.values()).map(runSnapshot));
    const ids = new Set(mem.map((x) => x.id));
    const disk = ledgerList(60).filter((x) => !ids.has(x.id));      // v0.8.10（D4）：后端重启后仍能列出历史作业
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
    job: { start: jobStart, status: jobStatus, collect: jobCollect, kill: jobKill, list: jobList },
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
      const out = await addon.send('execute_code', { code: REGION_CODE });
      // v0.8.10（A0/D4）：状态里带插件版本 + runtime 指纹 + 最近运行台账
      return { region: jsonFromStdout(out),
               plugin: { version: PLUGIN_VERSION, runtimeDir: wslToWin(HERE), runtime: runtimeFingerprint() },
               // v0.9.1（93-E2）：会话名 —— 多会话/多成员并存时，默认产物名与 evidence 路径按它隔离
               session: SESSION_NAME,
               resultsDir: wslToWin(RESULTS_DIR),
               trajectory: { dir: wslToWin(TRAJ_DIR), off: TRAJ_OFF, full: TRAJ_FULL,
                             note: 'v0.9.0（#15）：每次顶层调用一行 JSONL（route/op/ms/ok/参数摘要）；DSH_TRAJ=0 关，DSH_TRAJ_FULL=1 记更多参数' },
               runs: Array.from(runs.values()).slice(-5).map(runSnapshot),
               jobs: Array.from(jobs.values()).slice(-5).map(jobSnapshot),
               ledger: { path: wslToWin(LEDGER), recent: ledgerList(5).map((x) => ({ id: x.id, kind: x.kind || 'job', status: x.status, startedAt: x.startedAt, ms: x.ms })) } };
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
      // preload: 把 runtime 里的 python 模块（view/perf/runner/contract/planner...）源码拼进脚本开头
      let pre = '';
      const mods = Array.isArray(opts.preload) ? opts.preload : (opts.preload ? String(opts.preload).split(',') : []);
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
      const envPairsPreRunId = 'run-' + Date.now().toString(36) + Math.random().toString(36).slice(2, 5);
      const envPairs = {
        DSH_RUN_ID: envPairsPreRunId, DSH_OUTDIR: String(opts.outdir || WIN_TMP), DSH_SESSION: SESSION_NAME,
        DSH_PLUGIN_VERSION: PLUGIN_VERSION,
        DSH_ARGS: JSON.stringify(Array.isArray(opts.args) ? opts.args.map(String) : []),
      };
      if (opts.env && typeof opts.env === 'object') for (const k of Object.keys(opts.env)) envPairs[String(k)] = String(opts.env[k]);
      headParts.push(['# ---- DSH env prelude（子进程内可见；绕开 WSL interop 的 env 过滤）----', 'try:',
        '    import os as _os_pre', '    _os_pre.environ.update(' + JSON.stringify(envPairs) + ')',
        'except Exception:', '    pass'].join(String.fromCharCode(10)));
      if (opts.bootstrap !== false) headParts.push(KERNEL_BOOTSTRAP);
      if (gpuPre) headParts.push(gpuPre);
      // v0.8.10（D2）：workdir → 脚本内 chdir + sys.path 首位（Blender 是 Windows 进程，必须过 K.win_path）
      if (opts.workdir) {
        headParts.push(['# ---- DSH workdir ----',
          'try:',
          '    import os as _os, sys as _sys',
          '    _wd = K.win_path(' + JSON.stringify(String(opts.workdir)) + ')',
          '    _os.chdir(_wd)',
          '    if _wd not in _sys.path: _sys.path.insert(0, _wd)',
          '    print("DSH_WORKDIR " + _wd)',
          'except Exception as _e:',
          '    print("DSH_WORKDIR_ERR " + str(_e)[:120])'].join(String.fromCharCode(10)));
      }
      // v0.8.10（B2）：expect 前后哨兵 —— "build 返回空却不抛异常"必须被判失败
      const exp = (opts.expect && typeof opts.expect === 'object') ? opts.expect : null;
      const expectPre = exp ? ['# ---- DSH expect: before ----', 'import json as _dsh_ejson',
        '_DSH_EXP0 = {"objects": len(bpy.data.objects), "meshes": len(bpy.data.meshes), "materials": len(bpy.data.materials)}'].join(String.fromCharCode(10)) : '';
      const expectPost = exp ? ['# ---- DSH expect: after ----', 'try:',
        '    _DSH_EXP1 = {"objects": len(bpy.data.objects), "meshes": len(bpy.data.meshes), "materials": len(bpy.data.materials)}',
        '    print("DSH_EXPECT " + _dsh_ejson.dumps({"before": _DSH_EXP0, "after": _DSH_EXP1, "delta": {k: _DSH_EXP1[k] - _DSH_EXP0[k] for k in _DSH_EXP1}}))',
        'except Exception as _e:', '    print("DSH_EXPECT " + _dsh_ejson.dumps({"error": str(_e)[:160]}))'].join(String.fromCharCode(10)) : '';
      const body = (script || pre || gpuPre) ? (headParts.length ? headParts.join('\n') + '\n' : '') + (expectPre ? expectPre + String.fromCharCode(10) : '') + pre + script + (expectPost ? String.fromCharCode(10) + expectPost : '') : '';
      const args = ['-b'];
      if (opts.file && !fileLooksPython) args.push(wslToWin(String(opts.file)));
      if (opts.factoryStartup !== false) args.push('--factory-startup');
      let sp = null;
      if (body) { sp = writeHeadlessScript(body); args.push('--python', sp.win); }
      args.push('--');
      const outdirWsl = opts.outdir ? winToWsl(String(opts.outdir)) : null;
      if (opts.outdir) args.push(String(opts.outdir));
      if (Array.isArray(opts.args)) args.push.apply(args, opts.args.map(String));
      const timeoutMs = Math.max(1000, Math.min(1800000, Number(opts.timeoutMs) || 180000));
      metrics.headlessRuns++;
      // v0.8.10（A2/D4）：每次无头运行都登记成可查台账（id 与作业同空间：blender_rt_job op=status id=run-…）
      const runId = envPairsPreRunId;
      const runRec = { id: runId, child: null, pid: null, startedAt: t0, status: 'running',
                       outdir: opts.outdir || WIN_TMP, logs: null, script: sp ? sp.win : null,
                       exitCode: null, timedOut: false, artifacts: [], expect: exp, workdir: opts.workdir || null };
      runs.set(runId, runRec);
      if (runs.size > 20) { const k = runs.keys().next().value; if (k !== runId) runs.delete(k); }
      ledgerAppend({ id: runId, kind: 'headless', status: 'running', startedAt: t0, script: runRec.script,
                     outdir: runRec.outdir, timeoutMs: timeoutMs });
      // v0.8.7（外部反馈 #4 P0-2）：长任务自动转作业层 —— 客户端超时不再把结果丢掉。
      // 用法：as_job=true 强制转；或给 auto_job_ms（如 120000）表示预计超过它就走作业。
      const autoJobMs = Number(opts.autoJobMs) || 0;
      if (opts.asJob || (autoJobMs > 0 && timeoutMs >= autoJobMs)) {
        const j = jobStart(Object.assign({}, opts, { timeoutMs: Math.max(timeoutMs, 3600000) }));
        return { ok: true, mode: 'job', ms: Date.now() - t0, jobId: j.id, job: j,
                 logs: { stdout: j.stdoutLog, stderr: j.stderrLog },
                 reason: null,
                 hint: '已转作业层（长任务）：用 blender_rt_job(op="status"/"collect", id="' + j.id + '") 跟进，或用 jobId 轮询 /job' };
      }
      // ---- 子进程环境：可选透传用户 Blender 配置（GPU 偏好在里面）
      // v0.9.1（93-B4）：显式 env 入参 + 插件注入的契约变量 —— 参数不必再挤 argv（DSH_ARGS/DSH_OUTDIR/DSH_RUN_ID）
      const childEnv = Object.assign({}, process.env, {
        PYTHONIOENCODING: 'utf-8',
        DSH_RUN_ID: runId,
        DSH_OUTDIR: String(opts.outdir || WIN_TMP),
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
      try {
        const pass = Object.keys(childEnv).filter((k) => k.indexOf('DSH_') === 0 || (opts.env && Object.prototype.hasOwnProperty.call(opts.env, k)));
        const spec = pass.map((k) => k + '/w').join(':');
        childEnv.WSLENV = childEnv.WSLENV ? (childEnv.WSLENV + ':' + spec) : spec;
      } catch (e) { /* WSLENV 拼不出来就算了，脚本内注入是主路径 */ }
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
        const push = (buf, which) => {
          const t = buf.toString('utf8');
          if (which === 'o') { so += t; if (so.length > CAP) so = so.slice(-CAP); }
          else { se += t; if (se.length > CAP) se = se.slice(-CAP); }
        };
        child.stdout.on('data', (d) => push(d, 'o'));
        child.stderr.on('data', (d) => push(d, 'e'));
        child.on('error', (e) => { spawnErr = e; });
        const timer = setTimeout(() => { timedOut = true; try { child.kill('SIGKILL'); } catch (e) {} }, timeoutMs);
        child.on('close', (code, sig) => { clearTimeout(timer); exitCode = code; signal = sig; resolve({ so: so, se: se, exitCode: exitCode, signal: signal, spawnErr: spawnErr, timedOut: timedOut }); });
      });
      const ms = Date.now() - t0;
      metrics.lastHeadlessMs = ms;
      if (res.spawnErr) {
        const msg = '起不来 Blender 进程（' + BLENDER_EXE + '）：' + String((res.spawnErr && res.spawnErr.message) || res.spawnErr);
        const e = new Error(msg); e.code = 'blender-exe-missing';
        e.hint = '自动探测没找到 blender 可执行文件。三种配置方式（任选一种）：'
          + '① 环境变量 DSH_BLENDER_EXE=' + (IS_WIN ? 'D:\\...\\blender.exe' : '/mnt/d/.../blender.exe')
          + ' ② 包根写 dsh-blender.config.json: {"blenderExe":"..."}'
          + ' ③ ~/.dsh/dsh-blender.config.json。当前探测结果见 blender_viewport op=doctor 的 config 字段。';
        throw e;
      }
      const stdout = String(res.so || '');
      const stderr = String(res.se || '');
      // ---- 结果契约：HEADLESS <单行 JSON>（取最后一条）
      let parsed = null;
      const lines = stdout.split('\n').filter((l) => l.indexOf('HEADLESS ') === 0);
      if (lines.length) { try { parsed = JSON.parse(lines[lines.length - 1].slice('HEADLESS '.length).trim()); } catch (e) { parsed = { _parse_error: String((e && e.message) || e), raw: lines[lines.length - 1].slice(0, 300) }; } }
      // ---- GPU 回执（前导打印 DSH_GPU）
      let gpu = null;
      const gl = stdout.split('\n').filter((l) => l.indexOf('DSH_GPU ') === 0);
      if (gl.length) { try { gpu = JSON.parse(gl[gl.length - 1].slice('DSH_GPU '.length).trim()); } catch (e) { gpu = { _parse_error: String((e && e.message) || e) }; } }
      // ---- 全量日志落盘
      const logDirWsl = outdirWsl || winToWsl(WIN_TMP);
      const stamp = new Date(t0).toISOString().replace(/[:.]/g, '-');
      let logs = null;
      try {
        fs.mkdirSync(logDirWsl, { recursive: true });
        const soPath = path.join(logDirWsl, 'dsh_headless_' + stamp + '.stdout.log');
        const sePath = path.join(logDirWsl, 'dsh_headless_' + stamp + '.stderr.log');
        fs.writeFileSync(soPath, stdout, 'utf8');
        fs.writeFileSync(sePath, stderr, 'utf8');
        logs = { stdout: wslToWin(soPath), stderr: wslToWin(sePath) };
      } catch (e) { logs = null; }
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
      // ---- v0.9.1（93-A3）：结构化结果落盘（>4KB 自动落，或 outJson= 指定路径）—— 不必再从 stdout 里 indexOf 切片
      let outJsonInfo = { path: null, bytes: 0 };
      try {
        if (parsed !== null && parsed !== undefined) {
          const s = JSON.stringify(parsed);
          outJsonInfo.bytes = s.length;
          if (opts.outJson) {
            const t = readScriptPath(String(opts.outJson));
            fs.mkdirSync(path.dirname(t.wsl), { recursive: true });
            fs.writeFileSync(t.wsl, s, 'utf8');
            outJsonInfo.path = t.win;
          } else if (s.length > 4000) {
            outJsonInfo = Object.assign(outJsonInfo, dumpResult(parsed, runId));
          }
        }
      } catch (e) { /* 落盘失败不影响主结果 */ }
      // ---- v0.8.10（A2/D4）：台账终态
      try {
        runRec.status = res.timedOut ? 'killed' : (res.exitCode === 0 ? 'done' : 'failed');
        runRec.exitCode = res.exitCode; runRec.timedOut = !!res.timedOut; runRec.finishedAt = Date.now();
        runRec.artifacts = artifacts.slice(0, 20).map((a) => a.name);
        runRec.logs = logs; runRec.expect = expectEval;
        ledgerAppend({ id: runId, kind: 'headless', status: runRec.status, exitCode: res.exitCode,
                       ms: runRec.finishedAt - runRec.startedAt, outdir: runRec.outdir, script: runRec.script,
                       artifacts: runRec.artifacts.slice(0, 8), expectOk: (expectEval ? expectEval.ok : null) });
      } catch (e) { /* ignore */ }
      return { ok: runOk, runId: runId, pluginVersion: PLUGIN_VERSION, expect: expectEval,
               stdoutTruncated: stdout.length > 8000,
        status: failureClass, resumable: true, session: SESSION_NAME,
        failure_hint: FAIL_HINT,
        how_to_recover: '客户端超时/断连不代表失败：这是独立子进程。用 blender_rt_job(op="status"|"collect", id="' + runId +
                        '") 按 runId 回收结果与产物（台账：' + wslToWin(LEDGER) + '）',
        scriptFile: scriptFileInfo,
        outJson: outJsonInfo.path, resultBytes: outJsonInfo.bytes,
        resultPath: outJsonInfo.path,
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
