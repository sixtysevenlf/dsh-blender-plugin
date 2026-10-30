#!/usr/bin/env node
/**
 * S0 · 能力锁（capability lock）—— 重构的安全网。
 *
 * 把"当前对外能力"落成快照，任何阶段重构后对拍：**少一个 family / 少一个 op / 少一个工具就红**。
 * 只看**集合**（family 名+op 数、工具名），不看实现 —— 重构可以随便改代码，动了能力面就必须显式更新锁。
 *
 * 用法：node tests/capability_lock.mjs            # 校验
 *       node tests/capability_lock.mjs --update   # 重新生成（提交说明里要写为什么变）
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const LOCK = path.join(HERE, 'capability.lock.json');
const engine = await import(pathToFileURL(path.join(HERE, "..", "runtime", "engine.mjs")).href);

// v2.6.1（issue #42）：`catalogPayload({}).families` 给的是族名**字符串数组**，旧代码却按对象读
// （`f.f || f.family || f.prefix`）⇒ 锁内 33 行全塌成 {f:"undefined", ops:0}，族名 / 逐族 op 这一维
// 实际失效。逐族 op 数改取 `full:true` 的 `detail`（与 `ops_count` 同源）。
const cat = engine.catalogPayload({ full: true });
const detail = Array.isArray(cat.detail) ? cat.detail : [];
const families = detail
  .map((e) => ({ f: String(e.family), ops: Array.isArray(e.ops) ? e.ops.length : 0 }))
  .sort((a, b) => a.f.localeCompare(b.f));

// 取数面自检：结构不对就明说，不静默写出退化锁（#42 的教训 —— 假绿比红更坏）
const famOpsSum = families.reduce((sum, x) => sum + x.ops, 0);
const badShape = [];
if (!detail.length) badShape.push("catalog.detail 为空（engine.catalogPayload({full:true}) 没给逐族明细）");
if (families.some((x) => !x.f || x.f === "undefined")) badShape.push("存在空 / undefined 族名");
if (new Set(families.map((x) => x.f)).size !== families.length) badShape.push("族名重复");
if (Number(cat.ops_count) !== famOpsSum) badShape.push("逐族 op 数之和 " + famOpsSum + " ≠ ops_count " + Number(cat.ops_count));
if (badShape.length) {
  console.log("能力锁：取数面异常（不写锁，也不判绿）");
  for (const b of badShape) console.log("  x " + b);
  process.exit(1);
}

// 工具名取模型侧真源：T4 之后工具注册分散在 src/tools/*.ts（14 个工具）与 src/plan/tool.ts（plan），
// 不再从 src/index.ts 取（入口层只做 re-export，不含工具声明）。
// tarball guard (issue #40): files 面不含 src/ ⇒ tarball 用户读不到真源，工具维度不可判（family/op 维度仍照判）。
// 口径同 tests/host_routes_selftest.mjs「后端不可达 → SKIP 且 exit 0」：降级要说出来，不得静默当作已验。
const TOOLS_DIR = path.join(HERE, "..", "src", "tools");
const PLAN_TOOL_PATH = path.join(HERE, "..", "src", "plan", "tool.ts");
const SRC = (() => {
  if (!fs.existsSync(TOOLS_DIR)) return null;
  const files = fs.readdirSync(TOOLS_DIR)
    .filter((f) => f.endsWith(".ts"))
    .map((f) => path.join(TOOLS_DIR, f));
  files.push(PLAN_TOOL_PATH);
  return files.map((f) => (fs.existsSync(f) ? fs.readFileSync(f, "utf8") : "")).join("\n");
})();
// v1.0.1+S11（issue #10）：同一个 tool 名会在"降级占位"分支里被**再声明一次**（占位工具复用
// 既有名，不是新能力）⇒ 按**集合**去重再计数。本测试的口径本来就是"只看集合（工具名）"，
// 之前用数组长度是把重复声明也算成了新工具。
const tools = SRC === null ? [] : [...new Set([...SRC.matchAll(/name: '(blender_[a-z_]+)'/g)].map((m) => m[1]))].sort();

const snap = {
  families_count: families.length,
  ops_count: cat.ops_count,
  tools_count: tools.length,
  tools,
  families,
};

const update = process.argv.includes("--update") || process.env.DSH_UPDATE_LOCK === "1";
if (update || !fs.existsSync(LOCK)) {
  fs.writeFileSync(LOCK, JSON.stringify(snap, null, 2) + "\n", "utf8");
  console.log("能力锁已写入: " + path.basename(LOCK) + " (" + snap.families_count + " family / " + snap.ops_count + " op / " + snap.tools_count + " 工具)");
  process.exit(0);
}

const old = JSON.parse(fs.readFileSync(LOCK, "utf8"));
const fails = [];
if (SRC === null) console.log("  · SKIP 工具维度：src/tools/ 不在（发布 tarball 的 files 面不含 src/）。family/op 维度照常判。自检工具面请从 git 检出跑：git clone --branch <tag> <repo>");
if (old.families_count !== snap.families_count) fails.push("family 数变了: " + old.families_count + " -> " + snap.families_count);
if (old.ops_count !== snap.ops_count) fails.push("op 数变了: " + old.ops_count + " -> " + snap.ops_count);
if (SRC !== null && old.tools_count !== snap.tools_count) fails.push("工具数变了: " + old.tools_count + " -> " + snap.tools_count);

const oldMap = new Map((old.families || []).map((x) => [x.f, x.ops]));
const newMap = new Map(families.map((x) => [x.f, x.ops]));
for (const [k, v] of oldMap) {
  if (!newMap.has(k)) fails.push("丢了 family: " + k + " (" + v + " op)");
  else if (newMap.get(k) !== v) fails.push("family " + k + " 的 op 数变了: " + v + " -> " + newMap.get(k));
}
for (const [k, v] of newMap) {
  if (!oldMap.has(k)) fails.push("新增 family: " + k + " (" + v + " op) —— 若有意为之请 --update 并说明");
}

const oldTools = new Set(old.tools || []);
const newTools = new Set(tools);
const TOOLS_SKIPPED = SRC === null;
if (!TOOLS_SKIPPED) {
  for (const t of oldTools) if (!newTools.has(t)) fails.push("丢了工具: " + t);
  for (const t of newTools) if (!oldTools.has(t)) fails.push("新增工具: " + t + " —— 若有意为之请 --update 并说明");
}

if (fails.length) {
  console.log("能力锁：FAIL（" + fails.length + " 项）");
  for (const f of fails.slice(0, 12)) console.log("  x " + f);
  process.exit(1);
}
console.log("能力锁：PASS（" + snap.families_count + " family / " + snap.ops_count + " op / " + (TOOLS_SKIPPED ? "工具维度 SKIP" : snap.tools_count + " 工具") + "，与 " + path.basename(LOCK) + " 一致）");