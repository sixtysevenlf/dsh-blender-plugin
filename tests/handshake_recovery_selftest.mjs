#!/usr/bin/env node
/**
 * 握手自愈自检 —— 针对这个现场 bug（用户实机复现，面板永久消失）：
 *
 *   ① 后端首次解析协议时探测没收到回包（Blender 主线程忙）⇒ resolveProtocol 把协议缓存成 `fallback`
 *      （addon-protocol.mjs:241-244）；
 *   ② 但后续命令用 fallback 协议**照样成功** ⇒ `noteHandshake()`（engine.mjs:768）把
 *      `readiness.handshakeFrom` 记成 `'fallback'`；
 *   ③ `/host/ready` 按契约只读缓存、零 I/O ⇒ 永久返回 `{ready:false, reason:'handshake-stale'}`；
 *   ④ 客户端按设计在 ready=false 时注销右侧栏标签 ⇒ **用户的面板永久消失**，
 *      而重启 DSH 只会让①重新掷一次骰子。
 *
 * 为什么必须自动化：这个状态在人工排查里极易被误判成"通道坏了"——
 * 实际 `/doctor` 是 kind=ok、ping 通、协议号正常，只有 `/host/ready` 说 false。
 *
 * 复现手法：mock A = **吞掉协议探测包、但正常回答业务命令**
 *   （精确模拟"探测那一下漏了、命令还能用"，也就是用户机器上的现场）。
 * 修复判据：把 mock A 换成会回探测包的 mock B（**同一端口**）后，
 *   **无需任何人工干预**，`/host/ready` 必须自己回到 `ready:true`。
 *
 * 前提断言：第 ⑤ 步必须真的复现出 handshake-stale。复现不出来就直接判失败——
 *   一个没复现现场的测试不能算通过（否则"改坏了"和"修好了"都表现为绿）。
 *
 * 用法：node tests/handshake_recovery_selftest.mjs [--runtime <dir>]
 *   --runtime 指向含 server.mjs/engine.mjs 的目录（默认本仓库 runtime/），
 *   用来对未修复版本跑阴性对照。
 * 退出码：0 = 通过；1 = 失败（未修复版本应为 1）
 */
import net from 'node:net';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.join(HERE, '..');
const argv = process.argv.slice(2);
const ri = argv.indexOf('--runtime');
const RUNTIME = ri >= 0 && argv[ri + 1] ? path.resolve(argv[ri + 1]) : path.join(ROOT, 'runtime');
const HTTP_PORT = Number(process.env.DSH_HANDSHAKE_TEST_HTTP_PORT || 9897);
const RECOVER_WINDOW_MS = Number(process.env.DSH_HANDSHAKE_TEST_WINDOW_MS || 25000);

let pass = 0, fail = 0;
const failures = [];
function ok(name, cond, detail) {
  if (cond) { pass++; console.log('  ✓ ' + name); }
  else { fail++; failures.push(name); console.log('  ✗ ' + name + (detail !== undefined ? '  → ' + detail : '')); }
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const BASE = 'http://127.0.0.1:' + HTTP_PORT;
/** 写租约身份：必须与后端默认 holder **一致**，否则 /cmd 会被 409 leased
 *  （实测踩到：只取租约、不在 body 里带同一 holder ⇒ /cmd 用后端默认 holder 写 → 被自己挡）。 */
const HOLDER = 'plugin-pid-' + String(process.pid);
async function req(method, route, body, timeoutMs = 20000) {
  try {
    const r = await fetch(BASE + route, {
      method,
      headers: body === undefined ? {} : { 'content-type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: AbortSignal.timeout(timeoutMs),
    });
    let parsed = null;
    try { parsed = await r.json(); } catch (e) { /* 非 JSON */ }
    return { status: r.status, body: parsed };
  } catch (e) {
    return { status: 0, body: null, err: String((e && e.message) || e) };
  }
}

/**
 * mock addon：`state.deaf=true` 时**吞掉两种探测包**（不回复），其余业务命令照常回扁平封套。
 * 用可变的 state 而不是"换一个服务器"：现场是**同一台 addon、探测只漏了一次**，
 * 同一 socket 上翻转开关才是单变量对照；换服务器会顺带断掉后端 socket，
 * 把状态变成 addon-unreachable（那是另一个 reason，测不到本 bug）。
 */
function makeMock(state) {
  const sockets = new Set();
  const srv = net.createServer((sock) => {
    let buf = '';
    sock.on('data', (d) => {
      buf += d.toString('utf8');
      for (;;) {
        let line = null;
        const nl = buf.indexOf('\n');
        if (nl >= 0) { line = buf.slice(0, nl); buf = buf.slice(nl + 1); }
        else {
          try { JSON.parse(buf); line = buf; buf = ''; } catch (e) { break; }
        }
        if (!line || !line.trim()) continue;
        let r = null;
        try { r = JSON.parse(line); } catch (e) { continue; }
        // 两种探测包都要认得（实测：category-action 的探测是 system.get_info，不是 ping）
        const isFlatProbe = r.type === 'ping';
        const isCaProbe = r.type === 'command' && r.category === 'system' && r.action === 'get_info';
        if (state.deaf && (isFlatProbe || isCaProbe)) continue;   // ← 现场：探测那一下漏了
        let reply;
        if (r.type === 'get_scene_info') reply = { status: 'success', result: { name: 'Scene-mock', object_count: 3 } };
        else if (r.type === 'get_addon_info') reply = { status: 'success', result: { protocol_version: 13, version_string: 'mock' } };
        else if (r.type === 'execute_code') reply = { status: 'success', result: { executed: true, result: 'mock-ok' } };
        else if (r.type === 'get_viewport_screenshot') reply = { status: 'success', result: { output_path: (r.params && r.params.filepath) || '', bytes: 1024 } };
        else if (isFlatProbe || isCaProbe) reply = { status: 'success', result: { pong: true } };
        else reply = { status: 'error', message: 'mock: unknown type ' + String(r.type) };
        try { sock.write(JSON.stringify(reply)); } catch (e) { /* ignore */ }
      }
    });
    sock.on('error', () => {});
  });
  srv.on('connection', (s) => { sockets.add(s); s.on('close', () => sockets.delete(s)); });
  srv.__sockets = sockets;
  return srv;
}

function listenOn(srv, port) {
  return new Promise((resolve, reject) => {
    srv.once('error', reject);
    srv.listen(port, '127.0.0.1', () => resolve(true));
  });
}
const close = (srv) => new Promise((r) => {
  if (!srv) return r(true);
  // ⚠ 两个坑（都实测踩过）：
  //   ① server.close() 会**等已有连接结束**，而后端正握着到 mock 的 socket ⇒ 不强制断开就永远卡住；
  //   ② `closeAllConnections()` 只存在于 http.Server，**net.Server 没有** —— 写成它会被 try/catch 吞掉，
  //      表现为"看着优雅、实际没断"，最后靠看门狗才暴露。所以自己跟踪 socket 并 destroy。
  try { for (const s of (srv.__sockets || [])) { try { s.destroy(); } catch (e) {} } } catch (e) {}
  try { srv.close(() => r(true)); } catch (e) { r(false); }
});
const freePort = () => new Promise((resolve, reject) => {
  const s = net.createServer();
  s.once('error', reject);
  s.listen(0, '127.0.0.1', () => { const p = s.address().port; s.close(() => resolve(p)); });
});

console.log('== 握手自愈自检 ==');
console.log('runtime : ' + RUNTIME);
console.log('http    : ' + BASE);

const TMP = fs.mkdtempSync(path.join(os.tmpdir(), 'dsh-handshake-'));
let backend = null;
let srvA = null, srvB = null;
let log = '';
const cleanup = () => {
  try { if (backend) backend.kill('SIGKILL'); } catch (e) {}
  for (const srv of [srvA, srvB]) {
    try { for (const s of (srv && srv.__sockets) || []) { try { s.destroy(); } catch (e) {} } } catch (e) {}
    try { if (srv) srv.close(); } catch (e) {}
  }
  try { fs.rmSync(TMP, { recursive: true, force: true }); } catch (e) {}
};
process.on('exit', cleanup);
// 看门狗：测试绝不允许挂死（挂死的测试比失败的测试更糟 —— 会阻塞整条流水线）
const WATCHDOG_MS = Number(process.env.DSH_HANDSHAKE_TEST_WATCHDOG_MS || 120000);
const watchdog = setTimeout(() => {
  console.log('  ✗ 看门狗超时（' + Math.round(WATCHDOG_MS / 1000) + 's）——测试被判定失败，避免挂死');
  cleanup();
  process.exit(1);
}, WATCHDOG_MS);
watchdog.unref?.();

try {
  const addonPort = await freePort();
  const state = { deaf: true };
  srvA = makeMock(state);
  await listenOn(srvA, addonPort);

  backend = spawn(process.execPath, [path.join(RUNTIME, 'server.mjs'), '--port', String(HTTP_PORT)], {
    env: Object.assign({}, process.env, {
      DSH_BLENDER_WORKDIR: TMP,
      DSH_BLENDER_HTTP_PORT: String(HTTP_PORT),
      DSH_BLENDER_ADDON_PORT: String(addonPort),
      DSH_BLENDER_HOLDER: HOLDER,
    }),
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  backend.stdout.on('data', (d) => { log += d.toString('utf8'); });
  backend.stderr.on('data', (d) => { log += d.toString('utf8'); });

  let up = false;
  for (let i = 0; i < 50; i++) {
    const h = await req('GET', '/health', undefined, 3000);
    if (h.status === 200) { up = true; break; }
    await sleep(300);
  }
  ok('① 隔离后端已就绪', up, log.slice(-300));
  if (!up) throw new Error('后端起不来');

  // 拿写租约（/cmd 是写路由）；holder 必须与下面 body 里带的一致
  await req('POST', '/lease', { holder: HOLDER }, 8000);

  // ② 发一条业务命令：探测会失败（mock A 吞 ping），但命令本身必须成功
  const cmd = await req('POST', '/cmd', { name: 'get_scene_info', params: {}, holder: HOLDER }, 30000);
  ok('② mock A 吞掉探测包、但业务命令成功（复现前提）',
    cmd.status === 200 && cmd.body && cmd.body.ok === true,
    JSON.stringify(cmd.body).slice(0, 240));

  // ③ 复现：ready 必须为 false 且 reason 恰为 handshake-stale
  let ready = (await req('GET', '/host/ready', undefined, 8000)).body || {};
  ok('③ 复现 handshake-stale（socket 在、握手是 fallback）',
    ready.ready === false && ready.reason === 'handshake-stale',
    JSON.stringify(ready));
  ok('③b 此时 addon 仍报 connected（所以人工排查会误判成"通道没问题"）',
    ready.addon === true, JSON.stringify(ready));

  // ④ 让同一个 addon 开始回答探测包（现场：主线程空下来，探测终于能回包）
  //    单变量、同 socket、同端口 —— 不引入 addon-unreachable 这个无关状态
  state.deaf = false;
  const probeNow = await req('GET', '/doctor', undefined, 25000);
  ok('④ addon 开始回探测包（doctor 复现通道健康）',
    !!(probeNow.body && probeNow.body.addon && probeNow.body.addon.kind === 'ok'),
    JSON.stringify(probeNow.body && probeNow.body.addon).slice(0, 200));

  // ⑤ 修复判据：无需人工干预，ready 必须自己回到 true
  const t0 = Date.now();
  let recovered = false, last = null;
  while (Date.now() - t0 < RECOVER_WINDOW_MS) {
    const r = (await req('GET', '/host/ready', undefined, 8000)).body || {};
    last = r;
    if (r.ready === true) { recovered = true; break; }
    await sleep(1000);
  }
  ok('⑤ 探测恢复后 /host/ready 自愈为 ready:true（无需人工干预、无需重启）',
    recovered, '等了 ' + Math.round((Date.now() - t0) / 1000) + 's，最后状态 ' + JSON.stringify(last));
} catch (e) {
  fail++;
  failures.push('异常：' + String((e && e.message) || e));
  console.log('  ✗ 异常：' + String((e && e.message) || e));
} finally {
  cleanup();
}

console.log('');
console.log('─'.repeat(64));
console.log('handshake-recovery-selftest：' + pass + ' 通过 / ' + fail + ' 失败');
if (fail) for (const f of failures) console.log('  - ' + f);
process.exit(fail ? 1 : 0);
