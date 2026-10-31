/**
 * Blender 实时交互后端 —— 只服务 AI 通道（无网页 UI、无输入注入）。
 *
 *   GET  /health         存活 + 统计
 *   GET  /status         JSON：3D 视口区域 rect / 窗口 / 着色模式
 *   GET  /frame.png     单帧 PNG（?size=560，最长边像素）
 *   POST /act            body: {"code":"..."} → 执行 Python（持久内核 K），返回 stdout/耗时
 *   GET  /commands       JSON：addon 命令清单 + 当前可用性 + 5 个集成状态
 *   POST /cmd            body: {"name":"命令名","params":{...}} → 直连透传任意 addon 命令
 *   POST /loop           body: {"op":"start|status|stop|bench","spec":{...}} → Blender 侧内环（runner.py）
 *   POST /view           body: {from, look_at, lens, ortho, ...} → 自定义视角出图（view.py，不动场景）
 *   ── 宿主集成（用户侧能力；不进 /plan 的 op 目录）──
 *   GET  /host/scene-items   `@` 提及数据源（objects / materials / collections）
 *   GET  /host/jobs          headless 作业列表（**只读台账，零 Blender 往返**）
 *   GET  /host/jobs/log      某作业的 stdout/stderr 尾部（**只读**）
 *   ── v2.3（契约 v2.3 增补 §S1–§S3：Blender 原生选中 → DSH）──
 *   POST /host/selection         addon 推当前选中（N 面板「发送选中到 DSH」）
 *   GET  /host/selection/latest  客户端轮询未消费的选中（**零 Blender 往返**）
 *   POST /host/selection/consume 标记已插入草稿（seq）
 *   （所有 /host/* 带 CORS 头；Access-Control-Allow-Origin 只对 HTTP loopback 或精确 dsh-app://app 回显）
 *   POST /headless       body: {script, file, outdir, args, timeoutMs} → 无头 Blender 独立进程跑脚本
 *   GET  /who            JSON：租约持有者 + 通道指标（多会话共存时先看它）
 *   POST /lease          body: {holder, ttlMs, force?, renewOnly?} → 拿写权限租约
 *   POST /release        body: {holder?} → 主动释放租约
 *
 * 并发保护：写路由（/act /cmd /loop /perf /opt /headless）默认要求"无租约或租约是自己的"，
 * 别人的租约生效时返回 409 {error:"leased", holder, expiresInMs}；调用方带 force:true 可抢占。
 * 单 agent 场景下 holder 由插件自动带上，互不干扰。
 *
 * 启动：node server.mjs [--port 9877]（缺省端口取 config.mjs：DSH_BLENDER_HTTP_PORT / 配置文件 / 9877）
 */
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { createEngine, engineProvenance, PLAN_READ_ONLY_OPS } from './engine.mjs';
import { CFG, describeConfig } from './config.mjs';
// v2.3（Blender 原生选中 → DSH）：addon 推来的选中事件（纯内存；轮询方零 Blender 往返）
import { createSelectionStore } from './selectionstore.mjs';
// issue #51：Windows 上没有 /proc，残留后端/端口占用者的探测走 win_proc（非 Windows 侧零影响）
import { winSamePortHolders, winKillPids } from './win_proc.mjs';

const argv = process.argv.slice(2);
const portArg = argv.indexOf('--port');
const PORT = Number(portArg >= 0 ? argv[portArg + 1] : (process.env.VIEWPORT_PORT || CFG.httpPort));
const HOST = '127.0.0.1';

const engine = createEngine();
// v2.3：Blender 原生选中事件（N 面板「发送选中到 DSH」→ 这里 → 客户端插入草稿）
const selectionStore = createSelectionStore();
const stats = { frames: 0, acts: 0, cmds: 0, loops: 0, views: 0, headless: 0, plan: 0, worker: 0, txn: 0, preset: 0, job: 0, selections: 0, lastSelectionMs: null, lastPlanMs: null, lastViewMs: null, lastHeadlessMs: null, lastFrameMs: null, lastActMs: null, lastError: null, startedAt: Date.now() };

/**
 * v0.9.4（P2-1）：各路由最近 20 次耗时的 p50/p95 —— **只观测，不自动重启**。
 * 为什么：看护已经有（15 s 探活 + 掉线自动拉起），但"Blender 是不是在变慢"在 /health 里查不到
 * —— lastXxxMs 只有一条样本，看不出趋势。诊断要的是分布，不是单点。
 * 为什么**不做**"预测性重启"：误杀一次 20 分钟渲染的代价远大于省下的重启时间；
 * 自动拉起仍然只在"端口/PID 明确死亡"这一确定态发生。
 */
const RTT_N = 20;
const RTT = { frame: [], act: [], view: [], headless: [], plan: [], worker: [], job: [] };
function pushRtt(kind, ms) {
  const a = RTT[kind];
  if (!a) return;
  const v = Number(ms);
  a.push(Number.isFinite(v) ? Math.round(v) : 0);
  if (a.length > RTT_N) a.shift();
}
function rttView() {
  const out = {};
  for (const k of Object.keys(RTT)) {
    const raw = RTT[k];
    const sorted = raw.slice().sort((x, y) => x - y);
    const q = (p) => (sorted.length ? sorted[Math.min(sorted.length - 1, Math.floor(p * sorted.length))] : null);
    out[k] = { last: raw.length ? raw[raw.length - 1] : null, p50: q(0.5), p95: q(0.95), n: raw.length };
  }
  return out;
}

/**
 * 写权限租约：多 agent / 多会话同时驱动一个 Blender 时，靠它避免互相踩。
 * 语义：不持有租约时不拦（单机单 agent 完全无感）；一旦有人显式拿到租约，别人的写操作会被 409 挡住。
 * 过期即自动失效（TTL，默认 10 min，每次写操作自动续期）。
 */
const LEASE = { holder: null, token: null, acquiredAt: null, expiresAt: null, ttlMs: CFG.leaseTtlMs, renewals: 0,
               // v2.6（本轮反馈 #7）：写作用域的**第二维** —— 不只有"谁在写"，还有"写到哪"。
               scope: null };
/**
 * v0.9.4：租约持有者**是否还活着**（实测踩到：DSH 会话崩了/被关掉之后，它的租约在内存里
 * 继续生效到 TTL（默认 1 h），把别的会话的写通道整段挡死 —— 而那个会话早就不存在了）。
 * holder 约定是 `plugin-pid-<pid>`（插件前端 HOLDER，见 src/index.ts），后端与它同机 → 能直接探活。
 * 返回 true/false；**查不出来返回 null**（按"活着"处理，绝不误抢别人的租约）。
 */
/**
 * v2.6（本轮反馈 #7）：把写作用域声明规范化成 [{kind:'collection'|'prefix', value}]。
 *
 * 现场：子代理只有单一集合的写权限，于是灯（LGT-*）被迫塞进资产集合，装配时再逐个人工搬 ——
 * 「写作用域」只按集合一刀切，和「对象名前缀」这个真正的归属信号脱节。
 * 这里允许两种维度同时声明：
 *   collection:COL_Lights   或裸 COL_Lights        → 集合维度
 *   prefix:LGT-             或裸 LGT-* / LGT-      → 对象名前缀维度
 * 语义是**声明式**（advisory）：不拦写入，但会出现在 /who、/lease 与 409 回执里，
 * 让"谁该写哪"在协议层可见；强制归属由装配层的 K.route_collections() 一次归位。
 */
function normalizeScope(scope) {
  const raw = Array.isArray(scope) ? scope : String(scope == null ? '' : scope).split(',');
  const out = [];
  for (const item of raw) {
    const s0 = String(item || '').trim();
    if (!s0) continue;
    if (/^collection:/i.test(s0)) { out.push({ kind: 'collection', value: s0.slice(11).trim() }); continue; }
    if (/^prefix:/i.test(s0)) { out.push({ kind: 'prefix', value: s0.slice(7).trim() }); continue; }
    if (s0.endsWith('*') || s0.includes('-')) { out.push({ kind: 'prefix', value: s0.replace(/\*$/, '') }); continue; }
    out.push({ kind: 'collection', value: s0 });
  }
  return out.length ? out : null;
}
function holderAlive(holder) {
  const m = /^plugin-pid-(\d+)$/.exec(String(holder || ''));
  if (!m) return null;
  const pid = Number(m[1]);
  if (!Number.isInteger(pid) || pid < 2 || pid > 4194304) return null;
  try { process.kill(pid, 0); return true; }
  catch (e) { return (e && e.code === 'EPERM') ? true : false; }   // EPERM：存在但无权限 → 活着
}
function leaseView() {
  const now = Date.now();
  const active = !!(LEASE.holder && LEASE.expiresAt && LEASE.expiresAt > now);
  return {
    active: active,
    holder: active ? LEASE.holder : null,
    expiresInMs: active ? LEASE.expiresAt - now : 0,
    ttlMs: LEASE.ttlMs,
    since: LEASE.acquiredAt,
    renewals: LEASE.renewals,
    holderAlive: active ? holderAlive(LEASE.holder) : null,   // null = 无法判断 / 非本机约定命名
    scope: active ? LEASE.scope : null,                       // v2.6：写作用域（collection / prefix 两维）
    staleReclaimed: stats.staleLeasesReclaimed || 0,
  };
}
/** 死会话的租约 → 立刻回收（返回被回收的 holder；没得回收返回 null）。不改租约语义，只补一个"持有者不存在"的出口。 */
function releaseStaleLease(why) {
  const v = leaseView();
  if (!v.active || v.holderAlive !== false) return null;
  const dead = v.holder;
  LEASE.holder = null; LEASE.token = null; LEASE.expiresAt = null; LEASE.acquiredAt = null; LEASE.renewals = 0;
  stats.staleLeasesReclaimed = (stats.staleLeasesReclaimed || 0) + 1;
  stats.lastLeaseNote = '已回收死会话的写租约（holder=' + dead + '，' + String(why || '') + '）';
  return dead;
}
function leaseAcquire(holder, ttlMs, force, scope) {
  const now = Date.now();
  const h = String(holder || 'anonymous');
  const v = leaseView();
  if (v.active && v.holder !== h && !force) {
    return { denied: true, holder: v.holder, expiresInMs: v.expiresInMs };
  }
  if (!v.active || v.holder !== h) {
    LEASE.token = 'lk_' + Math.random().toString(16).slice(2, 10);
    LEASE.acquiredAt = now;
    LEASE.renewals = 0;
  } else {
    LEASE.renewals++;
  }
  LEASE.holder = h;
  if (ttlMs) LEASE.ttlMs = Math.max(5000, Math.min(86400000, Number(ttlMs) || LEASE.ttlMs));
  if (scope !== undefined) LEASE.scope = normalizeScope(scope);   // v2.6：声明式写作用域（省略则保留）
  LEASE.expiresAt = now + LEASE.ttlMs;
  return { granted: true, lease: leaseView() };
}
/** 只读 op：即便别人持有租约也放行（否则连"看看现状"都会被挡） */
const READ_ONLY_OPS = { '/perf': ['status', 'help'], '/loop': ['status', 'board', 'help'], '/opt': ['opt_analyze', 'analyze', 'help'],
  '/plan': PLAN_READ_ONLY_OPS,'/worker': ['status', 'list', 'see', 'ping'],
  '/txn': ['list', 'marks', 'help', 'edit_status'],
  '/preset': ['list', 'get', 'help'],
  // v0.9.3（D6）：wait 只是"阻塞读"，kill 只作用于作业自己的子进程（不碰 live 场景）——
  // 它们不该被别的会话的写租约挡住（否则"我的作业我收不回来"）。
  '/job': ['status', 'collect', 'list', 'wait', 'kill'] };
function isReadOnly(path, op) {
  const list = READ_ONLY_OPS[path];
  return !!(list && list.indexOf(String(op)) >= 0);
}

/** 请求带上 holder/force 时的门禁；返回 null = 放行 */
function leaseGate(payload) {
  const h = String((payload && (payload.holder || payload.lease)) || '');
  const force = !!(payload && payload.force);
  // v0.9.4：先回收"持有者进程已经不存在"的租约（否则会话崩了会把写通道挡到 TTL 结束）
  const reclaimed = releaseStaleLease('写请求到达时按 plugin-pid-<pid> 探活发现进程不存在');
  const v = leaseView();
  if (v.active && v.holder !== h && !force) {
    return { ok: false, error: 'leased', holder: v.holder, expiresInMs: v.expiresInMs, holderAlive: v.holderAlive, holderScope: v.scope || null,
      hint: '另一个会话正在驱动这个 Blender。要么等它（剩余 ' + Math.round(v.expiresInMs / 1000) + 's），要么 body 加 force:true 抢占，或 blender_viewport op=lease force=true' };
  }
  // 放行时顺手续期：holder 匹配或无人持有时自动接管
  if (!h) return null;  // 没报 holder 的老客户端：无租约时按旧行为放行
  const r = leaseAcquire(h, null, force);
  return r.denied ? { ok: false, error: 'leased', holder: r.holder, expiresInMs: r.expiresInMs, hint: '租约已被占用' } : null;
}
/** 每路由请求计数（诊断用：判断是否还有旧标签页在轮询已删除的接口） */
const reqCounts = new Map();
function bump(p) { reqCounts.set(p, (reqCounts.get(p) || 0) + 1); }

function json(res, code, obj) {
  const body = JSON.stringify(obj);
  res.writeHead(code, { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store' });
  res.end(body);
}
/**
 * 流式响应（ndjson）—— 长任务必需：客户端 fetch 的响应头超时默认 300 s（实测 UND_ERR_HEADERS_TIMEOUT），
 * 而服务端要等子进程跑完才回包 → 长任务必然踩超时。这里立刻发响应头 + 周期心跳，最后一行才是结果。
 */
async function streamJson(res, producer, hbMs = 15000) {
  const t0 = Date.now();
  res.writeHead(200, { 'content-type': 'application/x-ndjson; charset=utf-8', 'cache-control': 'no-store', 'x-dsh-stream': '1' });
  let done = false;
  const hb = setInterval(() => {
    if (done) return;
    try { res.write(JSON.stringify({ heartbeat: true, elapsedMs: Date.now() - t0 }) + '\n'); } catch (e) { /* 客户端断了就算了 */ }
  }, hbMs);
  let obj;
  try { obj = await producer(); }
  catch (e) { obj = { ok: false, error: String((e && e.message) || e), diagnosis: (e && e.diagnosis) || null }; }
  done = true; clearInterval(hb);
  try { res.write(JSON.stringify(obj) + '\n'); } catch (e) { /* ignore */ }
  res.end();
  return obj;
}

function readBody(req, limit = 1 << 22) {
  return new Promise((resolve, reject) => {
    let n = 0; const chunks = [];
    req.on('data', (c) => { n += c.length; if (n > limit) { reject(new Error('body too large')); req.destroy(); return; } chunks.push(c); });
    req.on('end', () => resolve(Buffer.concat(chunks).toString('utf8')));
    req.on('error', reject);
  });
}

// ── 宿主集成（/host/*，契约 docs/宿主集成-接口契约-v2.3增补.md §S1–§S3）────────────
// 这是**用户侧**能力（人点视口 → 进自己的消息），不是模型工具：不进 /plan 的 op 目录、
// 不加模型工具（capability.lock 的 family/op/工具名一动不动）。三条纪律：
//   ① 所有 /host/* 响应（含错误）都带 CORS 头，但 `Access-Control-Allow-Origin`
//      **只对 HTTP loopback 或精确 dsh-app://app 回显**（其余来源不回该头）；
//   ② 面板轮询只读 store（/host/view/latest 与 /host/view.png 一行都不碰 Blender）——
//      面板开着不能占 Blender 主线程，这是上游 ViewportStore 的核心设计；
//   ③ pick 先校验 file/scene 错配 → `*_mismatch`，**绝不**降级为"投到当前场景"。
// 这五条路由都**不改场景**（capture 只是离屏画一帧）→ 不碰写租约（只读语义）。
// DSH 官方 Electron 桌面端使用 dsh-app://app，而不是 HTTP loopback Origin。
// 只增加这个精确来源，不允许任意 dsh-app: 来源、null 或通配符。
// CORS 控制浏览器对响应的读取，不是身份验证，也不阻止非浏览器客户端伪造 Origin。
const HOST_ORIGIN_RE = /^(http:\/\/(127\.0\.0\.1|localhost)(:\d+)?|dsh-app:\/\/app)$/;
function hostCorsHeaders(origin) {
  const h = {
    'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
    'Access-Control-Allow-Headers': 'content-type',
    'Access-Control-Max-Age': '600',
    'Vary': 'Origin',
  };
  if (origin && HOST_ORIGIN_RE.test(String(origin))) h['Access-Control-Allow-Origin'] = String(origin);
  return h;
}
function hostJson(res, code, obj, origin) {
  res.writeHead(code, Object.assign({ 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store' },
                                    hostCorsHeaders(origin)));
  res.end(JSON.stringify(obj));
}
function hostErrText(e) { return String((e && e.message) || e); }
/** `@` 提及的 limit：夹到 [1,1000]，默认 200（旧 viewstore 口径，路由还在用）。 */
function clampItemsLimit(v, dflt = 200) {
  const n = Number(v);
  if (!Number.isFinite(n)) return dflt;
  return Math.max(1, Math.min(1000, Math.round(n)));
}
function hostNormEmpty(v) { return (v === '' || v == null) ? null : v; }

/**
 * v2.6.1：`GET /host/state` 的响应体（纯内存，零 Blender 往返；任何一块取不到都给保守默认值，不抛）。
 * 「AI 已暂停」只能在 addon 拒掉一条命令之后才知道（后端不为它轮询 Blender），成功一次就清空。
 */
function hostStateView() {
  const now = Date.now();
  const age = (t) => (t ? now - t : null);
  let m = {};
  try { m = engine.metrics() || {}; } catch (e) { m = {}; }
  let render = { busy: false, ageMs: null, scene: null };
  try {
    const g = engine.renderFlag();
    if (g && g.present) render = { busy: !g.stale, ageMs: g.ageMs, scene: (g.info && g.info.scene) || null };
  } catch (e) { /* 读不到标记 = 不在渲染 */ }
  let running = 0;
  try { running = (engine.job.list() || []).filter((j) => j && j.status === 'running').length; } catch (e) { running = 0; }
  return {
    ok: true,
    channel: {
      lastOkAgeMs: age(m.lastOkAt),
      lastCmd: m.lastCmd || null,
      lastCmdAgeMs: age(m.lastCmdAt),
      inflight: Number(m.inflight) || 0,
      lastError: m.lastError ? String(m.lastError).slice(0, 200) : null,
      lastErrorAgeMs: age(m.lastErrorAt),
      diagnosis: (m.lastDiagnosis && m.lastDiagnosis.kind) ? String(m.lastDiagnosis.kind) : null,
      aiPaused: !!m.aiPausedAt,
    },
    lease: leaseView(),
    render: render,
    jobs: { running: running },
    backend: { pid: process.pid, uptimeMs: now - stats.startedAt },
  };
}

async function handleHost(req, res, url, p) {
  const origin = req.headers.origin || null;

  // §1.1 CORS 预检：204 空体
  if (req.method === 'OPTIONS') {
    res.writeHead(204, hostCorsHeaders(origin));
    res.end();
    return;
  }

  // §1.6 `@` 提及数据源
  if (req.method === 'GET' && p === '/host/scene-items') {
    let out;
    try {
      out = await engine.hostSceneItems({ q: url.searchParams.get('q') || '',
                                          limit: clampItemsLimit(url.searchParams.get('limit')) });
    } catch (e) {
      stats.lastError = hostErrText(e);
      hostJson(res, 502, { ok: false, error: hostErrText(e) }, origin);
      return;
    }
    hostJson(res, 200, { ok: true, objects: out.objects || [], materials: out.materials || [],
                         collections: out.collections || [] }, origin);
    return;
  }

  // §8.3 headless 作业区（**只读**：只读后端已有的 engine.job 台账，**零 Blender 往返**）
  // 为什么面板看不到 headless：它跑的是独立 blender -b 进程（自己的场景），不碰 GUI ⇒ 视口/面板无画面。
  if (req.method === 'GET' && p === '/host/jobs') {
    const limit = clampIntParam(url.searchParams.get('limit'), 20, 1, 100);
    let all = [];
    try { all = engine.job.list() || []; } catch (e) { all = []; }
    const trimmed = all.map(trimJob);
    // running 在前；其余按耗时倒序（契约 §8.3）
    trimmed.sort((a, b) => ((b.status === 'running' ? 1 : 0) - (a.status === 'running' ? 1 : 0)) || (b.ms - a.ms));
    const running = trimmed.filter((j) => j.status === 'running').length;
    hostJson(res, 200, { ok: true, jobs: trimmed.slice(0, limit), running: running, total: trimmed.length }, origin);
    return;
  }
  if (req.method === 'GET' && p === '/host/jobs/log') {
    const id = String(url.searchParams.get('id') || '');
    const tail = clampIntParam(url.searchParams.get('tail'), 8000, 1, 200000);
    let snap = null;
    try { snap = engine.job.collect(id, tail); } catch (e) { snap = null; }
    // `engine.job.collect` 对不存在的 id 回 `{status:'unknown', ok:false}`（不是 null）⇒ 这里归一成明确的失败态
    if (!snap || snap.status === 'unknown' || snap.ok === false) {
      hostJson(res, 200, { ok: false, id: id, error: 'unknown_job', stdout: null, stderr: null, job: null }, origin);
      return;
    }
    hostJson(res, 200, { ok: true, id: id,
                         stdout: (snap.stdoutTail === undefined ? null : snap.stdoutTail),
                         stderr: (snap.stderrTail === undefined ? null : snap.stderrTail),
                         job: trimJob(snap) }, origin);
    return;
  }

  // v2.6.1（dsh-TUI 页脚状态字段，契约 v2.3 增补 §S6）：通道 / 租约 / 渲染 / 作业 / AI 暂停一次给全。
  // **零 Blender 往返**：只读内存里的 engine.metrics()、租约表、render_guard 标记文件与作业台账。
  if (req.method === 'GET' && p === '/host/state') {
    hostJson(res, 200, hostStateView(), origin);
    return;
  }

  // ── v2.3（Blender 原生选中 → DSH，契约 v2.3 增补 §S1–§S3）──────────────
  // addon 的 N 面板「发送选中到 DSH」直接 POST 到这里；客户端半边轮询 latest 并插入草稿。
  // 三条都**零 Blender 往返**：addon 主动推，轮询方只读内存。
  if (req.method === 'POST' && p === '/host/selection') {
    const raw = await readBody(req);
    let payload = null;
    try { payload = JSON.parse(raw); } catch (e) { payload = null; }
    const t0 = Date.now();
    const stored = selectionStore.put(payload);
    if (!stored.ok) {
      hostJson(res, 400, { ok: false, error: stored.error || 'invalid_payload' }, origin);
      return;
    }
    stats.selections = (stats.selections || 0) + 1;
    stats.lastSelectionMs = Date.now() - t0;
    hostJson(res, 200, stored, origin);
    return;
  }
  if (req.method === 'GET' && p === '/host/selection/latest') {
    hostJson(res, 200, selectionStore.snapshot(), origin);
    return;
  }
  if (req.method === 'POST' && p === '/host/selection/consume') {
    const raw = await readBody(req);
    let payload = null;
    try { payload = JSON.parse(raw); } catch (e) { payload = null; }
    const consumed = selectionStore.consume(payload && payload.seq, payload || {});
    hostJson(res, consumed.ok ? 200 : 400, consumed, origin);
    return;
  }

  hostJson(res, 404, { ok: false, error: 'not found', path: p }, origin);
}

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, 'http://' + HOST + ':' + String(PORT));
  const p = url.pathname;
  bump(p);
  try {
    // 宿主集成（/host/*）：独立路由面；出错也必须是**带 CORS 头**的 JSON
    if (p === '/host' || p.startsWith('/host/')) {
      try { await handleHost(req, res, url, p); }
      catch (e) {
        try { hostJson(res, 500, { ok: false, error: hostErrText(e) }, req.headers.origin || null); }
        catch (e2) { try { res.end(); } catch (e3) { /* 已经断了就算了 */ } }
      }
      return;
    }

    if (req.method === 'GET' && p === '/health') {
      json(res, 200, { ok: true, stats: stats, lease: leaseView(), requests: Object.fromEntries(reqCounts),
                       // v0.9.4（P2-1）：分布 + 后端自身内存。⚠ 不含 blender.exe 的 RSS ——
                       // 它在 Windows 侧，取一次要起 tasklist（~百 ms 级），不该塞进健康探针的热路径。
                       rtt: rttView(),
                       // v0.9.6（D3）：加载版本自证（内容哈希，不是 mtime）——"要不要重启后端"在这里一眼可见。
                       // 只做本地读+哈希（~0.5 ms），不 spawn 干净进程；强对拍走 /plan op=catalog args={verify:true}。
                       provenance: (() => { try { return engineProvenance({}); } catch (e) { return { verdict: 'unknown', error: String((e && e.message) || e) }; } })(),
                       backend: { pid: process.pid, rssMB: Math.round(process.memoryUsage().rss / 1048576),
                                  uptimeMs: Date.now() - stats.startedAt } });
      return;
    }

    // 已移除的"人肉面板"路由：明确告知 410，而不是让人对着坏掉的图猜
    if (p === '/' || p === '/index.html' || p === '/stream.mjpg' || p === '/input' || p === '/frame') {
      json(res, 410, {
        ok: false,
        error: 'gone',
        reason: '人肉视口面板（网页 UI + MJPEG 直播 + 输入注入）已移除',
        hint: '请关闭此标签页；Blender 交互改用 AI 通道：blender_rt_see / blender_rt_do / blender_rt_watch',
        removedAt: '2026-09-12',
      });
      return;
    }

    if (req.method === 'GET' && p === '/status') {
      try {
        const s = await engine.status();
        const m = engine.metrics();
        json(res, 200, { ok: true, region: s.region, plugin: s.plugin || null, runs: s.runs || [],
                         jobs: s.jobs || [], ledger: s.ledger || null,
                         stats: stats, metrics: m, diagnosis: m.lastError && m.lastDiagnosis ? m.lastDiagnosis : null });
      } catch (e) {
        json(res, 200, { ok: false, error: String((e && e.message) || e), stats: stats, metrics: engine.metrics(), diagnosis: (e && e.diagnosis) || engine.metrics().lastDiagnosis || null });
      }
      return;
    }

    if (req.method === 'GET' && p === '/doctor') {
      try {
        const d = await engine.doctor();
        json(res, 200, { ok: true, ...d, config: describeConfig({ httpPort: PORT }), metrics: engine.metrics(), stats: stats });
      } catch (e) {
        json(res, 200, { ok: false, error: String((e && e.message) || e), metrics: engine.metrics() });
      }
      return;
    }

// v0.9.4（外部反馈《M1A1 分件建模》P1）：一键拉起 GUI Blender 并自动 Connect addon。
    // 不加租约门：**Blender 没起来 / addon 没连** 恰恰是"还没有任何会话持有写权限"的状态，
    // 这时如果先要求租约，agent 就永远卡在第一步（反馈里 agent 只能自己拼 PowerShell 绕过去）。
    if (req.method === 'POST' && p === '/launch') {
      const raw = await readBody(req);
      let payload = {};
      try { payload = JSON.parse(raw); } catch (e) { payload = {}; }
      const t0 = Date.now();
      try {
        const out = await engine.launchBlender(payload);
        stats.launches = (stats.launches || 0) + 1;
        stats.lastLaunchMs = Date.now() - t0;
        const doc = out.ok && (out.launched || out.already) ? await engine.doctor().catch(() => null) : null;
        json(res, out.ok ? 200 : 200, Object.assign({ ok: !!out.ok, ms: Date.now() - t0, launches: stats.launches }, out, { doctor: doc }));
      } catch (e) {
        json(res, 500, { ok: false, ms: Date.now() - t0, error: String((e && e.message) || e), hint: '看后端日志；也可用 exe/path 显式指定 blender.exe' });
      }
      return;
    }

    if (req.method === 'GET' && p === '/frame.png') {
      const size = Math.max(120, Math.min(1600, Number(url.searchParams.get('size') || 560)));
      const full = url.searchParams.get('full') === '1';
      const areaIndex = Number(url.searchParams.get('area') || 0);
      const t0 = Date.now();
      try {
        const out = full ? await engine.frameArea(areaIndex) : await engine.frame(size);
        stats.frames++; stats.lastFrameMs = Date.now() - t0; pushRtt('frame', stats.lastFrameMs); stats.lastError = null;
        res.writeHead(200, { 'content-type': 'image/png', 'cache-control': 'no-store' });
        res.end(out.png);
      } catch (e) {
        stats.lastError = String((e && e.message) || e);
        json(res, 502, { ok: false, error: stats.lastError, diagnosis: (e && e.diagnosis) || null });
      }
      return;
    }

    if (req.method === 'POST' && p === '/act') {
      const raw = await readBody(req);
      let payload = {};
      try { payload = JSON.parse(raw); } catch (e) { payload = { code: raw }; }
      const gate = leaseGate(payload);
      if (gate) { json(res, 409, gate); return; }
      const t0 = Date.now();
      try {
        const out = await engine.act(payload.code || '', payload.timeoutMs || 120000, payload.file || null);
        stats.acts++; stats.lastActMs = Date.now() - t0; pushRtt('act', stats.lastActMs); stats.lastError = null;
        const o = out || {};
        json(res, 200, { ok: o.ok !== false, ms: o.ms != null ? o.ms : (Date.now() - t0),
                         mainThreadMs: o.mainThreadMs != null ? o.mainThreadMs : null,
                         executed: !!o.executed, stdout: String(o.stdout || ""), stderr: String(o.stderr || ""),
                         error: o.error || null, traceback: o.traceback || null,
                         file: o.file || null, marker_missing: !!o.marker_missing,
                         sceneEpoch: o.sceneEpoch || null,
                         errorKind: o.error ? 'execution' : null });
      } catch (e) {
        stats.acts++; stats.lastError = String((e && e.message) || e);
        json(res, 200, { ok: false, ms: Date.now() - t0, error: stats.lastError,
                         errorKind: (e && e.kind) || null, diagnosis: (e && e.diagnosis) || null });
      }
      return;
    }

    if (req.method === 'GET' && p === '/commands') {
      try {
        const c = await engine.commands();
        json(res, 200, { ok: true, ...c });
      } catch (e) {
        json(res, 200, { ok: false, error: String((e && e.message) || e), diagnosis: (e && e.diagnosis) || null });
      }
      return;
    }

    if (req.method === 'POST' && p === '/cmd') {
      const raw = await readBody(req);
      let payload = {};
      try { payload = JSON.parse(raw); } catch (e) { payload = {}; }
      const gate = leaseGate(payload);
      if (gate) { json(res, 409, gate); return; }
      const t0 = Date.now();
      stats.cmds++;
      try {
        const out = await engine.cmd(String(payload.name || ''), payload.params || {}, payload.timeoutMs || 120000);
        json(res, 200, { ok: true, ms: Date.now() - t0, result: out });
      } catch (e) {
        stats.lastError = String((e && e.message) || e);
        json(res, 200, { ok: false, ms: Date.now() - t0, error: stats.lastError, diagnosis: (e && e.diagnosis) || null });
      }
      return;
    }

    if (req.method === 'POST' && p === '/loop') {
      const raw = await readBody(req);
      let payload = {};
      try { payload = JSON.parse(raw); } catch (e) { payload = {}; }
      const op = String(payload.op || 'status');
      const gate = isReadOnly('/loop', op) ? null : leaseGate(payload);
      if (gate) { json(res, 409, gate); return; }
      stats.loops++;
      try {
        let out;
        if (op === 'start') out = await engine.loopStart(payload.spec || {});
        else if (op === 'stop') out = await engine.loopStop();
        else if (op === 'bench') out = await engine.loopBench(payload.iterations);
        else if (op === 'board') out = await engine.loopBoard(payload.limit || 10, payload.groups !== false);
        else if (op === 'export') out = await engine.loopExport({ path: payload.path, top: payload.top, includeVariants: !!payload.include_variants, note: payload.note });
        else if (op === 'help') out = await engine.loopHelp();
        else out = await engine.loopStatus(payload.history || 8, payload.board || 0);
        json(res, 200, { ok: true, op: op, result: out });
      } catch (e) {
        json(res, 200, { ok: false, op: op, error: String((e && e.message) || e), diagnosis: (e && e.diagnosis) || null });
      }
      return;
    }

    if (req.method === 'POST' && p === '/perf') {
      const raw = await readBody(req);
      let payload = {};
      try { payload = JSON.parse(raw); } catch (e) { payload = {}; }
      const op = String(payload.op || 'status');
      const gate = isReadOnly('/perf', op) ? null : leaseGate(payload);
      if (gate) { json(res, 409, gate); return; }
      stats.perf = (stats.perf || 0) + 1;
      try {
        const out = await engine.perf(op, payload.args);
        json(res, 200, { ok: true, op: op, result: out });
      } catch (e) {
        json(res, 200, { ok: false, op: op, error: String((e && e.message) || e), diagnosis: (e && e.diagnosis) || null });
      }
      return;
    }

    if (req.method === 'POST' && p === '/opt') {
      const raw = await readBody(req);
      let payload = {};
      try { payload = JSON.parse(raw); } catch (e) { payload = {}; }
      const op = String(payload.op || 'opt_analyze');
      const gate = isReadOnly('/opt', op) ? null : leaseGate(payload);
      if (gate) { json(res, 409, gate); return; }
      stats.opt = (stats.opt || 0) + 1;
      try {
        const out = await engine.opt(op, payload.args);
        json(res, 200, { ok: true, op: op, result: out });
      } catch (e) {
        json(res, 200, { ok: false, op: op, error: String((e && e.message) || e), diagnosis: (e && e.diagnosis) || null });
      }
      return;
    }

    if (req.method === 'GET' && p === '/who') {
      json(res, 200, { ok: true, lease: leaseView(), metrics: engine.metrics(), stats: stats, config: describeConfig({ httpPort: PORT }), requests: Object.fromEntries(reqCounts) });
      return;
    }

    if (req.method === 'POST' && p === '/lease') {
      const raw = await readBody(req);
      let payload = {};
      try { payload = JSON.parse(raw); } catch (e) { payload = {}; }
      const holder = String(payload.holder || 'anonymous');
      releaseStaleLease('op=lease 到达时探活发现持有者进程不存在');   // v0.9.4
      if (payload.renewOnly) {
        // 心跳：只有本来就持有租约时才续期 —— 不在空闲时抢占，避免多会话互相锁死
        const v = leaseView();
        if (v.active && v.holder === holder) {
          const rr = leaseAcquire(holder, payload.ttlMs, false, payload.scope);
          json(res, 200, { ok: true, lease: rr.lease, renewed: true });
        } else {
          json(res, 200, { ok: true, lease: v, renewed: false });
        }
        return;
      }
      const r = leaseAcquire(holder, payload.ttlMs, !!payload.force, payload.scope);
      if (r.denied) {
        const otherScope = leaseView().scope || null;
        json(res, 409, { ok: false, error: 'leased', holder: r.holder, expiresInMs: r.expiresInMs, holderScope: otherScope,
          hint: '另一个会话持有写权限租约；force:true 可抢占，或等 ' + Math.round(r.expiresInMs / 1000) + 's 后重试' });
        return;
      }
      json(res, 200, { ok: true, lease: r.lease, renewed: !!payload.renewOnly });
      return;
    }

    if (req.method === 'POST' && p === '/release') {
      const raw = await readBody(req);
      let payload = {};
      try { payload = JSON.parse(raw); } catch (e) { payload = {}; }
      const want = String(payload.holder || '');
      let released = null;
      if (LEASE.holder && (!want || LEASE.holder === want)) {
        released = LEASE.holder;
        LEASE.holder = null; LEASE.token = null; LEASE.expiresAt = null; LEASE.acquiredAt = null;
      }
      json(res, 200, { ok: true, released: released, lease: leaseView() });
      return;
    }

    if (req.method === 'POST' && p === '/view') {
      const raw = await readBody(req);
      let payload = {};
      try { payload = JSON.parse(raw); } catch (e) { payload = {}; }
      const t0 = Date.now();
      try {
        const out = await engine.view(payload);
        stats.views++; stats.lastViewMs = Date.now() - t0; pushRtt('view', stats.lastViewMs); stats.lastError = null;
        if (payload.asJson) { json(res, 200, { ok: true, ms: Date.now() - t0, meta: out.meta }); return; }
        res.writeHead(200, { 'content-type': 'image/png', 'cache-control': 'no-store',
          // ⚠ 实测坑（v0.9.1）：HTTP 头值不能含非 ASCII —— warning 里有中文，直接塞会 502 "Invalid character in header content"。
          // 所以整段 URL 编码后再放头里（客户端 decodeURIComponent 还原）。
          'x-dsh-view': encodeURIComponent(JSON.stringify({
            mode: out.meta.mode, ms: out.meta.ms, path: out.meta.path, width: out.meta.width, height: out.meta.height,
            coverage_estimate: out.meta.coverage_estimate, objects_in_frame: out.meta.objects_in_frame,
            min_margin_px: out.meta.min_margin_px, scene_bbox: out.meta.scene_bbox,
            warning: out.meta.warning, fallback_from: out.meta.fallback_from,
            region: (out.meta.meta || {}).region || null,
          }).slice(0, 6000)) });
        res.end(out.png);
      } catch (e) {
        stats.lastError = String((e && e.message) || e);
        json(res, 502, { ok: false, error: stats.lastError, diagnosis: (e && e.diagnosis) || null });
      }
      return;
    }

    if (req.method === 'POST' && p === '/headless') {
      const raw = await readBody(req);
      let payload = {};
      try { payload = JSON.parse(raw); } catch (e) { payload = {}; }
      const gate = leaseGate(payload);
      if (gate) { json(res, 409, gate); return; }
      const t0 = Date.now();
      stats.headless++;
      await streamJson(res, async () => {
        try {
          const out = await engine.headless(payload);
          stats.lastHeadlessMs = Date.now() - t0; pushRtt('headless', stats.lastHeadlessMs); stats.lastError = null;
          return { ok: !!out.ok, ms: out.ms, result: out };
        } catch (e) {
          stats.lastError = String((e && e.message) || e);
          return { ok: false, ms: Date.now() - t0, error: stats.lastError, hint: (e && e.hint) || null, code: (e && e.code) || null };
        }
      });
      return;
    }
    if (req.method === 'POST' && p === '/plan') {
      // 契约层（S1+S2）与规划器（S3）统一入口：op + args
      const raw = await readBody(req);
      let payload = {};
      try { payload = JSON.parse(raw); } catch (e) { payload = {}; }
      const op = String(payload.op || 'status');
      const gate = isReadOnly('/plan', op) ? null : leaseGate(payload);
      if (gate) { json(res, 409, gate); return; }
      stats.plan = (stats.plan || 0) + 1;
      const t0p = Date.now();
      try {
        const out2 = await engine.plan(op, payload.args || {});
        stats.lastPlanMs = Date.now() - t0p; pushRtt('plan', stats.lastPlanMs);
        json(res, 200, { ok: true, op: op, result: out2 });
      } catch (e) {
        stats.lastError = String((e && e.message) || e);
        json(res, 200, { ok: false, op: op, error: stats.lastError, diagnosis: (e && e.diagnosis) || null });
      }
      return;
    }

    if (req.method === 'POST' && p === '/worker') {
      // 热无头会话：start / exec / status / stop / restart（exec 走流式，长代码不会被客户端超时掐断）
      const raw = await readBody(req);
      let payload = {};
      try { payload = JSON.parse(raw); } catch (e) { payload = {}; }
      const op = String(payload.op || 'status');
      // P0：worker 是**独立无头进程**，不碰 live GUI 场景 ⇒ 显式 isolated:true 时不占全局写租约，
      // 否则多会话并行建模会互相 409（C 组调研的 P0 阻塞项）。同一 worker 仍由自身串行。
      const gate = (payload.isolated || isReadOnly('/worker', op)) ? null : leaseGate(payload);
      if (gate) { json(res, 409, gate); return; }
      stats.worker = (stats.worker || 0) + 1;
      await streamJson(res, async () => {
        try {
          // v0.9.4（P1-1）：全部按 name 走（缺省 = 'default'，与老调用逐字节同义）
          if (op === 'start') return { ok: true, op: op, result: await engine.worker.start(payload, payload && payload.name) };
          if (op === 'stop') return { ok: true, op: op, result: await engine.worker.stop(payload && payload.name) };
          if (op === 'restart') return { ok: true, op: op, result: await engine.worker.restart(payload && payload.name, payload) };
          if (op === 'list') return { ok: true, op: op, result: { ok: true, workers: engine.worker.list() } };
          if (op === 'exec') {
            const purge = payload.purgePrefix ? String(payload.purgePrefix).split(',').map(function (x) { return x.trim(); }).filter(Boolean) : null;
            const r = await engine.worker.exec(String(payload.code || ''), Number(payload.timeoutMs) || 120000, purge, payload && payload.name);
            // v0.8.4：错误/回溯提到信封层，避免客户端只看到 unknown
            return { ok: !!r.ok, op: op, result: r, error: (r && r.error) ? String(r.error) : null,
                     traceback: (r && r.traceback) ? String(r.traceback) : null };
          }
          if (op === 'see') {
            // P0 无头 see：临时相机 + Workbench 离屏；返回 png_b64 + coverage + ms
            return { ok: true, op: op, result: await engine.worker.see(payload.spec || payload.see || {}, payload && payload.name) };
          }
          if (op === 'watch') {
            return { ok: true, op: op, result: await engine.worker.watch(payload.spec || payload.watch || {}, payload && payload.name) };
          }
          if (op === 'reset') {
            return { ok: true, op: op, result: await engine.worker.reset(payload && payload.name) };
          }
          return { ok: true, op: 'status', result: await engine.worker.status(payload && payload.name) };
        } catch (e) {
          stats.lastError = String((e && e.message) || e);
          return { ok: false, op: op, error: stats.lastError, hint: (e && e.hint) || null };
        }
      });
      return;
    }

    if (req.method === 'POST' && p === '/txn') {
      // 事务：snapshot / restore / list / prune / mark / revert / marks / drop / help
      const raw = await readBody(req);
      let payload = {};
      try { payload = JSON.parse(raw); } catch (e) { payload = {}; }
      const op = String(payload.op || 'list');
      const gate = isReadOnly('/txn', op) ? null : leaseGate(payload);
      if (gate) { json(res, 409, gate); return; }
      stats.txn = (stats.txn || 0) + 1;
      await streamJson(res, async () => {
        try {
          const r = await engine.txn(op, payload.args || {});
          return { ok: !!(r && r.ok !== false), op: op, result: r };
        } catch (e) {
          stats.lastError = String((e && e.message) || e);
          return { ok: false, op: op, error: stats.lastError, diagnosis: (e && e.diagnosis) || null };
        }
      });
      return;
    }

    if (req.method === 'POST' && p === '/preset') {
      // 配方库：save / list / get / delete / apply / export / import / help
      const raw = await readBody(req);
      let payload = {};
      try { payload = JSON.parse(raw); } catch (e) { payload = {}; }
      const op = String(payload.op || 'list');
      const gate = isReadOnly('/preset', op) ? null : leaseGate(payload);
      if (gate) { json(res, 409, gate); return; }
      stats.preset = (stats.preset || 0) + 1;
      await streamJson(res, async () => {
        try {
          const r = await engine.preset(op, payload.args || {});
          return { ok: !!(r && r.ok !== false), op: op, result: r };
        } catch (e) {
          stats.lastError = String((e && e.message) || e);
          return { ok: false, op: op, error: stats.lastError, diagnosis: (e && e.diagnosis) || null };
        }
      });
      return;
    }

    if (req.method === 'POST' && p === '/job') {
      // 作业层：start / status / collect / kill / list（长任务后台化，渲染一整晚）
      const raw = await readBody(req);
      let payload = {};
      try { payload = JSON.parse(raw); } catch (e) { payload = {}; }
      const op = String(payload.op || 'list');
      const gate = isReadOnly('/job', op) ? null : leaseGate(payload);
      if (gate) { json(res, 409, gate); return; }
      stats.job = (stats.job || 0) + 1;
      // v0.9.3（D6.2）：op=wait 会阻塞到终态/超时 → 走流式回执（每 15 s 一行心跳，客户端不会被判超时）
      if (op === 'wait') {
        await streamJson(res, async () => {
          try {
            const j = await engine.job.wait(payload.id, Number(payload.timeoutMs) || Number(payload.timeout_ms) || 120000);
            return { ok: true, op: op, job: j };
          } catch (e) {
            stats.lastError = String((e && e.message) || e);
            return { ok: false, op: op, error: stats.lastError };
          }
        });
        return;
      }
      const t0j = Date.now();      // v0.9.4（P2-1）：作业路由耗时进 rtt 分布（P2-2 那种"偶发卡住"要看出趋势）
      try {
        if (op === 'start') { json(res, 200, { ok: true, op: op, job: engine.job.start(payload) }); return; }
        if (op === 'kill') { json(res, 200, { ok: true, op: op, job: engine.job.kill(payload.id) }); return; }
        if (op === 'collect') { json(res, 200, { ok: true, op: op, job: engine.job.collect(payload.id, Number(payload.tail) || 4000) }); return; }
        if (op === 'status') { json(res, 200, { ok: true, op: op, job: engine.job.status(payload.id) }); return; }
        json(res, 200, { ok: true, op: 'list', jobs: engine.job.list() });
      } catch (e) {
        stats.lastError = String((e && e.message) || e);
        json(res, 200, { ok: false, op: op, error: stats.lastError });
      } finally {
        pushRtt('job', Date.now() - t0j);
      }
      return;
    }

    json(res, 404, { ok: false, error: 'not found', path: p });
  } catch (e) {
    json(res, 500, { ok: false, error: String((e && e.stack) || e) });
  }
});

// Blender 可能还没起/没启用 addon —— 不能因此让整个后端退出：
// 否则 /health 不可用、看护与工具全部连不上，错误信息也说不清。启动失败只记一条日志，按调用报错。
// ── S10 自愈补丁（2026-09-29 实测）────────────────────────────────────────────
// 现场（可复现）：后端事件循环被长时间同步操作占死 ⇒ /health 不应答、SIGTERM 处理函数
// 永远排不上队、看护每 15 s 重生一个实例却撞 EADDRINUSE 退出 ⇒ 无限循环，只能人工 kill -9。
// 两层兜底：① 信号处理不再可能被"吞"（强制退出定时器）；② EADDRINUSE 时先判定占用者是死是活，
// 死的直接 SIGKILL 夺回端口并重试 listen（不依赖人工、不依赖看护）。
const sleepMs = (ms) => new Promise((r) => setTimeout(r, ms));

/** 按 /proc 扫出"占着本端口的同族后端"（供 SIGKILL 用；无 /proc 的平台返回 []）。
 *  ★ 必须按 **argv token** 精确匹配，绝不能拿整条 cmdline 做子串匹配：
 *    实测踩过（2026-09-29）—— 调用方的 `bash -c "... node runtime/server.mjs --port 9899 ..."`
 *    命令行里同样含这两个串，子串匹配会把调用方自己的 shell 一起 SIGKILL 掉（自伤）。 */
function samePortHolders(port) {
  const out = [];
  const wantPort = String(port);
  try {
    for (const d of fs.readdirSync('/proc')) {
      if (!/^[0-9]+$/.test(d) || Number(d) === process.pid) continue;
      try {
        const toks = fs.readFileSync('/proc/' + d + '/cmdline', 'utf8').split('\u0000').filter(Boolean);
        let isServer = false, portOk = false, isShell = false;
        for (let i = 0; i < toks.length; i++) {
          if (i === 0 && /(^|\/)(bash|sh|zsh|dash|fish|bwrap)$/.test(toks[i])) isShell = true;
          if (i >= 1 && /(^|\/)server\.mjs$/.test(toks[i])) isServer = true;   // 必须是独立 argv 且以 server.mjs 结尾
          if (toks[i] === '--port' && toks[i + 1] === wantPort) portOk = true;
        }
        if (isServer && portOk && !isShell) out.push(Number(d));
      } catch (e) { /* 进程刚好退出 */ }
    }
  } catch (e) { /* 无 /proc（macOS/Windows）→ 落到下面的 win32 分支 / 交给看护报错 */ }
  // issue #51：Windows 没有 /proc，原先这里恒返回 [] —— 即"EADDRINUSE 自愈在 win32 上等于没有"。
  // 只有 win32 才走这一支（macOS 仍维持"不夺"的既定行为）。
  if (!out.length && process.platform === 'win32') {
    try { return winSamePortHolders(port); } catch (e) { /* 探测失败则不夺 */ }
  }
  return out;
}

/** 占用者到底"死没死"：还能答 /health 就是活的（只是忙），那就绝不夺。 */
async function holderResponsive(port, ms = 1200) {
  const ac = new AbortController();
  const t = setTimeout(() => ac.abort(), ms);
  try { return (await fetch('http://127.0.0.1:' + String(port) + '/health', { signal: ac.signal })).ok; }
  catch (e) { return false; }
  finally { clearTimeout(t); }
}

function killHolders(port, signal) {
  const holders = samePortHolders(port);
  // issue #51：Windows 上 process.kill 只能杀父进程（且没有优雅信号语义），
  // 残留后端可能还带着 headless 子进程 → 统一走 taskkill /T /F。
  if (process.platform === 'win32') {
    try { return winKillPids(holders).map(Number); } catch (e) { return []; }
  }
  const killed = [];
  for (const pid of holders) {
    try { process.kill(pid, signal); killed.push(pid); } catch (e) { /* 已退出 */ }
  }
  return killed;
}

try {
  await engine.start();
} catch (e) {
  console.error('[blender-rt] 启动时未连上 addon（Blender 未运行或 addon 未监听 9876）：' + String((e && e.message) || e));
}

const onListening = () => console.log('[blender-rt] http://' + HOST + ':' + String(PORT) + '/  (routes: /health /status /doctor /launch /who /lease /release /frame /frame.png /act /commands /cmd /loop /perf /opt /view /headless /plan /worker /txn /preset /job /host/scene-items /host/jobs /host/jobs/log /host/state /host/selection /host/selection/latest /host/selection/consume)');
let reclaimTries = 0;   // 夺端口最多 2 次，防止两个实例互踢成死循环

// S9：端口被占（EADDRINUSE）时给可执行结论 —— 现场遇到过"僵死后端占着 9877、WSL 里看不到 PID"。
// S10：不再只是"报错退出"，而是先确认占用者是死是活；死的直接 SIGKILL 夺回并重试 listen。
server.on('error', async (e) => {
  const code = e && e.code;
  if (code === 'EADDRINUSE' && reclaimTries < 2) {
    reclaimTries += 1;
    if (await holderResponsive(PORT)) {
      console.error('[dsh-blender-backend] 端口 ' + String(PORT) + ' 被**能应答**的后端占着 —— 不夺，本次退出（交给看护/op=start）。');
      process.exit(0);
    }
    const k1 = killHolders(PORT, 'SIGTERM');
    await sleepMs(600);
    const k2 = killHolders(PORT, 'SIGKILL');   // 事件循环被占死的进程只认 SIGKILL
    console.error('[dsh-blender-backend] 僵死后端占着 ' + String(PORT) + '：SIGTERM=[' + k1.join(',') + '] SIGKILL=[' + k2.join(',') + '] → 重试 listen。');
    await sleepMs(300);
    try { server.listen(PORT, HOST, onListening); return; } catch (e2) { /* 落到下面的通用报错 */ }
  }
  const msg = code === 'EADDRINUSE'
    ? ('端口 ' + PORT + ' 已被占用（EADDRINUSE）且自动夺回失败：多半是上一次的后端僵死。'
       + '处置：blender_viewport op=restart，或 kill -9 掉 /proc 里 cmdline 含 "server.mjs --port ' + String(PORT) + '" 的进程。')
    : ('监听失败：' + String((e && e.message) || e));
  console.error('[dsh-blender-backend] ' + msg);
  try { fs.writeFileSync(path.join(CFG.workDirWsl, 'backend-listen-error.txt'), msg, 'utf8'); } catch (e2) { /* workDir 都写不了就算了 */ }
  process.exit(1);
});
server.listen(PORT, HOST, onListening);

// 强制退出兜底：信号处理里任何一步抛错/挂住，都不该让"占着端口的僵尸"活下来。
const shutdown = () => {
  try { engine.stop(); } catch (e) { /* 忽略 */ }
  try { server.close(); } catch (e) { /* 忽略 */ }
  setTimeout(() => process.exit(0), 400);
};
process.on('SIGINT', shutdown);
process.on('SIGTERM', shutdown);

// ---------------------------------------------------------------- v2.1（§8.3）作业区助手
/**
 * §8.3 `<trimmed job>`：只留契约点名的字段，**裁掉大字段**（`script`、超长路径串等）。
 * `resultJson` > 8 KB 截断并置 `resultTruncated:true`（别让一次轮询把面板撑爆）。
 * **纯函数、零 I/O** —— 这是 `/host/jobs` 能做到"零 Blender 往返"的前提。
 */
function trimJob(j) {
  const src = (j && typeof j === 'object') ? j : {};
  let resultJson = (src.resultJson === undefined) ? null : src.resultJson;
  let resultTruncated = false;
  if (resultJson !== null) {
    try {
      const s = JSON.stringify(resultJson);
      if (s !== undefined && s.length > 8192) {
        resultJson = { truncated: true, preview: s.slice(0, 8192) };
        resultTruncated = true;
      }
    } catch (e) { resultJson = null; resultTruncated = true; }
  }
  return {
    id: String(src.id === undefined ? '' : src.id),
    kind: src.kind === undefined ? null : src.kind,
    status: src.status === undefined ? 'unknown' : src.status,
    runId: src.runId === undefined ? null : src.runId,
    pid: src.pid === undefined ? null : src.pid,
    pidAlive: !!src.pidAlive,
    ms: Number(src.ms) || 0,
    timeoutMs: src.timeoutMs === undefined ? null : src.timeoutMs,
    outdir: src.outdir === undefined ? null : src.outdir,
    logDir: src.logDir === undefined ? null : src.logDir,
    exitCode: src.exitCode === undefined ? null : src.exitCode,
    signal: src.signal === undefined ? null : src.signal,
    stage: src.stage === undefined ? null : src.stage,
    stageName: src.stageName === undefined ? null : src.stageName,
    stageAgoMs: src.stageAgoMs === undefined ? null : src.stageAgoMs,
    lastOutputAt: src.lastOutputAt === undefined ? null : src.lastOutputAt,
    idleMs: src.idleMs === undefined ? null : src.idleMs,
    lines: Number(src.lines) || 0,
    logBytes: Number(src.logBytes) || 0,
    resultJson: resultJson,
    resultTruncated: resultTruncated,
    fromLedger: !!src.fromLedger,
  };
}

/** §8.3 clamp 口径：缺省/非数 → 默认值；给了数 → 夹进 [lo,hi]（**不报错**，照 §1.x 既有口径） */
function clampIntParam(v, dflt, lo, hi) {
  if (v === null || v === undefined || v === '') return dflt;
  const n = Number(v);
  if (!Number.isFinite(n)) return dflt;
  return Math.max(lo, Math.min(hi, Math.round(n)));
}
