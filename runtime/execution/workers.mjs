// 热无头 worker 会话池（T6）
// @imports
import { CFG, winToWsl, wslToWin } from "../config.mjs";
import path from "node:path";
import { BLENDER_EXE, USER_CONFIG_WIN, USER_SCRIPTS_WIN, WIN_TMP, WORKER_PATH } from "../paths.mjs";
import fs from "node:fs";
import { KERNEL_BOOTSTRAP } from "../core/kernel.mjs";
import { spawn } from "node:child_process";
import net from "node:net";


export function createWorkersApi(E) {
  const { metrics, preloadChunk } = E;
  // ---------------------------------------------------------------- 热无头 worker
  // 常驻 blender -b：阻塞 accept 跑在主线程（无头下 timers 不触发，主线程执行 bpy 才安全），
  // 复用持久内核 K；一次只处理一个请求（长代码会占住 worker —— 这是"热"的代价，也是安全的来源）。
  //
  // v0.9.4（P1-1）**多实例**：实测小作业的固定开销 ≈2.7 s（spawn 1.0 s + 首帧着色器编译 1.6 s）占 85%，
  // 而 2 并发只慢 ~10%（GPU 有余量）⇒ 批量小活应当**复用热进程**，不是每次 spawn。
  // 名字缺省 `default`（老调用逐字节不变）；额外实例按 name 各占一个端口（配置端口起顺延）。
  // ⚠ 复用 = 主动放弃"进程隔离"：同一 worker 里的作业共享场景与 K，脚本要自己保证无副作用残留；
  //    需要干净场景时用 `op=restart name=…`，或继续用 blender_rt_headless（每次新进程）。
  const WORKER_PORT = Number(process.env.DSH_BLENDER_WORKER_PORT) || CFG.workerPort || 9879;
  const WORKERS = new Map();
  function workerKey(n) {
    const k = String(n === undefined || n === null || n === '' ? 'default' : n).trim().toLowerCase()
      .replace(/[^a-z0-9_-]+/g, '-').slice(0, 24);
    return k || 'default';
  }
  function workerState(name) {
    const key = workerKey(name);
    let w = WORKERS.get(key);
    if (!w) {
      const used = new Set(Array.from(WORKERS.values()).map((x) => x.port));
      let p = WORKER_PORT;
      while (used.has(p)) p += 1;      // 端口顺延，避免两个实例撞车
      w = { name: key, child: null, port: p, ready: false, startedAt: null, gpu: null, pid: null,
            lastError: null, stdoutTail: '', stderrTail: '' };
      WORKERS.set(key, w);
    }
    return w;
  }
  function workerAlive(w) { return !!(w && w.child && w.child.exitCode === null && !w.child.signalCode); }
  function workerSnapshot(w) {
    return { name: w.name, alive: workerAlive(w), ready: w.ready, pid: w.pid, port: w.port,
             uptimeMs: w.startedAt ? Date.now() - w.startedAt : null,
             idleMs: w.lastUsed ? Date.now() - w.lastUsed : null,
             gpu: w.gpu, boot: w.boot || null,
             lastError: w.lastError, stdoutTail: w.stdoutTail ? w.stdoutTail.slice(-1200) : null };
  }
  function workerList() { return Array.from(WORKERS.values()).map(workerSnapshot); }
  async function workerStart(opts = {}, nameArg) {
    const w = workerState(nameArg !== undefined ? nameArg : opts.name);
    if (workerAlive(w) && w.ready) return workerSnapshot(w);
    if (w.child) { try { w.child.kill('SIGKILL'); } catch (e) {} w.child = null; }
    const port = Number(opts.port) || w.port;
    const gpuMode = String(opts.gpu === undefined || opts.gpu === null ? 'auto' : opts.gpu);
    const engineMode = String(opts.engine === undefined || opts.engine === null ? 'eevee' : opts.engine);
    // P0：preload —— 把 KERNEL_BOOTSTRAP + 模块源码写成 boot 文件，worker 在 ready 前 exec。
    // 旧 worker 只注入 K/bpy，热会话里 K.dsh_audit_api 等一律不存在（这是 C 组调研的最主要缺口）。
    const preloadRaw = opts.preload === undefined || opts.preload === null ? [] : opts.preload;
    const preloadList = (Array.isArray(preloadRaw) ? preloadRaw : String(preloadRaw).split(','))
      .map((x) => String(x).trim().replace(/\.py$/, '')).filter(Boolean);
    let bootWin = null;
    if (preloadList.length) {
      try {
        const bootWsl = path.join(winToWsl(WIN_TMP), 'worker_boot_' + w.name + '.py');
        fs.mkdirSync(path.dirname(bootWsl), { recursive: true });
        const bootSrc = [KERNEL_BOOTSTRAP].concat(preloadList.map((m) => preloadChunk(m))).join(String.fromCharCode(10));
        fs.writeFileSync(bootWsl, bootSrc, 'utf8');
        bootWin = wslToWin(bootWsl);
      } catch (e) {
        const err = new Error('worker preload 前导写盘失败：' + String((e && e.message) || e));
        err.hint = '检查 workDir 是否可写（DSH_BLENDER_WORKDIR）';
        throw err;
      }
    }
    const args = ['-b', '--factory-startup', '--python', wslToWin(WORKER_PATH), '--',
                  '--port', String(port), '--gpu', gpuMode, '--engine', engineMode,
                  '--frame-dir', String(WIN_TMP)];
    if (bootWin) args.push('--boot', bootWin);
    const childEnv = Object.assign({}, process.env, { PYTHONIOENCODING: 'utf-8', DSH_WORKER_FRAME_DIR: String(WIN_TMP) });
    if (opts.useUserConfig) {
      if (USER_CONFIG_WIN) childEnv.BLENDER_USER_CONFIG = USER_CONFIG_WIN;
      if (USER_SCRIPTS_WIN) childEnv.BLENDER_USER_SCRIPTS = USER_SCRIPTS_WIN;
    }
    const child = spawn(BLENDER_EXE, args, { env: childEnv, stdio: ['ignore', 'pipe', 'pipe'], windowsHide: true });
    w.child = child; w.port = port; w.ready = false; w.startedAt = Date.now();
    w.gpu = null; w.pid = child.pid; w.lastError = null; w.stdoutTail = ''; w.stderrTail = '';
    const info = await new Promise((resolve) => {
      let so = '', se = '';
      const t = setTimeout(() => resolve({ timedOut: true }), Math.max(20000, Number(opts.startTimeoutMs) || 90000));
      child.stdout.on('data', (d) => {
        so += d.toString('utf8'); w.stdoutTail = so.slice(-4000);
        const m = so.match(/DSH_WORKER (\{[\s\S]*?\})\s*(\r?\n|$)/);
        if (m) { try { clearTimeout(t); resolve({ ok: JSON.parse(m[1]) }); } catch (e) { /* 继续攒 */ } }
      });
      child.stderr.on('data', (d) => { se += d.toString('utf8'); w.stderrTail = se.slice(-4000); });
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
      e.hint = 'worker stderr 尾部：' + String(w.stderrTail || '').slice(-500);
      try { child.kill('SIGKILL'); } catch (x) {}
      throw e;
    }
    const boot = info.ok.boot || null;
    if (boot && boot.ok === false) {
      const e = new Error('worker boot/preload 失败：' + String(boot.error || 'unknown'));
      e.hint = 'boot stderr 尾部：' + String(boot.stderr || '').slice(-500) + '；检查 preload 模块名（runtime/<name>.py）';
      try { child.kill('SIGKILL'); } catch (x) {}
      throw e;
    }
    w.ready = true; w.gpu = info.ok.gpu || null; w.pid = info.ok.pid || child.pid; w.boot = boot;
    w.lastUsed = Date.now();
    metrics.workerStarts++;
    // P0 交互式 headless：首帧 Workbench 渲染有 ~1 s 的渲染器初始化（本机实测 first 1003ms / second 13ms）。
    // 在 start 时做一次极小 warmup（64×36），把这一秒付掉；之后每次 see 才是 10–40ms。
    let warm = null;
    if (opts.warmup !== false) {
      try {
        const wr = await workerCall(w, { op: 'see', spec: { width: 64, height: 36, aa: '2' } }, 60000);
        warm = { ok: !!(wr && wr.ok !== false), ms: (wr && wr.ms) || null };
      } catch (err) {
        warm = { ok: false, error: String((err && err.message) || err).slice(0, 200) };
      }
    }
    return Object.assign(workerSnapshot(w), { hello: info.ok, warmup: warm });
  }
  /** 与 worker 的单次请求-应答（每次新建短连接，避免残留状态） */
  function workerCall(w, req, timeoutMs = 120000) {
    return new Promise((resolve, reject) => {
      const id = Math.floor(Math.random() * 1e9);
      const s = net.connect({ host: '127.0.0.1', port: w.port });
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
  async function workerExec(code, timeoutMs = 120000, purgePrefixes = null, nameArg) {
    const w = workerState(nameArg);
    if (!workerAlive(w) || !w.ready) await workerStart({}, w.name);
    w.lastUsed = Date.now();
    metrics.workerExecs++;
    try { return await workerCall(w, { op: 'exec', code: String(code || ''), purgePrefixes: purgePrefixes || undefined }, timeoutMs); }
    catch (e) { w.lastError = String((e && e.message) || e); throw e; }
  }
  async function workerSee(spec, nameArg) {
    const w = workerState(nameArg);
    if (!workerAlive(w) || !w.ready) await workerStart({}, w.name);
    w.lastUsed = Date.now();
    metrics.workerExecs = (metrics.workerExecs || 0) + 1;
    try { return await workerCall(w, { op: 'see', spec: spec || {} }, 60000); }
    catch (e) { w.lastError = String((e && e.message) || e); throw e; }
  }
  async function workerWatch(spec, nameArg) {
    const w = workerState(nameArg);
    if (!workerAlive(w) || !w.ready) await workerStart({ engine: 'workbench', warmup: true }, w.name);
    w.lastUsed = Date.now();
    metrics.workerExecs = (metrics.workerExecs || 0) + 1;
    try { return await workerCall(w, { op: 'watch', spec: spec || {} }, 180000); }
    catch (e) { w.lastError = String((e && e.message) || e); throw e; }
  }
  async function workerReset(nameArg) {
    const w = workerState(nameArg);
    if (!workerAlive(w) || !w.ready) await workerStart({}, w.name);
    try { return await workerCall(w, { op: 'reset' }, 60000); }
    catch (e) { w.lastError = String((e && e.message) || e); throw e; }
  }
  async function workerStatus(nameArg) {
    const w = workerState(nameArg);
    if (!workerAlive(w) || !w.ready) return workerSnapshot(w);
    w.lastUsed = Date.now();
    try { const r = await workerCall(w, { op: 'status' }, 15000); return Object.assign(workerSnapshot(w), { status: r.status || null }); }
    catch (e) { w.lastError = String((e && e.message) || e); return Object.assign(workerSnapshot(w), { statusError: w.lastError }); }
  }
  async function workerStop(nameArg) {
    const w = workerState(nameArg);
    if (!workerAlive(w)) { w.ready = false; return { name: w.name, stopped: false, wasAlive: false }; }
    let bye = null;
    try { bye = await workerCall(w, { op: 'shutdown' }, 8000); } catch (e) { /* 直接杀 */ }
    await new Promise((r) => setTimeout(r, 300));
    try { if (workerAlive(w)) w.child.kill('SIGKILL'); } catch (e) {}
    w.ready = false;
    return { name: w.name, stopped: true, wasAlive: true, bye: bye };
  }
  /** 重启：换一个干净会话（复用 = 放弃进程隔离，脚本脏了就用它） */
  async function workerRestart(nameArg, opts) { await workerStop(nameArg); return workerStart(opts || {}, nameArg); }
  // P0 会话池：空闲回收（默认 15 min；DSH_WORKER_TTL_MS 可调；0=关闭）。只回收 ready 且无请求在飞的热进程。
  const WORKER_TTL_MS = (() => {
    const n = Number(process.env.DSH_WORKER_TTL_MS);
    return Number.isFinite(n) && n >= 0 ? n : 900000;
  })();
  let workerReaper = null;
  if (WORKER_TTL_MS > 0) {
    workerReaper = setInterval(() => {
      const now = Date.now();
      for (const w of WORKERS.values()) {
        if (!workerAlive(w) || !w.ready) continue;
        if (w.lastUsed && now - w.lastUsed > WORKER_TTL_MS) {
          try { workerStop(w.name).catch(() => {}) } catch (e) { /* 下一轮再试 */ }
        }
      }
    }, 30000);
    try { workerReaper.unref && workerReaper.unref() } catch (e) {}
  }
  function clearWorkerReaper() { if (workerReaper) { try { clearInterval(workerReaper); } catch (e) {} workerReaper = null; } }
  function killWorkers() { try { for (const w of WORKERS.values()) { if (workerAlive(w)) w.child.kill("SIGKILL"); } } catch (e) {} }
  return { workerStart, workerExec, workerSee, workerWatch, workerReset, workerStatus, workerStop, workerRestart, workerList, workerSnapshot, workerState, clearWorkerReaper, killWorkers };
}
