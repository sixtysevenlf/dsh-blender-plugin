// plan/EXT 前缀路由：把 op 分发到模块 api（T6）
// @imports
import { WIN_TMP, blenderJoin } from "../paths.js";
import { extractLoop } from "./output.js";
import { KERNEL_BOOTSTRAP } from "./kernel.js";


export interface PlanCallDeps {
  SESSION_NAME: string;
  addon: import("../transport/probes.js").AddonClient;
  ensureAssembly: () => Promise<unknown>; ensureAudit: () => Promise<unknown>; ensureBlockout: () => Promise<unknown>;
  ensureCalib: () => Promise<unknown>; ensureClearance: () => Promise<unknown>; ensureContract: () => Promise<unknown>;
  ensureDeliver: () => Promise<unknown>; ensureEvalharness: () => Promise<unknown>; ensureFace: () => Promise<unknown>;
  ensureFix: () => Promise<unknown>; ensureGate: () => Promise<unknown>; ensureGenerator: () => Promise<unknown>;
  ensureHuman: () => Promise<unknown>; ensureImg: () => Promise<unknown>; ensureMaterial: () => Promise<unknown>;
  ensureMontage: () => Promise<unknown>; ensureMotion: () => Promise<unknown>; ensurePipeline: () => Promise<unknown>;
  ensurePlanner: () => Promise<unknown>; ensurePrint: () => Promise<unknown>; ensureQc: () => Promise<unknown>;
  ensureQcRender: () => Promise<unknown>; ensureSculpt: () => Promise<unknown>; ensureShape: () => Promise<unknown>;
  ensureShapesolve: () => Promise<unknown>; ensureSurface: () => Promise<unknown>; ensureSweep: () => Promise<unknown>;
  ensureUv: () => Promise<unknown>; ensureVehicle: () => Promise<unknown>; ensureView: () => Promise<unknown>;
  ensureVisualReport: () => Promise<unknown>;
  jobStart: (opts: Record<string, unknown>) => Record<string, unknown>;
  readModuleSource: (name: string) => string;
}

export function createPlanCallInner(E: PlanCallDeps) {
  const { SESSION_NAME, addon, ensureAssembly, ensureAudit, ensureBlockout, ensureCalib, ensureClearance, ensureContract, ensureDeliver, ensureEvalharness, ensureFace, ensureFix, ensureGate, ensureGenerator, ensureHuman, ensureImg, ensureMaterial, ensureMontage, ensureMotion, ensurePipeline, ensurePlanner, ensurePrint, ensureQc, ensureQcRender, ensureSculpt, ensureShape, ensureShapesolve, ensureSurface, ensureSweep, ensureUv, ensureVehicle, ensureView, ensureVisualReport, jobStart, readModuleSource } = E;
  async function planCallInner(op: unknown, payload: unknown): Promise<unknown> {
    const o = String(op || 'status');
    const isPlan = o.startsWith('plan_');
    if (o === 'evidence') {
      await ensureView();
      // 契约层的 evidence 需要 view.path：这里补默认工作目录下的证据文件（与 view.py 的默认出图分开）
      const p: Record<string, unknown> = (payload !== null && typeof payload === 'object') ? payload as Record<string, unknown> : {};
      const viewRec: Record<string, unknown> = (p.view !== null && typeof p.view === 'object') ? p.view as Record<string, unknown> : {};
      if (!viewRec.path) viewRec.path = blenderJoin(WIN_TMP, 'dsh_evidence_' + SESSION_NAME.replace(/[^A-Za-z0-9_.-]/g, '_') + '.png');
      p.view = viewRec;
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
      const p: Record<string, unknown> = (payload !== null && typeof payload === 'object') ? payload as Record<string, unknown> : {};
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
    if (o === 'audit_clash_all' || o.indexOf('audit_clash_all_') === 0) {
      // P0：全对全分类扫描 —— 旧名 audit_clash_all 作为 asm_scan 的别名（不改变 audit_* 语义）
      await ensureAssembly();
      const body = 'print("LOOP " + K.dsh_assembly_api["dispatch"]("scan", _json.dumps(_json.loads('
        + JSON.stringify(JSON.stringify(payload || {})) + '))))';
      return extractLoop(await addon.send('execute_code', { code: KERNEL_BOOTSTRAP + '\nimport json as _json\n' + body }, 900000));
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
    // v0.9.6（上游整合 A1–A5）：五个新能力面 —— sculpt_* / fix_* / uv_* / print_* / sweep_*
    // 每个模块暴露 K.dsh_<x>_api["dispatch"](op, json_str)，与 audit/motion 同契约（返回 LOOP 行）。
    const EXT_ROUTES = [
      { p: 'gate_', n: 5, ensure: ensureGate, api: 'dsh_gate_api', ms: 1800000 },
      { p: 'img_', n: 4, ensure: ensureImg, api: 'dsh_img_api', ms: 300000 },
      { p: 'face_', n: 5, ensure: ensureFace, api: 'dsh_face_api', ms: 300000 },
      { p: 'human_', n: 6, ensure: ensureHuman, api: 'dsh_human_api', ms: 900000 },
      { p: 'clear_', n: 6, ensure: ensureClearance, api: 'dsh_clearance_api', ms: 600000 },
      { p: 'vehicle_', n: 8, ensure: ensureVehicle, api: 'dsh_vehicle_api', ms: 900000 },
      { p: 'shape_', n: 6, ensure: ensureShape, api: 'dsh_shape_api', ms: 900000 },
      { p: 'pipe_', n: 5, ensure: ensurePipeline, api: 'dsh_pipe_api', ms: 600000 },
      { p: 'asm_', n: 4, ensure: ensureAssembly, api: 'dsh_assembly_api', ms: 900000 },
      { p: 'blockout_', n: 9, ensure: ensureBlockout, api: 'dsh_blockout_api', ms: 900000 },
      { p: 'visualreport_', n: 13, ensure: ensureVisualReport, api: 'dsh_visualreport_api', ms: 600000 },
      { p: 'shapesolve_', n: 11, ensure: ensureShapesolve, api: 'dsh_shapesolve_api', ms: 900000 },
      { p: 'eval_', n: 5, ensure: ensureEvalharness, api: 'dsh_evalharness_api', ms: 900000 },
      { p: 'surface_', n: 8, ensure: ensureSurface, api: 'dsh_surface_api', ms: 900000 },
      { p: 'calib_', n: 6, ensure: ensureCalib, api: 'dsh_calib_api', ms: 1800000 },
      { p: 'material_', n: 9, ensure: ensureMaterial, api: 'dsh_material_api', ms: 900000 },
      { p: 'sculpt_', n: 7, ensure: ensureSculpt, api: 'dsh_sculpt_api', ms: 900000 },
      { p: 'fix_', n: 4, ensure: ensureFix, api: 'dsh_fix_api', ms: 600000 },
      { p: 'uv_', n: 3, ensure: ensureUv, api: 'dsh_uv_api', ms: 600000 },
      { p: 'print_', n: 6, ensure: ensurePrint, api: 'dsh_print_api', ms: 900000 },
      { p: 'sweep_', n: 6, ensure: ensureSweep, api: 'dsh_sweep_api', ms: 300000 },
    ];
    // P0/P1 别名：用户方案里的名字 → 模块正式 op（不新增 family；必须在 fix_/shape_ 前缀路由之前）
    if (o === 'fix_clash' || o === 'fix_interference') {
      await ensureAssembly();
      const sub = (o === 'fix_clash') ? 'repair' : 'fix_interference';
      const body = 'print("LOOP " + K.dsh_assembly_api["dispatch"](' + JSON.stringify(sub) + ', _json.dumps(_json.loads('
        + JSON.stringify(JSON.stringify(payload || {})) + '))))';
      return extractLoop(await addon.send('execute_code', { code: KERNEL_BOOTSTRAP + '\nimport json as _json\n' + body }, 900000));
    }
    // P1 别名：用户方案里的名字 → 模块正式 op（不新增 family）
    if (o === 'qc_visual_report') {
      await ensureVisualReport();
      const body = 'print("LOOP " + K.dsh_visualreport_api["dispatch"]("report", _json.dumps(_json.loads('
        + JSON.stringify(JSON.stringify(payload || {})) + '))))';
      return extractLoop(await addon.send('execute_code', { code: KERNEL_BOOTSTRAP + '\nimport json as _json\n' + body }, 600000));
    }
    if (o === 'shape_solve') {
      await ensureShapesolve();
      const body = 'print("LOOP " + K.dsh_shapesolve_api["dispatch"]("solve", _json.dumps(_json.loads('
        + JSON.stringify(JSON.stringify(payload || {})) + '))))';
      return extractLoop(await addon.send('execute_code', { code: KERNEL_BOOTSTRAP + '\nimport json as _json\n' + body }, 900000));
    }
    // P0：弱模型 flow op 别名 —— model_* 直接映射到 gate 的 preflight/checkpoint/deliver
    // （不新增 family；catalog 里以 gate_* 为正式名，别名只做通道兼容）
    if (o === 'model_preflight' || o === 'model_checkpoint' || o === 'model_deliver') {
      await ensureGate();
      const sub = o.slice('model_'.length);
      const body = 'print("LOOP " + K.dsh_gate_api["dispatch"](' + JSON.stringify(sub) + ', _json.dumps(_json.loads('
        + JSON.stringify(JSON.stringify(payload || {})) + '))))';
      return extractLoop(await addon.send('execute_code', { code: KERNEL_BOOTSTRAP + '\nimport json as _json\n' + body }, 1800000));
    }
    for (const ext of EXT_ROUTES) {
      if (o.indexOf(ext.p) === 0) {
        await ext.ensure();
        const body = 'print("LOOP " + K.' + ext.api + '["dispatch"](' + JSON.stringify(o.slice(ext.n)) + ', _json.dumps(_json.loads('
          + JSON.stringify(JSON.stringify(payload || {})) + '))))';
        return extractLoop(await addon.send('execute_code', { code: KERNEL_BOOTSTRAP + '\nimport json as _json\n' + body }, ext.ms));
      }
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
      // 路由契约测试发现：render_lock_status 直切会变成 qc_render 不认识的 lock_status（白名单里却算只读）。
      // qc_render 的锁查询 op 就叫 status（render_status 就是这么通的）=> 别名显式归一。
      const _rsub = o.slice(7) === 'lock_status' ? 'status' : o.slice(7);
      const body = 'print("LOOP " + K.dsh_qc_render_api["dispatch"](' + JSON.stringify(_rsub) + ', _json.dumps(_json.loads('
        + JSON.stringify(JSON.stringify(payload || {})) + '))))';
      return extractLoop(await addon.send('execute_code', { code: KERNEL_BOOTSTRAP + '\nimport json as _json\n' + body }, 900000));
    }
    await ensureContract();
    const body = 'print("LOOP " + K.dsh_contract_api["dispatch"](' + JSON.stringify(o) + ', _json.dumps(_json.loads('
      + JSON.stringify(JSON.stringify(payload || {})) + '))))';
    return extractLoop(await addon.send('execute_code', { code: KERNEL_BOOTSTRAP + '\nimport json as _json\n' + body }, 300000));
  }
  return { planCallInner };
}
