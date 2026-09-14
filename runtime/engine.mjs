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
export const KERNEL_BOOTSTRAP = [
  'import sys as _sys, types as _types',
  'K = _sys.modules.get("dsh_rt_kernel")',
  'if K is None:',
  '    K = _types.ModuleType("dsh_rt_kernel")',
  '    _sys.modules["dsh_rt_kernel"] = K',
  'import bpy, math, mathutils',
  'Vector = mathutils.Vector',
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
  const metrics = { calls: 0, errors: 0, timeouts: 0, inflight: 0, lastCmd: null, lastCmdAt: null, lastOkAt: null, lastError: null, lastDiagnosis: null, views: 0, headlessRuns: 0, lastHeadlessMs: null };
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
      if (/timeout|closed|ECONN|ECONNRESET/i.test(msg)) metrics.timeouts++;
      try { e.diagnosis = await diagnose(); metrics.lastDiagnosis = e.diagnosis; } catch (x) { /* 诊断自身失败不影响原错误 */ }
      throw e;
    } finally {
      metrics.inflight--;
    }
  };
  /** 把 Blender 侧 python 模块注入运行中的 Blender（模块自己把 API 挂到 K 上） */
  const MODULE_ATTR = { RUNNER_READY: 'dsh_loop_api', PERF_READY: 'dsh_perf_api', VIEW_READY: 'dsh_view_api',
                        CONTRACT_READY: 'dsh_contract_api', PLAN_READY: 'dsh_plan_api' };
  async function injectModule(file, marker, versionExpr = '1') {
    const attr = MODULE_ATTR[marker] || ('dsh_' + String(marker).toLowerCase() + '_api');
    // K 才是真相：Blender 重启后 K 会清空，仅靠本地 Set 会误判"已注入"
    try {
      const chk = await addon.send('execute_code', { code: KERNEL_BOOTSTRAP + '\nprint("HAVE " + str(hasattr(K, "' + attr + '"))) ' }, 30000);
      const t = (chk && typeof chk.result === 'string') ? chk.result : '';
      if (t.includes('HAVE True')) { injected.add(file); return true; }
      injected.delete(file);
    } catch (e) { /* 探测失败 → 走重新注入 */ }
    const src = fs.readFileSync(file, 'utf8');
    const tail = '\nprint("' + marker + ' v%d" % ' + versionExpr + ')';
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
    if (o === 'evidence') await ensureView();            // 证据要出图 → 先注入 view.py
    if (isPlan) {
      await ensurePlanner();
      const body = 'print("LOOP " + K.dsh_plan_api["dispatch"](' + JSON.stringify(o.slice(5)) + ', _json.dumps(_json.loads('
        + JSON.stringify(JSON.stringify(payload || {})) + '))))';
      return extractLoop(await addon.send('execute_code', { code: KERNEL_BOOTSTRAP + '\nimport json as _json\n' + body }, 300000));
    }
    await ensureContract();
    const body = 'print("LOOP " + K.dsh_contract_api["dispatch"](' + JSON.stringify(o) + ', _json.dumps(_json.loads('
      + JSON.stringify(JSON.stringify(payload || {})) + '))))';
    return extractLoop(await addon.send('execute_code', { code: KERNEL_BOOTSTRAP + '\nimport json as _json\n' + body }, 300000));
  }
  return {
    addon: addon,
    plan: (op, payload) => planCall(op, payload),
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
    /** 执行 Python（带持久内核 K），返回 addon 的 {"executed":true,"result":"<stdout>"} */
    async act(code, timeoutMs = 120000) {
      const wrapped = KERNEL_BOOTSTRAP + '\n' + String(code === undefined || code === null ? '' : code);
      return addon.send('execute_code', { code: wrapped }, timeoutMs);
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
    /** 无头 Blender：独立进程跑脚本，不占 GUI 通道（重渲染 / 批量几何 / 校验都走它） */
    async headless(opts = {}) {
      const t0 = Date.now();
      const script = opts.script ? String(opts.script) : '';
      // preload: 把 runtime 里的 python 模块（view/perf/runner...）源码拼进脚本开头 → 无头进程也能直接用 K.dsh_view_api 等
      let pre = '';
      const mods = Array.isArray(opts.preload) ? opts.preload : (opts.preload ? String(opts.preload).split(',') : []);
      for (const m of mods) {
        const name = String(m).trim().replace(/\.py$/, '');
        if (!name) continue;
        const f = path.join(HERE, name + '.py');
        if (!fs.existsSync(f)) throw new Error('preload 找不到模块：' + f);
        pre += '# ---- preload ' + name + '.py ----\n' + fs.readFileSync(f, 'utf8') + '\n';
      }
      const body = (script || pre) ? ((opts.bootstrap === false ? '' : KERNEL_BOOTSTRAP + '\n') + pre + script) : '';
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
      const res = await new Promise((resolve) => {
        let so = '', se = '', exitCode = null, signal = null, spawnErr = null, timedOut = false;
        const CAP = 262144;
        let child = null;
        try {
          child = spawn(BLENDER_EXE, args, { env: Object.assign({}, process.env, { PYTHONIOENCODING: 'utf-8' }), stdio: ['ignore', 'pipe', 'pipe'] });
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
      let parsed = null;
      const lines = stdout.split('\n').filter((l) => l.indexOf('HEADLESS ') === 0);
      if (lines.length) { try { parsed = JSON.parse(lines[lines.length - 1].slice('HEADLESS '.length).trim()); } catch (e) { parsed = { _parse_error: String((e && e.message) || e), raw: lines[lines.length - 1].slice(0, 300) }; } }
      let artifacts = [];
      if (outdirWsl) {
        try {
          artifacts = fs.readdirSync(outdirWsl)
            .map((n) => { const p = path.join(outdirWsl, n); let st = null; try { st = fs.statSync(p); } catch (e) {} return st && st.isFile() ? { name: n, bytes: st.size, mtimeMs: st.mtimeMs, fresh: st.mtimeMs >= t0 - 2000 } : null; })
            .filter(Boolean)
            .sort((a, b) => b.mtimeMs - a.mtimeMs)
            .slice(0, 40);
        } catch (e) { artifacts = []; }
      }
      return { ok: !res.timedOut && res.exitCode === 0, exitCode: res.exitCode, signal: res.signal, timedOut: !!res.timedOut,
        ms: ms, timeoutMs: timeoutMs, blender: BLENDER_EXE, script: sp ? sp.win : null, args: args, preload: mods.length ? mods : undefined,
        result: parsed, stdout: stdout.slice(-8000), stderr: String(res.se || '').slice(-4000), artifacts: artifacts };
    },
    async start() { await addon.ensure(); return true; },
    stop() { addon.close(); },
  };
}
