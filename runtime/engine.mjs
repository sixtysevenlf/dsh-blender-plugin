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
import { CFG, PATHS, winToWsl, wslToWin, describeConfig, IS_WIN } from './config.mjs';
// 协议适配层：直连通道支持两种 addon 实现（ahujasid 扁平协议 / harveyxiacn category-action），
// 由 CFG.addonProtocol 选择，默认 auto 自动探测。差异与映射见 runtime/addon-protocol.mjs。
import { resolveProtocol, detectProtocol, resetProtocolCache } from './addon-protocol.mjs';

const HERE = path.dirname(fileURLToPath(import.meta.url));

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
/**
 * 用户 Blender 配置目录（GPU 偏好所在）—— 无头进程默认读不到它，Cycles 会静默回落 CPU。
 * 分享版默认 null（用系统默认配置）；要继承某套配置就设 DSH_BLENDER_USER_CONFIG / 配置项 blenderUserConfig。
 */
export const USER_CONFIG_WIN = process.env.BLENDER_USER_CONFIG || CFG.blenderUserConfig || null;
export const USER_SCRIPTS_WIN = process.env.BLENDER_USER_SCRIPTS || CFG.blenderUserScripts || null;
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
'K.dsh_distro = __DISTRO__',
'K.win_path = _dsh_win_path',
'K.wsl_path = _dsh_wsl_path',
'K.blend_path = _dsh_blend_path',
'K.run = _dsh_run',
'K.out_dir = __OUTDIR__',
'K.workdir = K.out_dir',
].join('\n');
const PATH_HELPERS = PATH_HELPERS_TEMPLATE
  .replace(/__DISTRO__/g, JSON.stringify(process.env.WSL_DISTRO_NAME || 'Ubuntu'))
  .replace(/__OUTDIR__/g, JSON.stringify(WIN_TMP));
/** act 包装：异常也回传 partial stdout/stderr/traceback（v0.7.0） */
export const ACT_WRAPPER = (src) => [
  'import io as _dsh_io, contextlib as _dsh_ctx, traceback as _dsh_tb, json as _dsh_json',
  '_dsh_src = _dsh_json.loads(' + JSON.stringify(JSON.stringify(src)) + ')',
  '_dsh_o = _dsh_io.StringIO(); _dsh_e = _dsh_io.StringIO()',
  'try:',
  '    with _dsh_ctx.redirect_stdout(_dsh_o), _dsh_ctx.redirect_stderr(_dsh_e):',
  '        exec(compile(_dsh_src, "<rt_do>", "exec"), globals())',
  'except BaseException as _dsh_exc:',
  '    print("DSH_ACT_ERR " + _dsh_json.dumps({"error": "%s: %s" % (type(_dsh_exc).__name__, _dsh_exc), "traceback": _dsh_tb.format_exc()[-6000:], "stdout": _dsh_o.getvalue()[-16000:], "stderr": _dsh_e.getvalue()[-8000:]}, ensure_ascii=False))',
  'else:',
  '    print("DSH_ACT_OK " + _dsh_json.dumps({"stdout": _dsh_o.getvalue()[-16000:], "stderr": _dsh_e.getvalue()[-8000:]}, ensure_ascii=False))',
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
                        TXN_READY: 'dsh_txn_api', QC_READY: 'dsh_qc_api' };
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
      if (!p.view.path) p.view.path = path.win32.join(WIN_TMP, 'dsh_evidence.png');
      payload = p;
    }            // 证据要出图 → 先注入 view.py
    if (isPlan) {
      await ensurePlanner();
      const body = 'print("LOOP " + K.dsh_plan_api["dispatch"](' + JSON.stringify(o.slice(5)) + ', _json.dumps(_json.loads('
        + JSON.stringify(JSON.stringify(payload || {})) + '))))';
      return extractLoop(await addon.send('execute_code', { code: KERNEL_BOOTSTRAP + '\nimport json as _json\n' + body }, 300000));
    }
    if (o.indexOf('qc_') === 0) {
      // QC 走 blender_rt_plan 的 qc_* 前缀（验证与证据同属契约层；不新增工具）
      await ensureQc();
      const body = 'print("LOOP " + K.dsh_qc_api["dispatch"](' + JSON.stringify(o.slice(3)) + ', _json.dumps(_json.loads('
        + JSON.stringify(JSON.stringify(payload || {})) + '))))';
      return extractLoop(await addon.send('execute_code', { code: KERNEL_BOOTSTRAP + '\nimport json as _json\n' + body }, 300000));
    }
    await ensureContract();
    const body = 'print("LOOP " + K.dsh_contract_api["dispatch"](' + JSON.stringify(o) + ', _json.dumps(_json.loads('
      + JSON.stringify(JSON.stringify(payload || {})) + '))))';
    return extractLoop(await addon.send('execute_code', { code: KERNEL_BOOTSTRAP + '\nimport json as _json\n' + body }, 300000));
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
  async function workerExec(code, timeoutMs = 120000) {
    if (!workerAlive() || !worker.ready) await workerStart({});
    metrics.workerExecs++;
    try { return await workerCall({ op: 'exec', code: String(code || '') }, timeoutMs); }
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
  return {
    addon: addon,
    plan: (op, payload) => planCall(op, payload),
    txn: (op, payload) => txnCall(op, payload),
    worker: { start: workerStart, exec: workerExec, status: workerStatus, stop: workerStop, snapshot: workerSnapshot },
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
      return { region: jsonFromStdout(out) };
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
                 error: p.error || null, traceback: p.traceback || null, file: file || null };
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
      const script = opts.script ? String(opts.script) : '';
      // preload: 把 runtime 里的 python 模块（view/perf/runner/contract/planner...）源码拼进脚本开头
      let pre = '';
      const mods = Array.isArray(opts.preload) ? opts.preload : (opts.preload ? String(opts.preload).split(',') : []);
      for (const m of mods) {
        const name = String(m).trim().replace(/\.py$/, '');
        if (!name) continue;
        const f = path.join(HERE, name + '.py');
        if (!fs.existsSync(f)) throw new Error('preload 找不到模块：' + f);
        pre += '# ---- preload ' + name + '.py ----\n' + fs.readFileSync(f, 'utf8') + '\n';
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
      if (opts.bootstrap !== false) headParts.push(KERNEL_BOOTSTRAP);
      if (gpuPre) headParts.push(gpuPre);
      const body = (script || pre || gpuPre) ? (headParts.length ? headParts.join('\n') + '\n' : '') + pre + script : '';
      const args = ['-b'];
      if (opts.file) args.push(wslToWin(String(opts.file)));
      if (opts.factoryStartup !== false) args.push('--factory-startup');
      let sp = null;
      if (body) { sp = writeHeadlessScript(body); args.push('--python', sp.win); }
      args.push('--');
      const outdirWsl = opts.outdir ? winToWsl(String(opts.outdir)) : null;
      if (opts.outdir) args.push(String(opts.outdir));
      if (Array.isArray(opts.args)) args.push.apply(args, opts.args.map(String));
      const timeoutMs = Math.max(1000, Math.min(1800000, Number(opts.timeoutMs) || 180000));
      metrics.headlessRuns++;
      // ---- 子进程环境：可选透传用户 Blender 配置（GPU 偏好在里面）
      const childEnv = Object.assign({}, process.env, { PYTHONIOENCODING: 'utf-8' });
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
      if (em && em.length) lastException = em[em.length - 1].slice(0, 300);
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
      return { ok: !res.timedOut && res.exitCode === 0 && !gpuFailed,
        exitCode: res.exitCode, signal: res.signal, timedOut: !!res.timedOut,
        ms: ms, timeoutMs: timeoutMs, blender: BLENDER_EXE, script: sp ? sp.win : null, args: args,
        preload: mods.length ? mods : undefined, gpu: gpu, engine: (gpu && gpu.after && gpu.after.engine) || null,
        engineMode: (gpu && gpu.mode) || null, useUserConfig: !!opts.useUserConfig,
        logs: logs, lastException: lastException, traceback: traceback, hint: hint,
        noiseFiltered: noiseFiltered,
        reason: res.timedOut ? ('killed after timeout ' + timeoutMs + 'ms')
          : (gpuFailed ? 'gpu required but unavailable' : (res.exitCode === 0 ? null : 'exit code ' + String(res.exitCode))),
        result: parsed, stdout: stdout.slice(-8000), stderr: stderr.slice(-4000), artifacts: artifacts };
    },
    async start() { await addon.ensure(); return true; },
    stop() { addon.close(); try { if (workerAlive()) worker.child.kill('SIGKILL'); } catch (e) {} },
  };
}
