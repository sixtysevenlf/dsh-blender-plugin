#!/usr/bin/env node
/**
 * S4 · 单一事实源自检
 *
 * 钉两件事：
 *  ① /plan 只读白名单从 engine 派生后，与"迁移前的冻结基线"逐名一致（防止搬家中丢项/多项）；
 *  ② **计数只有一处手写**：src/index.ts 描述与工作区 AGENTS.md 里的 family/op 数必须等于 catalog 现算值。
 *
 * 用法：node tests/single_source_selftest.mjs
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.join(HERE, "..");
const engine = await import(path.join(ROOT, "runtime", "engine.mjs"));
const cat = engine.catalogPayload({});
const fams = cat.families.length;
const ops = cat.ops_count;

let pass = 0;
let fail = 0;
function ok(name, cond, extra) {
  if (cond) { pass++; console.log("  ✓ " + name); }
  else { fail++; console.log("  ✗ " + name + (extra === undefined ? "" : " — " + JSON.stringify(extra))); }
}

// ① 白名单：派生集合 == 冻结基线
const basePath = path.join(HERE, 'plan_readonly.baseline.json');
const baseline = JSON.parse(fs.readFileSync(basePath, 'utf8')).slice().sort();
const derived = [...engine.PLAN_READ_ONLY_OPS].slice().sort();
const missing = baseline.filter((n) => !derived.includes(n));
const extra = derived.filter((n) => !baseline.includes(n));
ok("只读白名单派生集合与冻结基线逐名一致（" + baseline.length + " 条）",
  missing.length === 0 && extra.length === 0, { missing: missing.slice(0, 6), extra: extra.slice(0, 6) });
ok("白名单里没有重复项", derived.length === new Set(derived).size, derived.length);
ok("server.mjs 确实消费派生常量（不再手抄）",
  fs.readFileSync(path.join(ROOT, 'runtime', 'server.mjs'), 'utf8').includes("'/plan': PLAN_READ_ONLY_OPS"));

// ② 计数：描述与 AGENTS.md 必须等于 catalog 现算值
const SRC = fs.readFileSync(path.join(ROOT, 'src', 'index.ts'), 'utf8');
const mDesc = SRC.match(/【判定 \/ 验收 \/ 导出 \/ 造型】(\d+) 个 family · (\d+) 个 op/);
ok("src/index.ts 描述里的计数 == catalog（" + fams + " family / " + ops + " op）",
  !!mDesc && Number(mDesc[1]) === fams && Number(mDesc[2]) === ops, mDesc ? mDesc.slice(1) : null);

const ag = path.join(ROOT, "..", "AGENTS.md");
if (fs.existsSync(ag)) {
  const t = fs.readFileSync(ag, 'utf8');
  const m2 = t.match(/(\d+) 个 family \/ (\d+) 个 op/);
  ok("工作区 AGENTS.md 计数 == catalog", !!m2 && Number(m2[1]) === fams && Number(m2[2]) === ops, m2 ? m2.slice(1) : null);
} else {
  console.log("  · 未找到工作区 AGENTS.md，跳过");
}

console.log("single-source-selftest：" + pass + " 通过 / " + fail + " 失败");
if (fail) {
  console.log("修法：node scripts/sync_counts.mjs   # 从 catalog 刷新计数（白名单请改 runtime/engine.mjs 一处）");
}
process.exit(fail ? 1 : 0);