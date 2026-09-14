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
import { createEngine } from './engine.mjs';
import { CFG, describeConfig } from './config.mjs';

const argv = process.argv.slice(2);
const portArg = argv.indexOf('--port');
const PORT = Number(portArg >= 0 ? argv[portArg + 1] : (process.env.VIEWPORT_PORT || CFG.httpPort));
const HOST = '127.0.0.1';

const engine = createEngine();
const stats = { frames: 0, acts: 0, cmds: 0, loops: 0, views: 0, headless: 0, plan: 0, worker: 0, txn: 0, lastPlanMs: null, lastViewMs: null, lastHeadlessMs: null, lastFrameMs: null, lastActMs: null, lastError: null, startedAt: Date.now() };

/**
 * 写权限租约：多 agent / 多会话同时驱动一个 Blender 时，靠它避免互相踩。
 * 语义：不持有租约时不拦（单机单 agent 完全无感）；一旦有人显式拿到租约，别人的写操作会被 409 挡住。
 * 过期即自动失效（TTL，默认 10 min，每次写操作自动续期）。
 */
const LEASE = { holder: null, token: null, acquiredAt: null, expiresAt: null, ttlMs: CFG.leaseTtlMs, renewals: 0 };
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
  };
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
  '/plan': ['status', 'help', 'ledger', 'check_envelope', 'check_interference', 'check_interface',
            'plan_status', 'plan_validate', 'plan_diag', 'plan_order', 'plan_graph'],
  '/worker': ['status'],
  '/txn': ['list', 'marks', 'help'] };
function isReadOnly(path, op) {
  const list = READ_ONLY_OPS[path];
  return !!(list && list.indexOf(String(op)) >= 0);
}

/** 请求带上 holder/force 时的门禁；返回 null = 放行 */
function leaseGate(payload) {
  const h = String((payload && (payload.holder || payload.lease)) || '');
  const force = !!(payload && payload.force);
  const v = leaseView();
  if (v.active && v.holder !== h && !force) {
    return { ok: false, error: 'leased', holder: v.holder, expiresInMs: v.expiresInMs,
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
    if (req.method === 'GET' && p === '/health') { json(res, 200, { ok: true, stats: stats, lease: leaseView(), requests: Object.fromEntries(reqCounts) }); return; }

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
        json(res, 200, { ok: true, region: s.region, stats: stats, metrics: m, diagnosis: m.lastError && m.lastDiagnosis ? m.lastDiagnosis : null });
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

    if (req.method === 'GET' && p === '/frame.png') {
      const size = Math.max(120, Math.min(1600, Number(url.searchParams.get('size') || 560)));
      const full = url.searchParams.get('full') === '1';
      const areaIndex = Number(url.searchParams.get('area') || 0);
      const t0 = Date.now();
      try {
        const out = full ? await engine.frameArea(areaIndex) : await engine.frame(size);
        stats.frames++; stats.lastFrameMs = Date.now() - t0; stats.lastError = null;
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
        stats.acts++; stats.lastActMs = Date.now() - t0; stats.lastError = null;
        // engine.act() 返回 {ok,executed,ms,mainThreadMs,stdout,stderr,error,traceback,file}，
        // host 侧（src/index.ts 的 rt_do / rt_watch）读的正是这些字段；这里按 out.result 取值会让
        // stdout 恒为空、file 参数丢失、异常被当成成功 —— 按 engine.act 的真实形状转发，并传下 file。
        json(res, 200, {
          ok: !(out && out.ok === false),
          ms: Date.now() - t0,
          executed: !!(out && out.executed),
          stdout: String((out && (out.stdout !== undefined ? out.stdout : out.result)) || ''),
          stderr: String((out && out.stderr) || ''),
          error: (out && out.error) || null,
          traceback: (out && out.traceback) || null,
          mainThreadMs: (out && out.mainThreadMs) || 0,
        });
      } catch (e) {
        stats.acts++; stats.lastError = String((e && e.message) || e);
        json(res, 200, { ok: false, ms: Date.now() - t0, error: stats.lastError, diagnosis: (e && e.diagnosis) || null });
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
        stats.views++; stats.lastViewMs = Date.now() - t0; stats.lastError = null;
        if (payload.asJson) { json(res, 200, { ok: true, ms: Date.now() - t0, meta: out.meta }); return; }
        res.writeHead(200, { 'content-type': 'image/png', 'cache-control': 'no-store',
          'x-dsh-view': JSON.stringify({ mode: out.meta.mode, ms: out.meta.ms, path: out.meta.path, width: out.meta.width, height: out.meta.height }).slice(0, 500) });
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
          stats.lastHeadlessMs = Date.now() - t0; stats.lastError = null;
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
      try {
        const out2 = await engine.plan(op, payload.args || {});
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
          if (op === 'start') return { ok: true, op: op, result: await engine.worker.start(payload) };
          if (op === 'stop') return { ok: true, op: op, result: await engine.worker.stop() };
          if (op === 'restart') { await engine.worker.stop(); return { ok: true, op: op, result: await engine.worker.start(payload) }; }
          if (op === 'exec') {
            const r = await engine.worker.exec(String(payload.code || ''), Number(payload.timeoutMs) || 120000);
            return { ok: !!r.ok, op: op, result: r };
          }
          return { ok: true, op: 'status', result: await engine.worker.status() };
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
  console.log('[blender-rt] http://' + HOST + ':' + String(PORT) + '/  (routes: /health /status /doctor /who /frame.png /act /view /headless /plan /worker /txn /lease)');
});
process.on('SIGINT', () => { engine.stop(); server.close(); process.exit(0); });
process.on('SIGTERM', () => { engine.stop(); server.close(); process.exit(0); });
