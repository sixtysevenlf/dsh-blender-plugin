#!/usr/bin/env node
/**
 * 路由契约测试 —— 拆 createEngine 的前置条件（也是"有没有 op 没接线"的常规体检）。
 *
 * 为什么需要它：现有 263 条断言偏"领域正确性"，不覆盖"每个 op 都能被路由到"。
 * 这几轮实际撞过两次：目录漏列 `vehicle_loft`（模型看不见）、`perf` 模块没有 dispatch（单步调用失败）。
 *
 * 两层：
 *   ① 静态：每个 family 的前缀在 engine.mjs 里都有路由（EXT_ROUTES 条目或 onIndexOf 分支）；
 *   ② 真机：把 /plan 只读白名单（PLAN_READ_ONLY_OPS）逐个空参调用，断言**不出现路由失败**
 *      （ok:false / degraded / 参数校验报错都算"路由正常"—— 我们要证的是"接上了"，不是"空参也能跑"）。
 *
 * 后端/Blender 不在时**跳过**（exit 0），不阻塞离线 npm test；单独跑：npm run test:routes
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.join(HERE, "..");
const BASE = process.env.DSH_BLENDER_BACKEND || "http://127.0.0.1:9877";
const engine = await import(path.join(ROOT, "runtime", "engine.mjs"));

let pass = 0;
let fail = 0;
const fails = [];
function ok(name, cond, extra) {
  if (cond) { pass++; }
  else { fail++; fails.push({ clause: name, detail: extra === undefined ? null : extra }); }
}

// ── 0) 后端可用性（不可用=跳过，不阻塞离线）
let doctor = null;
try {
  const r = await fetch(BASE + "/doctor", { signal: AbortSignal.timeout(4000) });
  doctor = await r.json();
} catch (e) { doctor = null; }
const dk = doctor && (doctor.addon && doctor.addon.kind ? doctor.addon.kind : doctor.kind);
if (!doctor || String(dk) !== "ok") {
  console.log("路由契约：跳过（后端/Blender 不可用：" + (dk || "unreachable") + "）");
  process.exit(0);
}

// ── ① 静态：每个 family 前缀都要有路由
const SRC = fs.readFileSync(path.join(ROOT, "runtime", "engine.mjs"), "utf8");
const cat = engine.catalogPayload({});
const PLAN = engine.PLAN_CATALOG || (await import(path.join(ROOT, 'runtime', 'catalog.mjs'))).PLAN_CATALOG;
const families = PLAN;
ok("目录非空（family >= 20）", families.length >= 20, families.length);
const noRoute = [];
for (const f of families) {
  const prefix = String(f.prefix || "");
  if (!prefix) {
    // 空前缀家族（contract / montage / render_guard…）：裸 op 名走 plan/契约兜底
    if (SRC.includes("dsh_plan_api") || SRC.includes("dsh_render_guard")) continue;
    noRoute.push(String(f.f) + " (无前缀且无兜底)");
    continue;
  }
  const stem = prefix.replace(/_$/, "");   // qc_render 这类无下划线分支
  const hasExt = SRC.includes("p: '" + prefix + "'");
  const hasBranch = SRC.includes("indexOf('" + prefix + "') === 0") || SRC.includes("indexOf('" + stem + "') === 0")
    || SRC.includes("startsWith('" + prefix + "')") || SRC.includes("startsWith('" + stem + "')");
  if (!(hasExt || hasBranch)) noRoute.push(String(f.f) + " -> " + prefix);
}
ok("每个 family 前缀都有路由（EXT_ROUTES 或 indexOf 分支）", noRoute.length === 0, noRoute.slice(0, 8));

// ── ② 真机：只读 op 逐个空参调用，只判"路由是否接上"
const ROUTE_FAIL = [/unknown op/i, /did_you_mean/i, /没有.*路由/, /not routed/i, /未知 ?op/i, /no such op/i, /Cannot read propert/i, /is not a function/i];
async function plan(op, args) {
  const r = await fetch(BASE + "/plan", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ op, args: args || {} }),
    signal: AbortSignal.timeout(60000),
  });
  const j = await r.json().catch(() => ({}));
  return j;
}
const roOps = engine.PLAN_READ_ONLY_OPS || [];
ok("只读白名单非空（>= 40）", roOps.length >= 40, roOps.length);
const routed = [];
const routingFails = [];
const summary = { ok: 0, rejectedArgs: 0, degraded: 0 };
for (const op of roOps) {
  let j = null;
  try { j = await plan(op, {}); } catch (e) { routingFails.push({ op, why: "请求异常: " + String(e.message).slice(0, 60) }); continue; }
  const text = JSON.stringify(j).slice(0, 2000);
  const bad = ROUTE_FAIL.find((re) => re.test(text));
  if (bad) { routingFails.push({ op, why: text.slice(0, 120) }); continue; }
  routed.push(op);
  const res = j.result;
  if (res && typeof res === "object" && res.ok === false) summary.rejectedArgs++;
  else if (res && typeof res === "object" && (res.verdict === "degraded" || res.state === "degraded")) summary.degraded++;
  else summary.ok++;
}
ok("只读 op 全部可路由（" + roOps.length + " 个逐个空参调用）", routingFails.length === 0, routingFails.slice(0, 6));

console.log("路由契约：" + pass + " 通过 / " + fail + " 失败");
console.log("  静态：family " + families.length + " 个前缀全部有路由" + (noRoute.length ? "（缺 " + noRoute.length + "）" : ""));
console.log("  真机：" + routed.length + "/" + roOps.length + " 个只读 op 接上了 —— 正常回执 " + summary.ok +
  " · 空参被拒(正常) " + summary.rejectedArgs + " · degraded " + summary.degraded);
if (fail) { for (const f of fails.slice(0, 8)) console.log("  x " + f.clause + (f.detail ? " — " + JSON.stringify(f.detail).slice(0, 300) : "")); }
process.exit(fail ? 1 : 0);