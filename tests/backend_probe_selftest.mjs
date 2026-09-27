#!/usr/bin/env node
/**
 * 后端探活与启动决策自检（S7 回归）—— 针对这个现场 bug：
 *   `op=start` 只看子进程句柄，句柄还在但端口没监听时回「已在跑」，用户拿到打不通的后端。
 * 覆盖：探 TCP/HTTP 的真假、决策表三态、waitHttp 的"迟到成功"与超时、以及 lib 能否解析新导入。
 */
import http from 'node:http';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { probeTcp, probeHttp, decideStart, waitHttp } from '../runtime/backend_probe.mjs';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.join(HERE, "..");
let pass = 0; let fail = 0; const fails = [];
const ok = (name, cond, extra) => { if (cond) pass++; else { fail++; fails.push({ clause: name, detail: extra }); } };

// ① 起一个真 HTTP 服务当"后端"
const srv = http.createServer((req, res) => { res.writeHead(200); res.end("ok"); });
await new Promise((r) => srv.listen(0, "127.0.0.1", r));
const port = srv.address().port;
const url = "http://127.0.0.1:" + port + "/health";
ok("服务在跑时 probeHttp=true", (await probeHttp(url, 800)) === true);
ok("服务在跑时 probeTcp=true", (await probeTcp("127.0.0.1", port, 500)) === true);

// ② 关掉服务 → 两个探活都必须 false（旧实现正是在这里失真）
await new Promise((r) => srv.close(r));
ok("服务关掉后 probeHttp=false", (await probeHttp(url, 800)) === false);
ok("服务关掉后 probeTcp=false", (await probeTcp("127.0.0.1", port, 400)) === false);

// ③ 决策表：端口能服务就是 already-running；端口不通但句柄在 ⇒ 必须判为陈旧句柄
ok("决策表：端口通 → already-running", decideStart({ portUp: true, childAlive: true }) === "already-running");
ok("决策表：端口不通+句柄在 → stale-handle", decideStart({ portUp: false, childAlive: true }) === "stale-handle");
ok("决策表：端口不通+无句柄 → spawn", decideStart({ portUp: false, childAlive: false }) === "spawn");

// ④ waitHttp：迟到的服务要等到，纯超时要如实回 false
const late = http.createServer((req, res) => { res.writeHead(200); res.end("ok"); });
setTimeout(() => late.listen(port, "127.0.0.1"), 700);
ok("waitHttp 能等到迟到 700ms 的服务", (await waitHttp(url, 6000, 200)) === true);
await new Promise((r) => late.close(r));
ok("waitHttp 纯超时回 false（不谎报）", (await waitHttp(url, 1200, 200)) === false);

// ⑤ lib 里新导入的说明符必须能解析（编译产物在包根，../runtime/ 才对）
let libOk = false; let libErr = "";
try {
  await import(path.join(ROOT, "runtime", "backend_probe.mjs"));
  libOk = true;
} catch (e) { libErr = String(e.message).slice(0, 120); }
ok("runtime/backend_probe.mjs 可被 lib 侧解析", libOk, libErr);

// ⑥ 真后端探活（有后端时应为 true；没有则跳过，不算失败）
const realUp = await probeHttp("http://127.0.0.1:9877/health", 1200);
console.log("指引：真后端 127.0.0.1:9877/health → " + (realUp ? "在跑" : "未跑（跳过，不计失败）"));

console.log("探活自检：" + pass + " 通过 / " + fail + " 失败");
if (fail) for (const f of fails) console.log("  x " + f.clause + (f.detail ? " — " + JSON.stringify(f.detail) : ""));
process.exit(fail ? 1 : 0);