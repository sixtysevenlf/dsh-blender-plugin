import { backendPost } from '../host/http.js';
import { attachPaths, planImagePaths } from '../utils/image.js';
/** P1：plan op → 无头 worker 的模块 API（不给 GUI 也能跑；op 前缀路由 + 别名） */
export const PLAN_HEADLESS_MAP = [
    ['visualreport_', 'dsh_visualreport_api', 'visualreport,qc,imgtools'],
    ['shapesolve_', 'dsh_shapesolve_api', 'shapesolve,imgtools'],
    ['eval_', 'dsh_evalharness_api', 'evalharness,assembly,audit,contract,clearance,gate,imgtools,calib,mesh_fix'],
    ['surface_', 'dsh_surface_api', 'surface,audit'],
    ['blockout_', 'dsh_blockout_api', 'blockout,qc,qc_render,montage'],
    ['asm_', 'dsh_assembly_api', 'assembly,audit,contract,clearance,gate,mesh_fix,geo,txn'],
    ['img_', 'dsh_img_api', 'imgtools'],
    ['audit_', 'dsh_audit_api', 'audit'],
    ['qc_', 'dsh_qc_api', 'qc,imgtools'],
    ['gate_', 'dsh_gate_api', 'gate,audit'],
    ['shape_', 'dsh_shape_api', 'shapegen,imgtools'],
    ['vehicle_', 'dsh_vehicle_api', 'vehicle,audit,clearance,gate'],
    ['clear_', 'dsh_clearance_api', 'clearance'],
    ['deliver_', 'dsh_deliver_api', 'deliver,gate'],
    ['fix_', 'dsh_fix_api', 'mesh_fix'],
    ['uv_', 'dsh_uv_api', 'uv_tools'],
    ['print_', 'dsh_print_api', 'printcheck'],
    ['sculpt_', 'dsh_sculpt_api', 'sculpt'],
    ['sweep_', 'dsh_sweep_api', 'sweep'],
    ['material_', 'dsh_material_api', 'material'],
    ['motion_', 'dsh_motion_api', 'motion'],
    ['generator_', 'dsh_generator_api', 'generator'],
    ['face_', 'dsh_face_api', 'faceeval'],
    ['human_', 'dsh_human_api', 'human,imgtools']
];
export const PLAN_HEADLESS_ALIAS = {
    qc_visual_report: ['dsh_visualreport_api', 'report', 'visualreport,qc,imgtools'],
    shape_solve: ['dsh_shapesolve_api', 'solve', 'shapesolve,imgtools'],
    fix_clash: ['dsh_assembly_api', 'repair', 'assembly,audit,contract,clearance,gate,mesh_fix,geo,txn'],
    fix_interference: ['dsh_assembly_api', 'fix_interference', 'assembly,audit,contract,clearance,gate,mesh_fix,geo,txn'],
    audit_clash_all: ['dsh_assembly_api', 'scan', 'assembly,audit,contract,clearance,gate,mesh_fix,geo,txn'],
    model_preflight: ['dsh_gate_api', 'preflight', 'gate,audit'],
    model_checkpoint: ['dsh_gate_api', 'checkpoint', 'gate,txn'],
    model_deliver: ['dsh_gate_api', 'deliver', 'gate,audit,txn,deliver']
};
export function planHeadlessTarget(op) {
    const o = String(op || '');
    if (PLAN_HEADLESS_ALIAS[o]) {
        const a = PLAN_HEADLESS_ALIAS[o];
        return { api: a[0], sub: a[1], preload: a[2] };
    }
    for (const row of PLAN_HEADLESS_MAP) {
        const p = String(row[0]);
        if (o.indexOf(p) === 0)
            return { api: row[1], sub: o.slice(p.length), preload: row[2] };
    }
    return null;
}
/** 用持久 worker 执行一个 plan op（不依赖 GUI addon；模块由 preload 注入 K） */
export async function planViaWorker(ctx, port, op, payload, session) {
    const t = planHeadlessTarget(op);
    if (!t)
        return { ok: false, error: '该 plan op 没有无头映射（先 op="catalog" 查，或走 GUI 通道）' };
    const nm = session ? String(session) : 'plan-' + String(t.api).replace(/^dsh_|_api$/g, '');
    try {
        const st = await backendPost(port, '/worker', { op: 'start', name: nm, preload: t.preload, engine: 'workbench', warmup: false, isolated: true }, 120000);
        if (!st || st.ok !== true)
            return { ok: false, error: '无头 worker 启动失败：' + String((st && (st.error || st.raw)) || 'unknown') };
        const code = 'import json as _json\n_s = _json.dumps(_json.loads(' +
            JSON.stringify(JSON.stringify(payload || {})) +
            '))\n' +
            '_r = K.' +
            t.api +
            '["dispatch"](' +
            JSON.stringify(String(t.sub)) +
            ', _s)\n' +
            'print("HEADLESS " + (_r if isinstance(_r, str) else _json.dumps(_r, ensure_ascii=False)))';
        const r = await backendPost(port, '/worker', { op: 'exec', name: nm, code: code, isolated: true }, 900000);
        const rr = (r && r.result) || {};
        if (!r || r.ok !== true) {
            return {
                ok: false,
                error: '无头 plan 执行失败：' + String((r && (r.error || rr.error)) || 'unknown'),
                hint: String(rr.traceback || '').slice(-1200)
            };
        }
        return { ok: true, res: rr.result, via: 'worker:' + nm };
    }
    catch (e) {
        return { ok: false, error: '无头 plan 异常：' + String((e && e.message) || e) };
    }
}
/** plan 回执统一收尾：信封 + 附图（GUI/无头共用） */
export async function finishPlanResponse(ctx, op, res, argsIn) {
    const txt = typeof res === 'string' ? res : JSON.stringify(res, null, 1);
    const CAP = 12000;
    const env = planEnvelope(op, res);
    const attachWanted = argsIn && argsIn.attach_images !== undefined
        ? !!argsIn.attach_images
        : ['img_digest', 'qc_compare', 'visualreport_report', 'qc_visual_report', 'shapesolve_solve'].indexOf(op) >= 0;
    let images = [];
    let attachNote = '';
    if (attachWanted) {
        const att = await attachPaths(ctx, planImagePaths(res), 'plan-' + op.replace(/[^a-z0-9_]+/gi, '_'), 2);
        images = att.images;
        if (att.used.length)
            attachNote = String.fromCharCode(10) + '附图：' + String(att.used.length) + ' 张（' + att.used.join(' · ') + '）';
        if (att.failed.length)
            attachNote += String.fromCharCode(10) + '⚠ 部分图未附：' + att.failed.slice(0, 2).join(' · ');
    }
    const head = 'PLAN ' +
        op +
        ' ok · ' +
        String(txt.length) +
        ' chars · verdict=' +
        String(env.verdict) +
        (env.summary ? ' · ' + String(env.summary) : '') +
        attachNote +
        String.fromCharCode(10);
    return {
        text: head +
            (txt.length > CAP
                ? txt.slice(0, CAP) +
                    String.fromCharCode(10) +
                    '…[已截断，共 ' +
                    String(txt.length) +
                    ' 字符；结构化全量见后端 results/ 目录或用 headless(outJson=…) 落盘]'
                : txt),
        envelope: env,
        ...(images.length ? { images: images } : {})
    };
}
/** 弱模型契约 v2：verdict-first 信封（单行、≤2k）——plan 与 headless 共用口径 */
export function planEnvelope(op, res) {
    const r = res && typeof res === 'object' ? res : { value: res };
    const verdict = r.verdict || r.state || (r.ok === false ? 'fail' : r.ok === true ? (r.clean === false ? 'degraded' : 'pass') : null);
    let defects = [];
    for (const k of ['top_defects', 'offenders', 'defects', 'failures', 'errors']) {
        if (Array.isArray(r[k])) {
            defects = r[k];
            break;
        }
    }
    const limit = (t, n) => String(t == null ? '' : t).slice(0, n);
    const top = defects.slice(0, 5).map((d, i) => {
        if (!d || typeof d !== 'object')
            return { id: 'D' + String(i + 1), code: limit(d, 80) };
        return {
            id: d.id || 'D' + String(i + 1),
            code: limit(d.code || d.kind || d.class || d.error || 'issue', 60),
            severity: d.severity || null,
            object: d.object || d.a || d.name || null,
            measured: d.measured ||
                (d.volume_mm3 !== undefined ? { volume_mm3: d.volume_mm3 } : d.depth_mm !== undefined ? { depth_mm: d.depth_mm } : null),
            fix_hint: d.fix_hint || d.repair || null
        };
    });
    const next = (Array.isArray(r.next) ? r.next : Array.isArray(r.repairs) ? r.repairs : [])
        .slice(0, 3)
        .map((x) => typeof x === 'string'
        ? { op: limit(x, 120) }
        : { op: limit(x.op || x.name || 'next', 80), args: x.args || null, why: limit(x.why || '', 120) });
    const env = {
        contract: 'receipt.v2',
        op: op,
        verdict: verdict,
        summary: limit(r.summary || r.note || r.error || r.hint || '', 160),
        top_defects: top,
        next: next,
        images: planImagePaths(r).slice(0, 2),
        provenance: { ms: typeof r.ms === 'number' ? r.ms : null }
    };
    const counts = r.counts || (r.totals ? r.totals : null);
    if (counts && typeof counts === 'object')
        env.counts = counts;
    if (r.detail && typeof r.detail === 'object')
        env.detail = { path: r.detail.path || null, bytes: r.detail.bytes || null, id: r.detail.id || null };
    // 信封硬预算：超过 2k 就只保留最有用的三块（verdict / top3 / next2）
    try {
        if (JSON.stringify(env).length > 2000) {
            env.top_defects = top.slice(0, 3);
            env.next = next.slice(0, 2);
            delete env.counts;
            if (env.detail && env.detail.bytes && env.detail.bytes > 0) {
                /* keep path only */
            }
            else
                delete env.detail;
        }
    }
    catch (e) {
        /* ignore */
    }
    return env;
}
//# sourceMappingURL=headless-target.js.map