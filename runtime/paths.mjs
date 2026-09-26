/**
 * S3-b · 路径与常量（从 engine.mjs 外移）
 *
 * 绝对/跨 OS 路径、临时目录、addon 侧命令目录、以及 KERNEL_BOOTSTRAP 用的 PATH_HELPERS。
 * engine.mjs 具名导入并原样重导出 ⇒ 对外面逐字节不变（tmp/snap.mjs / snap_norm.mjs 对拍）。
 */
import { createHash } from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { CFG, PATHS, PKG_ROOT, IS_WIN, IS_MAC, winToWsl, wslToWin } from './config.mjs';

/** 与 engine.mjs 同目录（runtime/） */
export const HERE = path.dirname(fileURLToPath(import.meta.url));

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
/** 共享内核（kit）：JSON/API 样板·单位·几何·度量·回执骨架的唯一实现 */
export const KIT_PATH = path.join(HERE, 'kit.py');
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
/** 程序化雕刻（v0.9.6 · 上游整合 A1）：sculpt_* 前缀 op 走这里。
 *  上游两家都做不到程序化雕刻（blend-ai 自述不能模拟笔触；mcp-for-blender 没有雕刻），
 *  本模块用 numpy 位移笔刷 + 拓扑准备 + 遮罩把"雕刻"变成可复算的几何操作。 */
export const SCULPT_PATH = path.join(HERE, 'sculpt.py');
/** 网格修复闭环（v0.9.6 · 上游整合 A2）：fix_* 前缀 op —— audit 只诊断，这里负责治。 */
export const FIX_PATH = path.join(HERE, 'mesh_fix.py');
/** UV 四件套（v0.9.6 · 上游整合 A3）：uv_* 前缀 op（smart project / unwrap / projection / pack）。 */
export const UV_PATH = path.join(HERE, 'uv_tools.py');
/** 制造检查（v0.9.6 · 上游整合 A4）：print_* 前缀 op（薄壁 / 悬垂 / 壳体 / 汇总）。 */
export const PRINT_PATH = path.join(HERE, 'printcheck.py');
/** 沿路径扫掠（v0.9.6 · 上游整合 A5）：sweep_* 前缀 op（管路/线缆/轨道 + 弯折半径先算后建）。 */
export const SWEEP_PATH = path.join(HERE, 'sweep.py');
/** 材质节点图（v0.9.6 · A6）：material_* 前缀 op —— 实测 2,732 次节点连线 / 204 个脚本在重复造轮子 */
export const MATERIAL_PATH = path.join(HERE, 'material.py');
/** 渲染状态跟踪（v0.9.6 · A7）：渲染开始写磁盘标记、结束删；后端读文件秒判 busy */
export const RENDER_GUARD_PATH = path.join(HERE, 'render_guard.py');
/** 规格驱动门包（v0.9.6 · 现场反馈 #3）：一份 spec 跑完所有门（三态判定） */
export const GATE_PATH = path.join(HERE, 'gate.py');
/** 参考图量具（P0 整合方案）：量成数字 / 裁出来放大 / 画回去 / 差分 */
export const IMG_PATH = path.join(HERE, 'imgtools.py');
/** 门标定套件（P0）：注入 8 类缺陷量门抓得住几类 + 健康基线对照 */
export const CALIB_PATH = path.join(HERE, 'calib.py');
/** 人脸比例门（P1）：landmarks → 6 个无量纲比例 → 与经典/参考比差 */
export const FACE_PATH = path.join(HERE, 'faceeval.py');
/** 人形素体（P2 零素材）：关节坐标+半径 → Skin+Subsurf → 比例正确的假人 */
export const HUMAN_PATH = path.join(HERE, 'human.py');
/** 成对间隙门（P3）：规则表驱动的最近距离 + 互穿判定 */
export const CLEARANCE_PATH = path.join(HERE, 'clearance.py');
/** 车辆外壳（P3）：纵向剖面 → 放样成壳（解析轮眉）+ 比例门 */
export const VEHICLE_PATH = path.join(HERE, 'vehicle.py');
/** 参考图通用还原方法层（P4）：类别协议 + 通用量具/放样/拟合/分件/旋转体 */
export const SHAPE_PATH = path.join(HERE, 'shapegen.py');
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

// kit 的**内容指纹**：并入每个模块的失效判据 ⇒ 改 kit 会让所有模块在下次调用时自动重注
export const KIT_SRC = (() => { try { return fs.readFileSync(KIT_PATH, "utf8"); } catch (e) { return ""; } })();
export const KIT_HASH = createHash("sha256").update(KIT_SRC).digest("hex").slice(0, 12);
