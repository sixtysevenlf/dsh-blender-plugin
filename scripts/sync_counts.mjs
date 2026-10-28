#!/usr/bin/env node
/**
 * S4 · 计数同步器：**catalog 是唯一真源**，本脚本把数字刷进各处展示文案。
 *
 * 用法：node scripts/sync_counts.mjs          # 写入（把过时数字刷新）
 *       node scripts/sync_counts.mjs --dry    # 只报告差异，不写
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.join(HERE, "..");
const cat = (await import(pathToFileURL(path.join(ROOT, "runtime", "engine.mjs")).href)).catalogPayload({});
const fams = cat.families.length;
const ops = cat.ops_count;
const dry = process.argv.includes("--dry");

const targets = [
  { file: path.join(ROOT, "src", "index.ts"),
    pairs: [[/(【判定 \/ 验收 \/ 导出 \/ 造型】)\d+ 个 family · \d+ 个 op/,
             "$1" + fams + " 个 family · " + ops + " 个 op"]] },
  // AGENTS.md 两份都要刷：工作区那份（single_source_selftest 校验）和**仓内那份**
  // （随插件发布、被助手自动加载的项目说明）。
  // 三种写法都出现过：2.6.0「33 个 family / 217 个 op」、2.6.1「33 family / 217 op」、
  // 还有速查里的「catalog（33/217）」与故障表的「catalog 不是 33-217」。
  // 同一句在一份文件里出现多次，必须 /g，否则只刷第一处（实测就漏过）。
  ...[path.join(ROOT, "..", "AGENTS.md"), path.join(ROOT, "AGENTS.md")].map((file) => ({
    file,
    pairs: [
      [/\d+ (?:个 )?family \/ \d+ (?:个 )?op/g, fams + " 个 family / " + ops + " 个 op"],
      [/catalog（\d+\/\d+）/g, "catalog（" + fams + "/" + ops + "）"],
      [/catalog 不是 \d+-\d+/g, "catalog 不是 " + fams + "-" + ops],
    ],
  })),
];

// 文档也要刷：操作教程与随包 skill（两者都会被用户/AI 当事实源读）。
for (const rel of ["docs/操作教程.md", "skills/blender-modeling/SKILL.md"]) {
  targets.push({
    file: path.join(ROOT, rel),
    pairs: [[/\d+ (?:个 )?family \/ \d+ (?:个 )?op/g, fams + " 个 family / " + ops + " 个 op"]],
  });
}

let changed = 0;
for (const t of targets) {
  if (!fs.existsSync(t.file)) { console.log("跳过（不存在）: " + t.file); continue; }
  const s = fs.readFileSync(t.file, "utf8");
  let s2 = s;
  for (const [re, rep] of t.pairs) s2 = s2.replace(re, rep);
  if (s2 === s) { console.log("已一致: " + path.basename(t.file)); continue; }
  if (dry) { console.log("需更新(dry): " + t.file); }
  else { fs.writeFileSync(t.file, s2, "utf8"); console.log("已更新: " + t.file); }
  changed++;
}
console.log("catalog 现算：" + fams + " family / " + ops + " op；" + (dry ? "需改 " : "已改 ") + changed + " 个文件");