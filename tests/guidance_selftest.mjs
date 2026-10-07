#!/usr/bin/env node
/**
 * 指引完整性自检（S6）—— 让"给模型指明工具"这件事不会腐烂。
 *
 * 三层：
 *   ① 类型表完整性：>=12 类，每类的 c/name/entry/chain/forbid/gate 都不能空，forbid 必须是硬话（别/禁止/不要）；
 *   ② 入口有效性：entry/chain 里出现的 op="x_y" 必须真在 planOpNames() 里（防 op 改名后指引变成错指路）；
 *   ③ 硬警告钉住：易自造的那批 family，其 catalog `not` 必须含硬警告（防有人把警告删掉）。
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.join(HERE, "..");
const engine = await import(pathToFileURL(path.join(ROOT, "runtime", "engine.mjs")).href);
const { MODELING_CLASS_GUIDE, classGuideStats } = await import(pathToFileURL(path.join(ROOT, "runtime", "classes.mjs")).href);
const lock = JSON.parse(fs.readFileSync(path.join(ROOT, "tests", "capability.lock.json"), "utf8"));

let pass = 0; let fail = 0; const fails = [];
const ok = (name, cond, extra) => { if (cond) pass++; else { fail++; fails.push({ clause: name, detail: extra === undefined ? null : extra }); } };

// ① 类型表完整性
const stats = classGuideStats();
ok("类型表 >= 12 类", stats.classes >= 12, stats.classes);
ok("每类六个字段都不空（c/name/entry/chain/forbid/gate）", stats.missing.length === 0, stats.missing);
const softForbid = MODELING_CLASS_GUIDE.filter((r) => !/别|禁止|不要/.test(String(r.forbid))).map((r) => r.c);
ok("每类的 forbid 都是硬话（别/禁止/不要）", softForbid.length === 0, softForbid);

// ② 入口有效性：指引里点到的 op 必须真的存在
const opNames = new Set(engine.planOpNames());
const toolNames = new Set(lock.tools || []);
const badOps = []; const badTools = [];
for (const r of MODELING_CLASS_GUIDE) {
  const text = [r.entry, r.chain, r.gate, r.forbid].join(" ");
  for (const m of text.matchAll(/op="([a-z0-9_]+)"/g)) if (!opNames.has(m[1])) badOps.push(r.c + " -> " + m[1]);
  for (const m of text.matchAll(/blender_rt_[a-z_]+/g)) if (!toolNames.has(m[0])) badTools.push(r.c + " -> " + m[0]);
  // chain 里的裸算子名（如 img_rectify / crease_lines / material_build）也要能对上，或者至少在 ops 全表里
}
ok("指引里的 plan op 名都真实存在", badOps.length === 0, badOps);
ok("指引里的工具名都真实存在", badTools.length === 0, badTools);

// ③ 硬警告钉住：这批 family 的 not 必须含硬话
const PINNED = ["vehicle", "shape", "sweep", "generator", "uv", "material", "deliver", "gate", "motion", "human", "img", "audit", "qc"];
const cat = engine.PLAN_CATALOG;
const missingWarn = [];
for (const f of PINNED) {
  const e = cat.find((x) => x.f === f);
  if (!e) { missingWarn.push(f + "(family 不存在)"); continue; }
  if (!/别|禁止|不要|必须/.test(String(e.not))) missingWarn.push(f);
}
ok("易自造族的 catalog not 都带硬警告（" + PINNED.length + " 族）", missingWarn.length === 0, missingWarn);
const noWarn = cat.filter((e) => !/别|禁止|不要|必须/.test(String(e.not))).map((e) => e.f);


// ④ 子代理交接块（S6-b）：子代理看不到主模型读过的 catalog ⇒ 必须能把指路"交下去"
const handoff = engine.catalogPayload({ handoff: true });
const ht = String(handoff.text || "");
ok("handoff 块存在且非空", handoff.ok === true && ht.length > 500, ht.length);
ok("handoff 块短到能塞进子代理提示词（<= 3500 字符）", ht.length <= 3500, ht.length);
ok("handoff 覆盖全部 12 类", MODELING_CLASS_GUIDE.every((r) => ht.includes(r.c)),
  MODELING_CLASS_GUIDE.map((r) => r.c).filter((c) => !ht.includes(c)));
ok("handoff 含禁止自造与三条硬规则", ht.includes("禁止自造") && ht.includes("三条硬规则"));
const mft = (await import(pathToFileURL(path.join(ROOT, "runtime", "classes.mjs")).href)).MODEL_FACING_TOOLS;
const lockTools = lock.tools || [];
ok("工具清单与能力锁逐名一致（防漂移）",
  mft.length === lockTools.length && mft.every((t) => lockTools.includes(t)), { mine: mft.length, lock: lockTools.length });
console.log("指引自检：" + pass + " 通过 / " + fail + " 失败");
console.log("  类型表 " + stats.classes + " 类 · 钉住硬警告 " + PINNED.length + " 族 · 其余未带硬警告 " + noWarn.length + " 族（" + noWarn.join(",") + "）");
if (fail) for (const f of fails.slice(0, 8)) console.log("  x " + f.clause + (f.detail ? " — " + JSON.stringify(f.detail).slice(0, 300) : ""));
process.exit(fail ? 1 : 0);