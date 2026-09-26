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
import { createEngine, engineProvenance, PLAN_READ_ONLY_OPS } from './engine.mjs';
import { CFG, describeConfig } from './config.mjs';

const argv = process.argv.slice(2);
const portArg = argv.indexOf('--port');
const PORT = Number(portArg >= 0 ? argv[portArg + 1] : (process.env.VIEWPORT_PORT || CFG.httpPort));
const HOST = '127.0.0.1';

const engine = createEngine();
const stats = { frames: 0, acts: 0, cmds: 0, loops: 0, views: 0, headless: 0, plan: 0, worker: 0, txn: 0, preset: 0, job: 0, lastPlanMs: null, lastViewMs: null, lastHeadlessMs: null, lastFrameMs: null, lastActMs: null, lastError: null, startedAt: Date.now() };

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
const LEASE = { holder: null, token: null, acquiredAt: null, expiresAt: null, ttlMs: CFG.leaseTtlMs, renewals: 0 };
/**
 * v0.9.4：租约持有者**是否还活着**（实测踩到：DSH 会话崩了/被关掉之后，它的租约在内存里
 * 继续生效到 TTL（默认 1 h），把别的会话的写通道整段挡死 —— 而那个会话早就不存在了）。
 * holder 约定是 `plugin-pid-<pid>`（插件前端 HOLDER，见 src/index.ts），后端与它同机 → 能直接探活。
 * 返回 true/false；**查不出来返回 null**（按"活着"处理，绝不误抢别人的租约）。
 */
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
function leaseAcquire(holder, ttlMs, force) {
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
  LEASE.expiresAt = now + LEASE.ttlMs;
  return { granted: true, lease: leaseView() };
}
/** 只读 op：即便别人持有租约也放行（否则连"看看现状"都会被挡） */
const READ_ONLY_OPS = { '/perf': ['status', 'help'], '/loop': ['status', 'board', 'help'], '/opt': ['opt_analyze', 'analyze', 'help'],
  '/plan': PLAN_READ_ONLY_OPS,'/worker': ['status', 'list'],
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
    return { ok: false, error: 'leased', holder: v.holder, expiresInMs: v.expiresInMs, holderAlive: v.holderAlive,
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

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, 'http://' + HOST + ':' + String(PORT));
  const p = url.pathname;
  bump(p);
  try {
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
          const rr = leaseAcquire(holder, payload.ttlMs, false);
          json(res, 200, { ok: true, lease: rr.lease, renewed: true });
        } else {
          json(res, 200, { ok: true, lease: v, renewed: false });
        }
        return;
      }
      const r = leaseAcquire(holder, payload.ttlMs, !!payload.force);
      if (r.denied) {
        json(res, 409, { ok: false, error: 'leased', holder: r.holder, expiresInMs: r.expiresInMs,
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
      const gate = isReadOnly('/worker', op) ? null : leaseGate(payload);
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
try {
  await engine.start();
} catch (e) {
  console.error('[blender-rt] 启动时未连上 addon（Blender 未运行或 addon 未监听 9876）：' + String((e && e.message) || e));
}
server.listen(PORT, HOST, () => {
  console.log('[blender-rt] http://' + HOST + ':' + String(PORT) + '/  (routes: /health /status /doctor /who /frame.png /act /view /headless /plan /worker /txn /preset /lease)');
});
process.on('SIGINT', () => { engine.stop(); server.close(); process.exit(0); });
process.on('SIGTERM', () => { engine.stop(); server.close(); process.exit(0); });
