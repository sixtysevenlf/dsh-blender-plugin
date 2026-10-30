// plan 本地 op + 外部通路 + 渲染 guard + 声明式搜索（T6）
// @imports
import { catalogPayload, extractLoop } from "./output.mjs";
import { KERNEL_BOOTSTRAP } from "./kernel.mjs";
import { planOpSuggestion } from "../catalog.mjs";
import { wslToWin } from "../config.mjs";

import path from "node:path";
import { fileURLToPath } from "node:url";

const RUNTIME_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

export function createPlanDispatch(E) {
  const { RENDER_FLAG_WSL, RENDER_STALE_MS, addon, clearRenderFlag, ensurePerf, ensureQc, ensureRenderGuard, ensureRunner, ensureView, loopBoard, loopExport, loopStart, loopStatus, loopStop, planCallInner, readRenderFlag } = E;
  async function perfCall(op, payload) {
    await ensurePerf();
    const body = 'import json as _json' + '\n' + 'print("LOOP " + K.dsh_perf_api["dispatch"](' + JSON.stringify(op) + ', ' + JSON.stringify(JSON.stringify(payload === undefined ? {} : payload)) + '))';
    return extractLoop(await addon.send('execute_code', { code: KERNEL_BOOTSTRAP + '\n' + body }, 300000));
  }
  /** v0.9.6（D1）：把模块返回里的 unknown op 变成"一步改对"——补 did_you_mean + 最小骨架 */
  function enhancePlanResult(out, o) {
    let obj = out;
    let asString = false;
    if (typeof out === 'string') {
      try { obj = JSON.parse(out); asString = true; } catch (e) { return out; }
    }
    if (!obj || typeof obj !== 'object' || obj.ok !== false) return out;
    const err = String(obj.error || '');
    if (!/unknown|不认识|not found|参数不匹配/i.test(err)) return out;
    const near = planOpSuggestion(o);
    if (near) {
      obj.did_you_mean = near;
      obj.hint = '回执里有 did_you_mean：直接用这个名字重发，别猜参数——骨架见 did_you_mean.skeleton。';
      if (obj.hint_shown === undefined) obj.hint_shown = true;
    }
    return asString ? JSON.stringify(obj) : obj;
  }

  /**
   * 契约层 / 规划器统一调用：op = 契约 op（status、register_component、check_envelope、destructive_guard、verify、flip …）
   * 或 plan_<op>（load/validate/order/build/graph/status/help）。
   * 两个 python 模块都提供 dispatch(op, args)，因此这里只发 {op, args}。
   *
   * v0.9.6（D1 · 可发现性）：op="catalog" 在**本地**直接返回目录（不占 Blender 往返），
   * 其余结果统一过 enhancePlanResult（未知 op → 最近邻 + 骨架）。
   */
  async function planCall(op, payload) {
    const o = String(op || 'status');
    if (o === 'catalog' || o === 'catalog_help' || o === 'help_all') return catalogPayload(payload);
    // v0.9.6（A7）：渲染状态跟踪的 op 在**本地**处理（读磁盘标记，不占 Blender 往返）
    // P1-③：外部通路（资产门 glTF-Validator / 第二交通路 open3d）—— 本地 spawn，不占 Blender
    if (o === 'gltf_validate' || o === 'ext_mesh_check') return await extCheckOp(o, payload);
    if (o === 'render_state' || o === 'render_wait' || o === 'render_reset'
        || o === 'render_guard_install' || o === 'render_guard_selftest') {
      return await renderGuardOp(o, payload);
    }
    // P2 · 声明式搜索：目标 + 参数区间 → 内环全流程（spec 由 shape_search 生成；本地编排，不占额外往返）
    if (o === 'shape_search') return await shapeSearchOp(payload);
    const out = await planCallInner(o, payload);
    try { return enhancePlanResult(out, o); } catch (e) { return out; }
  }

  async function extCheckOp(op, payload) {
    const p = (payload && typeof payload === 'object') ? payload : {};
    const cp = await import('node:child_process');
    const extDir = path.join(RUNTIME_DIR, 'ext');
    const winPath = String(p.path || '');
    const wslPath = /^[A-Za-z]:[\\/]/.test(winPath)
      ? ('/mnt/' + winPath[0].toLowerCase() + winPath.slice(2).replace(/\\/g, '/'))
      : winPath;
    const runner = path.join(extDir, op === 'gltf_validate' ? 'gltf_validate_runner.mjs' : 'open3d_check.py');
    // 后端跑在 WSL 时 node 看到的是 Linux 文件系统：**先给 WSL 路径**；在 Windows 时反过来。
    const isWin = process.platform === 'win32';
    const first = isWin ? winPath : wslPath;
    const second = isWin ? wslPath : winPath;
    const cands = op === 'gltf_validate'
      ? [['node', runner, first], ['node', runner, second], ['wsl.exe', '-e', 'node', runner, wslPath]]
      : [[path.join(extDir, 'venv', 'bin', 'python'), runner, first],
         [path.join(extDir, 'venv', 'bin', 'python'), runner, second],
         ['wsl.exe', '-e', path.join(extDir, 'venv', 'bin', 'python'), runner, wslPath],
         [path.join(extDir, 'venv', 'Scripts', 'python.exe'), runner, winPath]];
    const tried = [];
    let raw = null, used = null;
    for (const c of cands) {
      try {
        const r = cp.spawnSync(c[0], c.slice(1), { encoding: 'utf8', timeout: 120000, windowsHide: true });
        const out = String((r.stdout || '') + (r.stderr || ''));
        tried.push({ cmd: c.slice(0, 2).join(' '), status: r.status, err: (r.error && r.error.message) ? String(r.error.message).slice(0, 80) : null });
        const m = out.match(/^(RAW|EXT) (.+)$/m);
        if (m) { raw = m[2]; used = c.slice(0, 2).join(' '); break; }
      } catch (e) {
        tried.push({ cmd: c.slice(0, 2).join(' '), err: String(e && e.message).slice(0, 80) });
      }
    }
    if (!raw) {
      // 安装提示必须是**可直接粘的绝对路径**：旧版写成相对路径 'dsh-blender-plugin/runtime/ext/venv'，
      // 用户若正好在 runtime/ext 里执行，会再套一层 runtime/ext/dsh-blender-plugin/...（现场踩过）。
      const extAbs = path.resolve(extDir);
      const installCmd = op === 'gltf_validate'
        ? 'cd ' + (isWin ? '/d "' : '"') + extAbs + '" && npm i gltf-validator'
        : (isWin ? 'cd /d "' + extAbs + '" && py -3 -m venv venv && venv\\Scripts\\pip install open3d'
                 : 'cd "' + extAbs + '" && python3 -m venv venv && venv/bin/pip install open3d');
      return { ok: false, installed: false, path: winPath, tried: tried,
               install: installCmd,
               hint: '依赖必须装在 ' + extAbs + ' 下（上面那条自带绝对路径与 cd，在哪个目录执行都一样）；'
                     + '两种解释器都试过了 —— 后端在 Windows 时会用 wsl.exe 调 Linux venv' };
    }
    let parsed = null;
    try { parsed = JSON.parse(raw); } catch (e) { parsed = { parse_error: String(e).slice(0, 120), raw: raw.slice(0, 400) }; }
    if (op === 'gltf_validate') {
      const iss = (parsed && parsed.issues) || {};
      const msgs = (iss.messages || []).slice(0, 12).map((m) => ({ code: m.code, severity: m.severity, pointer: m.pointer, message: String(m.message || '').slice(0, 160) }));
      return { ok: !!(parsed && parsed.issues && parsed.issues.numErrors === 0), tool: 'gltf-validator',
               validatorVersion: parsed && parsed.validatorVersion, path: winPath,
               errors: iss.numErrors, warnings: iss.numWarnings, infos: iss.numInfos, hints: iss.numHints,
               messages: msgs, used: used, info: parsed && parsed.info ? { version: parsed.info.version, generator: parsed.info.generator, drawCallCount: parsed.info.drawCallCount, totalVertexCount: parsed.info.totalVertexCount, totalTriangleCount: parsed.info.totalTriangleCount, maxUVs: parsed.info.maxUVs } : null,
               note: '规则码（messages[].code）可直接写进 gate spec 的 pass_if；numErrors==0 才算过' };
    }
    return Object.assign({ tool: 'open3d', used: used, tried: tried }, parsed || {});
  }

  /** v0.9.6（A7）：渲染 guard 的本地 op（读/等/清标记；install/selftest 走 Blender 一次） */
  async function renderGuardCall(modOp, payload) {
    await ensureRenderGuard();
    const body = 'print("LOOP " + K.dsh_render_guard["dispatch"](' + JSON.stringify(String(modOp)) + ', _json.dumps(_json.loads('
      + JSON.stringify(JSON.stringify(payload || {})) + '))))';
    return extractLoop(await addon.send('execute_code', { code: KERNEL_BOOTSTRAP + '\nimport json as _json\n' + body }, 120000));
  }
  async function renderGuardOp(op, payload) {
    const p = (payload && typeof payload === 'object') ? payload : {};
    if (op === 'render_state') {
      const g = readRenderFlag();
      const out = { ok: true, busy: !!(g.present && !g.stale), stale: !!g.stale, ageMs: g.ageMs,
                    flag: g.info, flagPath: wslToWin(RENDER_FLAG_WSL), staleMs: RENDER_STALE_MS,
                    note: 'busy=true ⇒ 主线程命令会被秒拒；用 op="render_wait" 等它，或 op="render_reset" 清陈标记' };
      if (p.probe) {
        try { out.blender = await renderGuardCall('state', {}); }
        catch (e) { out.probe_error = String((e && e.message) || e).slice(0, 200); }
      }
      return out;
    }
    if (op === 'render_wait') {
      const cap = Math.max(1000, Math.min(600000, Number(p.timeout_ms) || 120000));
      const t0 = Date.now();
      for (;;) {
        const g = readRenderFlag();
        if (!g.present || g.stale) {
          return { ok: true, idle: true, waitedMs: Date.now() - t0, stale_cleared: !!g.stale, last: g.info };
        }
        if (Date.now() - t0 >= cap) {
          return { ok: false, idle: false, timeout: true, waitedMs: Date.now() - t0, flag: g.info,
                   hint: '仍在渲染：长渲染请一开始就用 blender_rt_job(op="start")，这样它是独立进程、不占主线程' };
        }
        await new Promise((r) => setTimeout(r, 400));
      }
    }
    if (op === 'render_reset') {
      const had = readRenderFlag();
      const removed = clearRenderFlag();
      let blender = null;
      try { blender = await renderGuardCall('clear', { why: String(p.why || 'reset') }); }
      catch (e) { blender = { ok: false, error: String((e && e.message) || e).slice(0, 160) }; }
      return { ok: true, cleared: removed, previous: had.info, blender: blender,
               note: '只在"标记是陈的/渲染其实已崩"时用；真在渲染时清了会让写命令排队' };
    }
    if (op === 'render_guard_install') {
      const r = await renderGuardCall('install', { force: !!p.force });
      return { ok: true, install: r, flagPath: wslToWin(RENDER_FLAG_WSL) };
    }
    // render_guard_selftest
    return await renderGuardCall('selftest', {});
  }

  const SHAPE_SEARCH_SKELETON = 'blender_rt_plan(op="shape_search", args={objective:"silhouette_iou", '
    + 'ref:"D:/ref/side.png", view:{from:[0,-6,1.0], look_at:[0,0,0.5], lens:50}, '
    + 'apply:"ob.location.y = p[\'dy_mm\']/1000.0\\n    ob.location.z = p[\'dz_mm\']/1000.0\\n'
    + '    bpy.context.view_layer.update()", '
    + 'params:{dy_mm:{min:-30,max:30,step:5}, dz_mm:{min:-20,max:20,step:5}}, '
    + 'iterations:200, budget_ms:30000, top_k:10})';

  /** Python 字面量（JSON 的 true/false/null 在 Python 里不是这个写法 —— 直接 JSON.stringify 会 NameError） */
  function pyLit(v) {
    if (v === null || v === undefined) return 'None';
    if (typeof v === 'boolean') return v ? 'True' : 'False';
    if (typeof v === 'number') return isFinite(v) ? String(v) : 'None';
    if (typeof v === 'string') return JSON.stringify(v);
    if (Array.isArray(v)) return '[' + v.map(pyLit).join(', ') + ']';
    if (typeof v === 'object') return '{' + Object.keys(v).map((k) => JSON.stringify(k) + ': ' + pyLit(v[k])).join(', ') + '}';
    return JSON.stringify(String(v));
  }
  const pyIndent = (src, pad) => String(src).split('\n').map((l) => (l.length ? pad + l : l)).join('\n');

  /** 一个轴（参数维度）的候选值：enum 直接取 values；range 按 step 切（没给 step 就等分 10 档） */
  function searchAxisValues(spec0, cap) {
    if (spec0.kind === 'enum') return spec0.values.slice(0, cap);
    const out = [];
    const step = spec0.step > 0 ? spec0.step : (spec0.max - spec0.min) / 9;
    if (!(step > 0)) return [spec0.min];
    for (let v = spec0.min; v <= spec0.max + 1e-12 && out.length < cap; v += step) {
      out.push(spec0.integer ? Math.round(v) : Number(v.toPrecision(12)));
    }
    if (out.length < cap && out[out.length - 1] !== spec0.max) out.push(spec0.max);
    return out.length ? out : [spec0.min];
  }

  async function shapeSearchOp(payload) {
    const p = (payload && typeof payload === 'object' && !Array.isArray(payload)) ? payload : {};
    const OBJECTIVES = ['silhouette_iou', 'profile_err', 'aabb_err'];
    const objective = String(p.objective || 'silhouette_iou');
    if (OBJECTIVES.indexOf(objective) < 0) {
      return { ok: false, op: 'shape_search', stage: 'validate', error: 'objective 只支持 ' + OBJECTIVES.join(' / '),
               got: objective, skeleton: SHAPE_SEARCH_SKELETON };
    }
    const rawParams = p.params;
    if (!rawParams || typeof rawParams !== 'object' || Array.isArray(rawParams) || Object.keys(rawParams).length === 0) {
      return { ok: false, op: 'shape_search', stage: 'validate',
               error: 'params 必填：{参数名: {min,max(,step)} 或 {values:[...]}}',
               got: (rawParams === undefined ? null : rawParams), skeleton: SHAPE_SEARCH_SKELETON };
    }
    const norm = {};
    const problems = [];
    for (const key of Object.keys(rawParams)) {
      const v = rawParams[key];
      if (!v || typeof v !== 'object') { problems.push(key + ': 要 {min,max} 或 {values:[...]}'); continue; }
      if (Array.isArray(v.values)) {
        if (!v.values.length) { problems.push(key + ': values 为空'); continue; }
        norm[key] = { kind: 'enum', values: v.values.slice(0, 500) };
        continue;
      }
      const lo = Number(v.min), hi = Number(v.max);
      if (!isFinite(lo) || !isFinite(hi) || hi < lo) { problems.push(key + ': min/max 不合法（' + String(v.min) + ',' + String(v.max) + '）'); continue; }
      const step = Number(v.step);
      norm[key] = { kind: 'range', min: lo, max: hi, step: (isFinite(step) && step > 0) ? step : 0, integer: !!v.integer };
    }
    if (problems.length) return { ok: false, op: 'shape_search', stage: 'validate', error: '参数区间有问题', problems: problems, skeleton: SHAPE_SEARCH_SKELETON };

    const iterationsReq = Math.max(1, Math.min(200000, Number(p.iterations) || 200));
    const budgetMs = Math.max(1000, Math.min(600000, Number(p.budget_ms) || 30000));
    const topK = Math.max(0, Math.min(200, p.top_k === undefined ? 10 : Number(p.top_k)));
    const patience = Math.max(0, Number(p.patience) || 0);
    const minimize = p.minimize === undefined ? true : !!p.minimize;
    const interval = Math.max(0, Number(p.interval) || 0);
    const measureEvery = Math.max(1, Number(p.measure_every) || 1);

    const axes = Object.keys(norm).map((k) => ({ k: k, spec: norm[k], vals: searchAxisValues(norm[k], 600) }));
    let combos = 1;
    for (const a of axes) combos *= a.vals.length;
    let strategy = String(p.strategy || 'auto');
    if (strategy === 'auto') strategy = combos <= iterationsReq ? 'grid' : 'random';
    if (strategy !== 'grid' && strategy !== 'random') {
      return { ok: false, op: 'shape_search', stage: 'validate', error: 'strategy 只支持 auto / grid / random', got: strategy,
               skeleton: SHAPE_SEARCH_SKELETON };
    }
    if (strategy === 'grid' && combos > 20000) {
      return { ok: false, op: 'shape_search', stage: 'validate',
               error: 'grid 组合数 ' + combos + ' 太大（>20000）→ 用 strategy:"random" 或缩小 step', combos: combos,
               skeleton: SHAPE_SEARCH_SKELETON };
    }
    const iterations = strategy === 'grid' ? Math.min(iterationsReq, combos) : iterationsReq;

    const renderObjective = objective !== 'aabb_err';
    const views = [];
    if (Array.isArray(p.views) && p.views.length) { for (const v of p.views) views.push(v); }
    else if (p.view && typeof p.view === 'object') views.push(p.view);
    const ref = p.ref ? String(p.ref) : '';
    const refBox = (p.ref_box === undefined || p.ref_box === null) ? null : p.ref_box;
    const bins = Math.max(2, Number(p.bins) || 24);
    const target = p.target ? String(p.target) : '';
    const targetSize = Array.isArray(p.target_size) ? p.target_size.slice(0, 3) : null;
    const warnings = [];
    if (renderObjective) {
      if (!views.length) return { ok: false, op: 'shape_search', stage: 'validate', error: objective + ' 需要 view 或 views（机位）', skeleton: SHAPE_SEARCH_SKELETON };
      if (!ref) return { ok: false, op: 'shape_search', stage: 'validate', error: objective + ' 需要 ref（参考图路径）', skeleton: SHAPE_SEARCH_SKELETON };
    } else {
      if (!target) return { ok: false, op: 'shape_search', stage: 'validate', error: 'aabb_err 需要 target（对象名）', skeleton: SHAPE_SEARCH_SKELETON };
      if (!targetSize || targetSize.length !== 3) return { ok: false, op: 'shape_search', stage: 'validate', error: 'aabb_err 需要 target_size:[x,y,z]', skeleton: SHAPE_SEARCH_SKELETON };
    }
    const applyBody = p.apply ? String(p.apply) : '';
    if (renderObjective && !applyBody && !p.measure) {
      return { ok: false, op: 'shape_search', stage: 'validate',
               error: '渲染类目标需要 apply：每轮把 ns["params"] 落到场景上的代码（改完记得 view_layer.update()）',
               skeleton: SHAPE_SEARCH_SKELETON };
    }
    if (!applyBody && !p.measure) warnings.push('没有给 apply：每轮只改 ns["params"]，场景不动 —— 目标必须是纯几何量（如 aabb_err）');

    const setupSrc = (function () {
      /**
       * 代码块归一化（实测踩过：apply 写成"首行顶格 + 续行缩进"时，pyIndent 会把续行多加 4 格
       * ⇒ 生成的 Python 直接 IndentationError；只做字符串断言抓不到，tests/shape_search_selftest.mjs
       * 里有一条"用 python3 真编译"的回归）。
       * 两段式：① 按**首行**缩进整体去缩进；② 续行的**公共**缩进再收一次（保留嵌套块的相对层次）。
       */
      const normBody = (src) => {
        const lines = String(src).replace(/\t/g, '    ').split('\n');
        const ind = (l) => l.match(/^ */)[0].length;
        const firstIdx = lines.findIndex((l) => l.trim());
        if (firstIdx < 0) return lines.join('\n');
        const i0 = ind(lines[firstIdx]);
        let out = (i0 > 0)
          ? lines.map((l, k) => ((k === firstIdx || !l.trim()) ? (k === firstIdx ? l.slice(i0) : l) : l.slice(Math.min(i0, ind(l)))))
          : lines.slice();
        const tail = out.slice(firstIdx + 1).filter((l) => l.trim());
        const iTail = tail.length ? Math.min.apply(null, tail.map(ind)) : 0;
        if (iTail > 0) out = out.map((l, k) => ((k > firstIdx && l.trim()) ? l.slice(iTail) : l));
        return out.join('\n');
      };
      const parts = ['import bpy', 'import math', 'import random'];
      if (p.setup) parts.push(normBody(p.setup));
      parts.push(applyBody ? ('def apply(p):\n' + pyIndent(normBody(applyBody), '    ')) : 'def apply(p):\n    pass');
      return parts.join('\n') + '\n';
    })();
    const stepSrc = (function () {
      if (strategy === 'grid') {
        const axesLit = '[' + axes.map((a) => '[' + JSON.stringify(a.k) + ', ' + pyLit(a.vals) + ']').join(', ') + ']';
        return '_axes = ' + axesLit + '\n_i = ns["i"] % ' + String(combos) + '\n_p = {}\n'
          + 'for _k, _vs in _axes:\n    _p[_k] = _vs[_i % len(_vs)]\n    _i //= len(_vs)\n'
          + 'ns["params"] = _p\n';
      }
      const lines = axes.map((a) => {
        if (a.spec.kind === 'enum') return '    ' + JSON.stringify(a.k) + ': random.choice(' + pyLit(a.vals) + ')';
        if (a.spec.integer) return '    ' + JSON.stringify(a.k) + ': int(round(random.uniform(' + a.spec.min + ', ' + a.spec.max + ')))';
        return '    ' + JSON.stringify(a.k) + ': random.uniform(' + a.spec.min + ', ' + a.spec.max + ')';
      });
      return 'ns["params"] = {\n' + lines.join(',\n') + '\n}\n';
    })();
    const measureSrc = (function () {
      if (p.measure) return (applyBody ? 'apply(ns["params"])\n' : '') + String(p.measure) + '\n';
      const lines = [];
      if (applyBody) lines.push('apply(ns["params"])');
      if (objective === 'aabb_err') {
        lines.push('ns["score"] = K.dsh_measure["aabb_err"](' + JSON.stringify(target) + ', ' + pyLit(targetSize) + ')');
      } else {
        const fn = objective === 'profile_err' ? 'profile_err' : 'silhouette_iou';
        const extra = (fn === 'profile_err') ? (', ' + String(bins)) : '';
        const call = (viewLit) => 'K.dsh_measure[' + JSON.stringify(fn) + '](' + viewLit + ', ' + JSON.stringify(ref) + ', ' + pyLit(refBox) + extra + ')';
        if (views.length === 1) {
          lines.push('ns["score"] = ' + call(pyLit(views[0])));
        } else {
          lines.push('_vs = ' + pyLit(views));
          lines.push('_ss = [' + call('_v') + ' for _v in _vs]');
          lines.push('ns["score"] = sum(_ss) / float(len(_ss))');
          lines.push('ns["metrics"] = {"per_view": _ss}');
        }
      }
      return lines.join('\n') + '\n';
    })();

    const spec = { setup: setupSrc, step: stepSrc, measure: measureSrc,
                   iterations: iterations, budget_ms: budgetMs, interval: interval, measure_every: measureEvery,
                   minimize: minimize, top_k: topK, patience: patience, redraw_every: 0 };
    const specEcho = { objective: objective, strategy: strategy, combos: combos, iterations: iterations,
                       budget_ms: budgetMs, minimize: minimize, top_k: topK, patience: patience,
                       param_names: Object.keys(norm), views: views.length, ref: (ref || null), target: (target || null),
                       restore: String(p.restore || 'best') };
    if (p.dry_run === true) {
      return { ok: true, op: 'shape_search', dry_run: true, spec_echo: specEcho, spec: spec, warnings: warnings,
               note: 'dry_run：只校验并回 spec，没有启动内环（想手跑就把它喂给 blender_rt_loop(op="start", spec=…)）' };
    }

    try {
      await ensureRunner();
      if (renderObjective) { await ensureQc(); await ensureView(); }
    } catch (e) {
      return { ok: false, op: 'shape_search', stage: 'preload',
               error: '注入 runner/qc/view 失败：' + String((e && e.message) || e).slice(0, 240),
               hint: 'GUI Blender 连着吗？本 op 只在 GUI 会话有效（无头下 bpy.app.timers 不触发）' };
    }
    let started = null;
    try { started = await loopStart(spec); }
    catch (e) { return { ok: false, op: 'shape_search', stage: 'start', error: String((e && e.message) || e).slice(0, 300), spec_echo: specEcho }; }
    if (!started || started.ok === false) {
      return { ok: false, op: 'shape_search', stage: 'start', spec_echo: specEcho,
               error: (started && (started.err || started.error)) || '内环启动失败', detail: started || null,
               hint: '若提示 loop already running：先 blender_rt_loop(op="status") 看上一轮，必要时 op="stop"' };
    }

    const waitCap = Math.max(5000, Math.min(600000, Number(p.wait_ms) || (budgetMs + 20000)));
    const t0 = Date.now();
    let st = null, timedOut = false, pollError = null;
    try {
      st = await loopStatus(8);
      while (st && st.running === true) {
        if (Date.now() - t0 > waitCap) {
          timedOut = true;
          await loopStop();
          await new Promise((r) => setTimeout(r, 300));
          st = await loopStatus(8);
          break;
        }
        await new Promise((r) => setTimeout(r, 300));
        st = await loopStatus(8);
      }
    } catch (e) { pollError = String((e && e.message) || e).slice(0, 300); }

    let board = null, exported = null;
    try { board = await loopBoard(topK > 0 ? topK : 10, false); } catch (e) { board = null; }
    if (p.export) {
      try { exported = await loopExport({ path: String(p.export), top: 1, includeVariants: false, note: 'shape_search ' + objective }); }
      catch (e) { exported = { ok: false, error: String((e && e.message) || e).slice(0, 200) }; }
    }

    const best = (st && st.best) || (board && board.best) || null;
    let applied = null;
    if (best && String(p.restore || 'best') !== 'none' && applyBody) {
      try {
        const body = 'import json as _json\n'
          + 'try:\n'
          + pyIndent(setupSrc, '    ') + '\n'
          + '    apply(' + pyLit(best.params || {}) + ')\n'
          + '    print("SEARCH_APPLIED " + _json.dumps({"ok": True, "params": _json.loads('
          + JSON.stringify(JSON.stringify(best.params || {})) + ')}))\n'
          + 'except Exception as _e:\n'
          + '    print("SEARCH_APPLIED " + _json.dumps({"ok": False, "error": str(_e)[:200]}))\n';
        const out = await addon.send('execute_code', { code: KERNEL_BOOTSTRAP + '\n' + body }, 60000);
        const raw = (out && typeof out.result === 'string') ? out.result : '';
        const mm = raw.match(/SEARCH_APPLIED (\{[\s\S]*?\})\s*$/m);
        applied = mm ? JSON.parse(mm[1]) : { ok: false, raw_tail: raw.slice(-200) };
      } catch (e) { applied = { ok: false, error: String((e && e.message) || e).slice(0, 200) }; }
    } else if (best && String(p.restore || 'best') === 'none') {
      applied = { ok: false, skipped: true, note: 'restore:"none" —— 场景仍停在最后一次迭代的参数上，best 在回执里' };
    }

    const nextSteps = [];
    if (best) nextSteps.push('换另一条通路复核（内环只优化你写的 score）：blender_rt_plan(op="qc_render_views", args={views:[…], ref_path:"…"}) + op="audit_measure" + blender_rt_see');
    if (!p.export) nextSteps.push('要落可复现脚本：本 op 加 export:"D:/out/best_fit.py"（或 blender_rt_loop(op="export", path=…)）');
    if (objective === 'aabb_err') nextSteps.push('aabb_err 只量包围盒三轴误差：形状/轮廓差异它看不见，必须配 qc_render_views 图上对拍');
    if (objective !== 'aabb_err') nextSteps.push('单视轮廓 IoU 是必要不充分（填满轮廓就能刷高）→ 再配间隙/尺寸/特征线证据');
    if (timedOut) nextSteps.push('wait_ms 到点已急停：结果仍在（best/board 有效），需要更多迭代就加大 budget_ms 后重跑');

    return { ok: !!(best), op: 'shape_search', spec_echo: specEcho,
             stop_reason: (st && st.stop_reason) || (timedOut ? 'wait_timeout' : null),
             timed_out: timedOut, iterations_done: st ? st.i : null, tps: st ? st.tps : null, elapsed_ms: st ? st.elapsed_ms : null,
             best: best, board: board ? board.board : null,
             loop_error: st ? st.error : null, poll_error: pollError,
             applied: applied, exported: exported, warnings: warnings, next_steps: nextSteps,
             note: '声明式搜索：本 op 生成 spec 后跑 Blender 侧内环（bpy.app.timers，只在 GUI 会话有效）；内环结束时场景停在最后一次迭代，已按 restore 口径处理' };
  }
  return { perfCall, enhancePlanResult, planCall };
}
