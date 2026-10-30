// server.mjs 兼容面：metrics/status/act/cmd/loop/frame/view/hostSceneItems/start/stop（T6）
// @imports
import { diagnose, freshPing, protocolInfo, tcpProbe } from "../transport/probes.js";
import { FLAGS_CODE, REGION_CODE } from "./flags.js";
import { extractLoop, extractTag, jsonFromStdout } from "./output.js";
import { PLUGIN_VERSION, RUNTIME_DIR, engineProvenance, runtimeFingerprint } from "./evidence.js";
import { winToWsl, wslToWin } from "../config.js";
import { KERNEL_BOOTSTRAP } from "./kernel.js";
import { ACT_WRAPPER, LIVE_PNG_WIN, LIVE_PNG_WSL, VIEW_PNG_WIN } from "../paths.js";
import { enrichError } from "./errors.js";
import { COMMAND_CATALOG, planOpCrossChannelHint } from "../catalog.js";
import fs from "node:fs";
import { errorMessage } from "./json.js";
import type { AddonClient } from "../transport/probes.js";
import type { JobRecord } from "../execution/jobs.js";

export interface CompatMetrics {
  [k: string]: unknown;
  lastCmdAt: number | null;
  views: number;
}

export interface ServerCompatDeps {
  LEDGER: string;
  RESULTS_DIR: string;
  SESSION_NAME: string;
  TRAJ_DIR: string;
  TRAJ_FULL: boolean;
  TRAJ_OFF: boolean;
  addon: AddonClient;
  clearWorkerReaper: () => void;
  ensureRunner: () => Promise<unknown>;
  ensureView: () => Promise<unknown>;
  jobSnapshot: (j: JobRecord) => Record<string, unknown>;
  jobs: Map<string, JobRecord>;
  killWorkers: () => void;
  ledgerList: (limit?: number) => Array<Record<string, unknown>>;
  ledgerState: () => Record<string, unknown>;
  metrics: CompatMetrics;
  perfCall: (op: string, payload?: unknown) => Promise<unknown>;
  runSnapshot: (r: Record<string, unknown>) => Record<string, unknown>;
  runs: Map<string, unknown>;
}


export function createPublicMethods(E: ServerCompatDeps) {
  const { LEDGER, RESULTS_DIR, SESSION_NAME, TRAJ_DIR, TRAJ_FULL, TRAJ_OFF, addon, clearWorkerReaper, ensureRunner, ensureView, jobSnapshot, jobs, killWorkers, ledgerList, ledgerState, metrics, runSnapshot, runs } = E;
  return ({
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
        return { ok: false, addon: d, metrics: this.metrics(), error: errorMessage(e) };
      }
    },
    /** 3D 视口区域 rect / 窗口尺寸 / 着色模式 */
    async status() {
      // v0.9.3：**本地信息不该被「Blender 没连上」挡住** —— 旧版这里第一句就 throw，
      // 于是 workDir 可写性 / 台账 / 版本自证在没连 addon 时全看不到（恰恰是最需要它们的时候）。
      let region = null, addonError = null;
      try { const out = await addon.send('execute_code', { code: REGION_CODE }); region = jsonFromStdout(out); }
      catch (e) { addonError = errorMessage(e).slice(0, 300); }
      // v0.8.10（A0/D4）：状态里带插件版本 + runtime 指纹 + 最近运行台账
      return { region: region, addonError: addonError,
               plugin: { version: PLUGIN_VERSION, runtimeDir: wslToWin(RUNTIME_DIR), runtime: runtimeFingerprint(),
                         // v0.9.6（D3）：加载版本自证 —— runtime 指纹已从 size-mtime 换成**内容哈希**，
                         // 这里再给 loaded/current 对拍结论（stale=true → 磁盘已改、本进程是旧一代）
                         provenance: engineProvenance({}) },
               // v0.9.1（93-E2）：会话名 —— 多会话/多成员并存时，默认产物名与 evidence 路径按它隔离
               session: SESSION_NAME,
               resultsDir: wslToWin(RESULTS_DIR),
               trajectory: { dir: wslToWin(TRAJ_DIR), off: TRAJ_OFF, full: TRAJ_FULL,
                             note: 'v0.9.0（#15）：每次顶层调用一行 JSONL（route/op/ms/ok/参数摘要）；DSH_TRAJ=0 关，DSH_TRAJ_FULL=1 记更多参数' },
               runs: Array.from(runs.values()).slice(-5).map((r) => runSnapshot(r as Record<string, unknown>)),
               jobs: Array.from(jobs.values()).slice(-5).map((j) => jobSnapshot(j)),
               // v0.9.3（D4 同源）：台账/工作目录的**可写性**必须自证 —— 旧版写失败是静默的
               ledger: Object.assign({ path: wslToWin(LEDGER), pathWsl: LEDGER,
                                       recent: ledgerList(5).map((x: Record<string, unknown>) => ({ id: x.id, kind: x.kind || 'job', status: x.status, startedAt: x.startedAt, ms: x.ms })) },
                                     ledgerState()) };
    },
    /**
     * 执行 Python（持久内核 K）。v0.7.0 起：异常也回传 **partial stdout / stderr / traceback**
     * （用 DSH_ACT_OK / DSH_ACT_ERR 标记包裹；解析不到标记就回落旧行为）。
     * file 给定时改为执行该文件（K.run，支持 WSL / Windows / 相对 .blend 三种路径）。
     */
    async act(code: unknown, timeoutMs = 120000, file: unknown = null) {
      const src = file ? ('K.run(' + JSON.stringify(String(file)) + ')')
        : (code === undefined || code === null ? '' : String(code));
      const wrapped = KERNEL_BOOTSTRAP + '\n' + ACT_WRAPPER(src);
      const t0 = Date.now();
      const res = await addon.send('execute_code', { code: wrapped }, timeoutMs);
      const resRec = (res !== null && typeof res === 'object') ? res as Record<string, unknown> : null;
      const raw = (resRec && typeof resRec.result === 'string') ? resRec.result : '';
      const ms = Date.now() - t0;
      const pick = (tag: string): string | null => { const ls = raw.split('\n').filter((x: string) => x.indexOf(tag + ' ') === 0); return ls.length ? ls[ls.length - 1] : null; };
      const errLine = pick('DSH_ACT_ERR');
      const okLine = pick('DSH_ACT_OK');
      let p: Record<string, unknown> | null = null;
      try { p = JSON.parse((errLine || okLine || '').slice((errLine ? 'DSH_ACT_ERR ' : 'DSH_ACT_OK ').length).trim()) as Record<string, unknown>; }
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
    async cmd(name: unknown, params: Record<string, unknown> = {}, timeoutMs = 120000) {
      if (!name || typeof name !== 'string') throw new Error('cmd: name required');
      // v0.9.6（D1）：把 plan op 当 addon 命令发是实测出现过的弯路（qc_render_catalog），
      // 这里在**本地**就纠正，不再浪费一次 Blender 往返。
      const hint = planOpCrossChannelHint(name);
      if (hint) return { status: 'error', result: 'CMD_HINT ' + hint, hint_shown: true };
      return addon.send(name, params || {}, timeoutMs);
    },
    /** 命令清单：目录 + 当前可用性（受 scene 开关与其 API key 影响） */
    async commands() {
      const out = await addon.send('execute_code', { code: FLAGS_CODE });
      const meta = (jsonFromStdout(out) || { flags: {}, scene: null, file: null }) as Record<string, unknown>;
      const flags = (meta.flags || {}) as Record<string, unknown>;
      const available: string[] = [];
      const disabled: string[] = [];
      for (const name of Object.keys(COMMAND_CATALOG)) {
        const gate = COMMAND_CATALOG[name].gate;
        if (!gate || flags[gate]) available.push(name);
        else disabled.push(name);
      }
      // 集成"开关已开但仍不可用"的实情（缺 key 等）由各自 status 命令回答
      const integrations: Record<string, unknown> = {};
      for (const t of ['get_polyhaven_status', 'get_hyper3d_status', 'get_sketchfab_status', 'get_polypizza_status', 'get_hunyuan3d_status']) {
        try {
          const r = await addon.send(t, {}, 15000);
          const rr = (r || null) as Record<string, unknown> | null;
          integrations[t.replace('get_', '').replace('_status', '')] = { enabled: !!(rr && rr.enabled), message: String((rr && rr.message) || '').split('\n')[0].slice(0, 160) };
        } catch (e) {
          integrations[t] = { enabled: false, message: errorMessage(e).slice(0, 120) };
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
    async loopStart(spec: unknown) {
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
    async loopExport(opts: Record<string, unknown> = {}) {
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
      const outRec = (out || null) as Record<string, unknown> | null;
      const txt = (outRec && typeof outRec.result === 'string') ? outRec.result : '';
      if (!txt.includes('AREA_SHOT')) throw new Error('区域截图失败: ' + txt.slice(0, 200));
      const png = fs.readFileSync(LIVE_PNG_WSL);
      return { png: png, meta: { method: 'screenshot_area', info: txt.trim() } };
    },
    /** 渲染性能：status / apply / revert / analyze / help */
    async perf(op: string, payload?: unknown) { return E.perfCall(op, payload); },
    /** 对象精简：opt_analyze / opt_join */
    async opt(op: string, payload?: unknown) { return E.perfCall(op, payload); },
    /** 取一帧视口 PNG（覆盖写固定文件） */
    async frame(maxSize = 560) {
      const res = await addon.send('get_viewport_screenshot', { max_size: maxSize, filepath: LIVE_PNG_WIN, format: 'png' });
      const resRec = (res || null) as Record<string, unknown> | null;
      if (resRec && resRec.error) throw new Error(String(resRec.error));
      const png = fs.readFileSync(LIVE_PNG_WSL);
      return { png: png, meta: res };
    },
    /** 自定义视角捕获：from/look_at/lens/ortho/...（view.py）——不动物体、不动用户视口 */
    async view(spec: Record<string, unknown> = {}) {
      await ensureView();
      metrics.views++;
      // 未指定输出路径时用配置里的工作目录（保证 Blender 与宿主两端都能访问）
      const spec2 = Object.assign({}, spec || {});
      if (!spec2.path) spec2.path = VIEW_PNG_WIN;
      const j = JSON.stringify(spec2);
      const body = 'import json as _json' + '\n' + 'print("VIEW " + K.dsh_view_api["capture"](_json.dumps(_json.loads(' + JSON.stringify(j) + '))))';
      const info = extractTag(await addon.send('execute_code', { code: KERNEL_BOOTSTRAP + '\n' + body }, Math.max(30000, Number(spec.timeoutMs) || 120000)), 'VIEW') as Record<string, unknown> | null;
      if (!info || info.ok === false) throw new Error('view 失败: ' + JSON.stringify(info).slice(0, 300));
      metrics.lastView = { at: Date.now(), ms: info.ms, mode: info.mode, path: info.path };
      return { png: fs.readFileSync(winToWsl(info.path)), meta: info };
    },
    /**
     * 宿主集成（/host/view/capture，契约 §1.4）：抓**当前 GUI 视口**那一帧。
     * 与 view() 的分工：view() 是"从任意位置看一眼"（自建矩阵、不动用户视口）；
     * 本方法是"用户现在看到的就是这一帧"（矩阵直接取 region_3d）→ 面板显示 + 点击反投影。
     * 零场景/零视口改动；拿不到矩阵时 view.py 回 ok=false（上层据此把 pickable 置 false，不撒谎）。
     */
    /** 宿主集成（/host/scene-items，契约 §1.6）：`@` 提及的数据源（objects/materials/collections）。 */
    async hostSceneItems(spec: Record<string, unknown> = {}) {
      await ensureView();
      const body = 'print("HOSTITEMS " + K.dsh_view_api["host_scene_items"](' + JSON.stringify(JSON.stringify(spec)) + '))';
      const info = extractTag(await addon.send('execute_code', { code: KERNEL_BOOTSTRAP + '\n' + body }, 60000, { noMark: true }), 'HOSTITEMS') as Record<string, unknown> | null;
      if (!info || info.ok === false) throw new Error('scene-items 失败: ' + JSON.stringify(info).slice(0, 300));
      return info;
    },
    /**
     * 启动时连上常驻 socket，并**尽力**补一次 `get_addon_info`（不 await、失败即忽略 ——
     * 绝不让"addon 没起来"或"addon 正忙"拖慢后端启动）。
     */
    async start() {
      await addon.ensure();
      addon.send('get_addon_info', {}, 3000).catch(() => null);   // 尽力而为，不阻塞 listen
      return true;
    },
    // v0.9.4（P1-1）：worker 现在是多实例 —— 关后端时要把**所有**实例收干净（原来只杀单例）
    stop() { clearWorkerReaper(); addon.close(); killWorkers(); },
  });
}
