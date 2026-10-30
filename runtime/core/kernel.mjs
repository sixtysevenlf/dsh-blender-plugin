// Blender 持久内核：KERNEL_BOOTSTRAP、模块注入与 ensure* 链（T6）
// @imports
import { pyModuleFile, pyModuleNames, ASSEMBLY_PATH, AUDIT_PATH, BLOCKOUT_PATH, CALIB_PATH, CLEARANCE_PATH, CONTRACT_PATH, DELIVER_PATH, EVALHARNESS_PATH, FACE_PATH, FIX_PATH, GATE_PATH, GENERATOR_PATH, GEO_SRC, HUMAN_PATH, IMG_PATH, KIT_HASH, KIT_SRC, MATERIAL_PATH, MONTAGE_PATH, MOTION_PATH, PATH_HELPERS, PERF_PATH, PIPELINE_PATH, PLANNER_PATH, PRESET_PATH, PRINT_PATH, QC_PATH, QC_RENDER_PATH, RENDER_GUARD_PATH, RUNNER_PATH, SCULPT_PATH, SHAPESOLVE_PATH, SHAPE_PATH, SURFACE_PATH, SWEEP_PATH, TXN_PATH, UV_PATH, VEHICLE_PATH, VIEW_PATH, VISUALREPORT_PATH, WIN_TMP } from "../paths.mjs";
import fs from "node:fs";
import { slipFixPath } from "./output.mjs";
import { winToWsl, wslToWin } from "../config.mjs";

import path from "node:path";
import { fileURLToPath } from "node:url";

const RUNTIME_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

export const KERNEL_BOOTSTRAP = [
  'import sys as _sys, types as _types',
  'K = _sys.modules.get("dsh_rt_kernel")',
  'if K is None:',
  '    K = _types.ModuleType("dsh_rt_kernel")',
  '    _sys.modules["dsh_rt_kernel"] = K',
  // v1.0.1（2026-09-30 实测反馈）：PRELOAD_MODULES / runtime/*.py 的 API 挂在
  // K.dsh_<name>_api 上，但**没 preload 的模块就是不存在** —— 裸的 AttributeError
  // 不会告诉 agent"要用 preload"，实测 4 个 agent 独立在这一步卡过一轮。
  // 给内核模块挂 PEP 562 的 __getattr__，把"缺模块"变成"告诉你怎么装 + 已装了什么"。
  'if not hasattr(K, "__getattr__"):',
  '    def _dsh_kernel_getattr(_name, _K=K):',
  '        if _name.startswith("dsh_") and _name.endswith("_api"):',
  '            _have = sorted(_k[4:-4] for _k in dir(_K)',
  '                           if _k.startswith("dsh_") and _k.endswith("_api"))',
  '            _base = _name[4:-4]',
  '            raise AttributeError(',
  '                "内核里没有 %s —— 模块 %r 没被加载。已加载：%s。"',
  '                "headless 用 preload=%r 带上它；或 K.dsh_kit.kapi(%r) 查/取。"',
  '                % (_name, _base, _have or "（只有 kit）", _base, _base))',
  '        raise AttributeError(_name)',
  '    K.__getattr__ = _dsh_kernel_getattr',
  'import bpy, math, mathutils',
  // ---- v2.6（本轮反馈 #3/#7）：多灯卫生 + 对象归属路由（任何通道都在）----
  'def _dsh_light_hygiene(apply=False, max_shadow=4):',
  '    import json as _j, bpy as _b',
  '    lights = [o for o in _b.data.objects if o.type == "LIGHT"]',
  '    rows = [{"name": o.name, "type": o.data.type, "energy": round(float(o.data.energy), 3), "shadow": bool(o.data.use_shadow)} for o in lights]',
  '    res = {"lights": len(rows), "shadow_casters_before": sum(1 for r in rows if r["shadow"]), "by_type": {}, "max_shadow": int(max_shadow)}',
  '    for r in rows:',
  '        res["by_type"][r["type"]] = res["by_type"].get(r["type"], 0) + 1',
  '    pool = None',
  '    try:',
  '        pool = str(_b.context.scene.eevee.shadow_pool_size)',
  '    except Exception:',
  '        pass',
  '    res["shadow_pool_size"] = pool',
  '    if apply:',
  '        keep = 0',
  '        for o in sorted(lights, key=lambda x: -float(x.data.energy)):',
  '            if o.data.type in {"POINT", "SPOT"}:',
  '                if keep < int(max_shadow):',
  '                    keep += 1',
  '                else:',
  '                    o.data.use_shadow = False',
  '        try:',
  '            _b.context.scene.eevee.shadow_pool_size = "2048"',
  '        except Exception:',
  '            pass',
  '        res["shadow_casters_after"] = sum(1 for o in lights if o.data.use_shadow)',
  '        res["shadow_pool_size"] = getattr(_b.context.scene.eevee, "shadow_pool_size", pool)',
  '    res["hint"] = "EEVEE 阴影池上限 2048 MB（枚举，写 4096 无效）。亮灯多的场景请 apply=True：只保留能量最高的 max_shadow 盏点/聚光灯投影，其余 use_shadow=False。"',
  '    print("LIGHT_HYGIENE " + _j.dumps(res, ensure_ascii=False))',
  '    return res',
  'K.light_hygiene = _dsh_light_hygiene',
  'def _dsh_route_collections(rules=None, apply=True):',
  '    import json as _j, bpy as _b',
  '    DEFAULT = [("LGT-", "COL_Lights"), ("LIGHT-", "COL_Lights"), ("CAM-", "COL_Camera"),',
  '               ("GEO-VFX-", "COL_VFX"), ("GEO-CITY-", "COL_City"), ("GEO-GZ-", "COL_Godzilla")]',
  '    rules = rules or DEFAULT',
  '    moved = []',
  '    for ob in list(_b.data.objects):',
  '        for pref, cname in rules:',
  '            if ob.name.startswith(pref):',
  '                coll = _b.data.collections.get(cname) or _b.data.collections.new(cname)',
  '                if coll.name not in [c.name for c in ob.users_collection]:',
  '                    for c in list(ob.users_collection):',
  '                        c.objects.unlink(ob)',
  '                    coll.objects.link(ob)',
  '                    moved.append({"object": ob.name, "collection": coll.name})',
  '                break',
  '    res = {"moved": moved, "count": len(moved), "rules": [list(r) for r in rules]}',
  '    print("ROUTE_COLLECTIONS " + _j.dumps(res, ensure_ascii=False))',
  '    return res',
  'K.route_collections = _dsh_route_collections',
  'Vector = mathutils.Vector',
  '# ---- 路径辅助（v0.7.0）：GUI 与无头两侧都能用，省掉手拼 UNC 与 chr(92)\n' + PATH_HELPERS,
  '# ---- 共享内核 kit（唯一实现）：**按需执行** ----',
  '# 实测：kit 10.7KB，每次 execute_code 都 exec 一遍要 8.4ms（= 往返 163ms 的 5%）。',
  '# 改成条件执行：K 上已有 dsh_kit 就跳过（持久内核下只在第一次真跑）。',
  '# 传输量不变（本地 socket 可忽略），换来零风险：任何路径都保证 kit 在。',
  'if getattr(K, "dsh_kit", None) is None:',
  ...KIT_SRC.split('\n').map((l) => (l ? '    ' + l : l)),
  // ---- 几何底座（geo · 机制层：布尔/开孔/开槽/镜像/阵列/拉伸/放样/倒角/自动平滑）----
  // 与 kit 同等：**任何注入路径都保证在**。不做成"可选 preload"的理由是实测 ——
  // 最大的失效模式不是报错，是"不知道有这个模块"（两个会话各手写一套 kit 的直接原因）。
  // 代价：每次 execute_code 传输量 +GEO_SRC（本地 socket，可忽略）；exec 只在首轮（持久内核）。
  'if getattr(K, "dsh_geo_api", None) is None:',
  ...GEO_SRC.split('\n').map((l) => (l ? '    ' + l : l)),
].join('\n');

export function createKernel(E) {
  const { RENDER_FLAG_WIN, addon, injected } = E;
  const MODULE_ATTR = { RUNNER_READY: 'dsh_loop_api', PERF_READY: 'dsh_perf_api', VIEW_READY: 'dsh_view_api',
                        CONTRACT_READY: 'dsh_contract_api', PLAN_READY: 'dsh_plan_api',
                        TXN_READY: 'dsh_txn_api', QC_READY: 'dsh_qc_api', GEO_READY: 'dsh_geo_api',
                        QC_RENDER_READY: 'dsh_qc_render_api',
                        PRESET_READY: 'dsh_preset_api',
                        AUDIT_READY: 'dsh_audit_api', MONTAGE_READY: 'dsh_montage_api',
                        MOTION_READY: 'dsh_motion_api', DELIVER_READY: 'dsh_deliver_api',
                        GENERATOR_READY: 'dsh_generator_api',
                        SCULPT_READY: 'dsh_sculpt_api', FIX_READY: 'dsh_fix_api',
                        UV_READY: 'dsh_uv_api', PRINT_READY: 'dsh_print_api',
                        SWEEP_READY: 'dsh_sweep_api',
                        MATERIAL_READY: 'dsh_material_api', RENDER_GUARD_READY: 'dsh_render_guard',
                        GATE_READY: 'dsh_gate_api', IMG_READY: 'dsh_img_api', CALIB_READY: 'dsh_calib_api', FACE_READY: 'dsh_face_api', HUMAN_READY: 'dsh_human_api', CLEARANCE_READY: 'dsh_clearance_api', VEHICLE_READY: 'dsh_vehicle_api', SHAPE_READY: 'dsh_shape_api', PIPELINE_READY: 'dsh_pipe_api',
                        ASSEMBLY_READY: 'dsh_assembly_api', BLOCKOUT_READY: 'dsh_blockout_api',
                        VISUALREPORT_READY: 'dsh_visualreport_api', SHAPESOLVE_READY: 'dsh_shapesolve_api',
                        EVALHARNESS_READY: 'dsh_evalharness_api', SURFACE_READY: 'dsh_surface_api' };
  /** 读 runtime 下的 python 模块源码（preload / 作业脚本拼接用） */
  const readModuleSource = (name) => fs.readFileSync(pyModuleFile(name), 'utf8');
  /**
   * v0.9.1（93-B3）：脚本路径解析。headless 的 `file=` 语义是 **.blend**（老成员误传 .py 会拿到
   * `File format is not supported`），现在：显式 `scriptFile=` 收 .py；`file=` 传 .py 时自动改当脚本并给提示。
   * Windows 路径（D:\ 或 \\wsl.localhost\）与 WSL 路径（/home/...）都能收。
   */
  function readScriptPath(p) {
    const s = slipFixPath(String(p), null);   // v0.9.6 现场反馈：漏 / 的 WSL 路径先纠正
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
  async function injectModule(file, marker, versionExpr = '1', prelude = '') {
    const attr = MODULE_ATTR[marker] || ('dsh_' + String(marker).toLowerCase() + '_api');
    const hashAttr = attr + '_fp';
    // 内容指纹（size + mtime）：模块文件一改，下次调用自动重新注入 —— 开发闭环必需。
    // 注意：K 跨后端重启保留，所以"只查 hasattr"会让改了文件却不生效（v0.7.0 修）。
    let fp = 'x';
    try { const st = fs.statSync(file); fp = String(st.size) + '-' + String(Math.round(st.mtimeMs)) + '-k' + KIT_HASH; } catch (e) { fp = 'missing-k' + KIT_HASH; }
    // K 才是真相：Blender 重启后 K 会清空，仅靠本地 Set 会误判"已注入"
    try {
      // noMark：加载 python 模块是纯 plumbing，不可能改场景 —— 不该参与 stale 判定
      const chk = await addon.send('execute_code', { code: KERNEL_BOOTSTRAP + '\nprint("HAVE " + str(hasattr(K, "' + attr + '")) + " FP " + str(getattr(K, "' + hashAttr + '", "none")))' }, 30000, { noMark: true });
      const t = (chk && typeof chk.result === 'string') ? chk.result : '';
      if (t.includes('HAVE True') && t.includes(fp)) { injected.add(file); return true; }
      injected.delete(file);
    } catch (e) { /* 探测失败 → 走重新注入 */ }
    const src = fs.readFileSync(file, 'utf8');
    const tail = '\nK.' + hashAttr + ' = ' + JSON.stringify(fp) + '\nprint("' + marker + ' v%d" % ' + versionExpr + ')';
    const out = await addon.send('execute_code', { code: KERNEL_BOOTSTRAP + '\n' + (prelude ? prelude + '\n' : '') + src + tail }, 60000, { noMark: true });
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
  // v0.9.6（上游整合）：五个新模块都走 needFile —— 还没落盘时给清楚的错，而不是 ENOENT
  const ensureSculpt = () => injectModule(needFile(SCULPT_PATH, 'sculpt'), 'SCULPT_READY', 'SCULPT_VERSION');
  const ensureFix = () => injectModule(needFile(FIX_PATH, 'mesh_fix'), 'FIX_READY', 'FIX_VERSION');
  const ensureUv = () => injectModule(needFile(UV_PATH, 'uv_tools'), 'UV_READY', 'UV_VERSION');
  const ensurePrint = () => injectModule(needFile(PRINT_PATH, 'printcheck'), 'PRINT_READY', 'PRINT_VERSION');
  const ensureSweep = () => injectModule(needFile(SWEEP_PATH, 'sweep'), 'SWEEP_READY', 'SWEEP_VERSION');
  const ensureMaterial = () => injectModule(needFile(MATERIAL_PATH, 'material'), 'MATERIAL_READY', 'MAT_VERSION');
  // 门包会按 spec 调其它模块：先把常用的几个备好（各自注入一次，之后走缓存）
  const ensureImg = () => injectModule(needFile(IMG_PATH, 'imgtools'), 'IMG_READY', 'IMG_VERSION');
  // human_spec 要用 imgtools 量参考图 ⇒ 链路里带上
  const ensurePipeline = () => injectModule(needFile(PIPELINE_PATH, 'pipeline'), 'PIPELINE_READY', 'PIPE_VERSION');
  const ensureAssembly = () => injectModule(needFile(ASSEMBLY_PATH, 'assembly'), 'ASSEMBLY_READY', 'ASSEMBLY_VERSION');
  const ensureBlockout = () => injectModule(needFile(BLOCKOUT_PATH, 'blockout'), 'BLOCKOUT_READY', 'BLOCKOUT_VERSION');
  const ensureVisualReport = () => injectModule(needFile(VISUALREPORT_PATH, 'visualreport'), 'VISUALREPORT_READY', 'VISUALREPORT_VERSION');
  const ensureShapesolve = () => injectModule(needFile(SHAPESOLVE_PATH, 'shapesolve'), 'SHAPESOLVE_READY', 'SHAPESOLVE_VERSION');
  const ensureEvalharness = () => injectModule(needFile(EVALHARNESS_PATH, 'evalharness'), 'EVALHARNESS_READY', 'EVALHARNESS_VERSION');
  const ensureSurface = () => injectModule(needFile(SURFACE_PATH, 'surface'), 'SURFACE_READY', 'SURFACE_VERSION');
  const ensureShape = async () => { await ensureVehicle();
    return injectModule(needFile(SHAPE_PATH, 'shapegen'), 'SHAPE_READY', 'SHAPE_VERSION'); };
  const ensureClearance = () => injectModule(needFile(CLEARANCE_PATH, 'clearance'), 'CLEARANCE_READY', 'CLEAR_VERSION');
  // vehicle_sections 要 imgtools 的 col_profile、panels 之后常配 clearance 验收 ⇒ 链路带上
  const ensureVehicle = async () => { await ensureImg(); await ensureClearance();
    return injectModule(needFile(VEHICLE_PATH, 'vehicle'), 'VEHICLE_READY', 'VEHICLE_VERSION'); };
  const ensureHuman = async () => { await ensureImg(); return injectModule(needFile(HUMAN_PATH, 'human'), 'HUMAN_READY', 'HUMAN_VERSION'); };
  const ensureFace = () => injectModule(needFile(FACE_PATH, 'faceeval'), 'FACE_READY', 'FACE_VERSION');
  const ensureCalib = async () => { await ensureGate(); return injectModule(needFile(CALIB_PATH, 'calib'), 'CALIB_READY', 'CALIB_VERSION'); };
  const ensureGate = async () => {
    for (const f of [ensureAudit, ensurePrint, ensureUv, ensureMaterial, ensureFix, ensureHuman, ensureAssembly]) {
      try { await f(); } catch (e) { /* 某个模块注入失败不该拦下整包：gate 会按门报"该门需要模块 X" */ }
    }
    return injectModule(needFile(GATE_PATH, 'gate'), 'GATE_READY', 'GATE_VERSION');
  };
  // 渲染 guard 必须把标记文件路径注入进去（Windows 形式；handler 闭包持有该命名空间）
  const ensureRenderGuard = () => injectModule(needFile(RENDER_GUARD_PATH, 'render_guard'), 'RENDER_GUARD_READY',
    'RG_VERSION', 'DSH_RENDER_FLAG_WIN = ' + JSON.stringify(RENDER_FLAG_WIN));

  async function sceneEpoch() {
    try {
      const out = await addon.send('execute_code', { code: KERNEL_BOOTSTRAP + '\nprint("EPOCH " + _dsh_epoch_json())' }, 20000);
      const t = (out && typeof out.result === 'string') ? out.result : '';
      const l = t.split('\n').filter((x) => x.indexOf('EPOCH ') === 0);
      return l.length ? JSON.parse(l[l.length - 1].slice('EPOCH '.length)) : null;
    } catch (e) { return null; }
  }
  return { MODULE_ATTR, readModuleSource, readScriptPath, preloadChunk, injectModule, needFile,
    ensureRunner, ensurePerf, ensureView, ensureContract, ensurePlanner, ensureTxn, ensureQc, ensureQcRender,
    ensurePreset, ensureAudit, ensureMontage, ensureMotion, ensureDeliver, ensureGenerator, ensureSculpt,
    ensureFix, ensureUv, ensurePrint, ensureSweep, ensureMaterial, ensureImg, ensurePipeline, ensureAssembly,
    ensureBlockout, ensureVisualReport, ensureShapesolve, ensureEvalharness, ensureSurface, ensureShape,
    ensureClearance, ensureVehicle, ensureHuman, ensureFace, ensureCalib, ensureGate, ensureRenderGuard, sceneEpoch };
}
