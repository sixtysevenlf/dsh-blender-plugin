#!/usr/bin/env node
/**
 * S4 · 计数同步器：**catalog 是唯一真源**，本脚本把数字刷进各处展示文案。
 *
 * 用法：node scripts/sync_counts.mjs          # 写入（把过时数字刷新）
 *       node scripts/sync_counts.mjs --dry    # 只报告差异，不写
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.join(HERE, "..");
const cat = (await import(path.join(ROOT, "runtime", "engine.mjs"))).catalogPayload({});
const fams = cat.families.length;
const ops = cat.ops_count;
const dry = process.argv.includes("--dry");

const targets = [
  { file: path.join(ROOT, "src", "index.ts"),
    re: /(【判定 \/ 验收 \/ 导出 \/ 造型】)\d+ 个 family · \d+ 个 op/,
    rep: "$1" + fams + " 个 family · " + ops + " 个 op" },
  { file: path.join(ROOT, "..", "AGENTS.md"),
    re: /\d+ 个 family \/ \d+ 个 op/,
    rep: fams + " 个 family / " + ops + " 个 op" },
];

let changed = 0;
for (const t of targets) {
  if (!fs.existsSync(t.file)) { console.log("跳过（不存在）: " + t.file); continue; }
  const s = fs.readFileSync(t.file, "utf8");
  const s2 = s.replace(t.re, t.rep);
  if (s2 === s) { console.log("已一致: " + path.basename(t.file)); continue; }
  if (dry) { console.log("需更新(dry): " + path.basename(t.file)); }
  else { fs.writeFileSync(t.file, s2, "utf8"); console.log("已更新: " + path.basename(t.file)); }
  changed++;
}
console.log("catalog 现算：" + fams + " family / " + ops + " op；" + (dry ? "需改 " : "已改 ") + changed + " 个文件");