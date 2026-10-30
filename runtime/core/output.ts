// 输出/目录：catalog 页、stdout 标记解析、路径改写与结果落盘（T6 自 runtime/engine.ts 拆出）
// @imports
import { engineProvenance } from "./evidence.js";
import type { Provenance } from "./evidence.js";
import { PLAN_CATALOG, PLAN_TOOL_MAP, TOOL_DETAIL, planOpNames, renderFamilyText } from "../catalog.js";
import { MODELING_CLASS_GUIDE, renderClassText, renderHandoff } from "../classes.js";
import fs from "node:fs";
import { winToWsl, wslToWin } from "../config.js";
import path from "node:path";
import { errorMessage } from "./json.js";


export interface DumpResult { path: string | null; bytes: number; error?: string }

export function catalogPayload(opts?: unknown): Record<string, unknown> {
  const o: Record<string, unknown> = (opts && typeof opts === 'object') ? opts as Record<string, unknown> : {};
  // v0.9.6（D3）：加载版本自证 —— 目录里也要带"本进程加载的是哪一代"，避免磁盘 mtime 冒充加载版本
  const prov: Provenance = engineProvenance({ verify: o.verify === true || o.verify === 'true', verifyTimeoutMs: o.verifyTimeoutMs });
  // v0.9.6（D2）：工具参数细节（重工具的长尾）也走这里 —— 一个入口覆盖"哪个 op / 哪个 family / 哪个工具"
  if (o.tool) {
    const t = String(o.tool).replace(/^blender_/, '');
    const hit = TOOL_DETAIL[t];
    if (!hit) return { ok: false, error: '没有这个工具的参数细节：' + o.tool, tools: Object.keys(TOOL_DETAIL), provenance: prov };
    return { ok: true, op: 'catalog', tool: t, text: hit, provenance: prov };
  }
  if (o.family) {
    const e = PLAN_CATALOG.find((x) => x.f === String(o.family));
    if (!e) return { ok: false, error: 'unknown family: ' + o.family, families: PLAN_CATALOG.map((x) => x.f), provenance: prov };
    return { ok: true, op: 'catalog', family: e.f, text: renderFamilyText(e), provenance: prov };
  }
  if (o.handoff) {   // 子代理交接块：主模型一条命令拿到，粘进子代理提示词
    return { ok: true, op: 'catalog', handoff: true, text: renderHandoff(), provenance: prov };
  }
  if (o.classes) {
    const rows = MODELING_CLASS_GUIDE.map((r) => renderClassText(r)).join(String.fromCharCode(10) + String.fromCharCode(10));
    return { ok: true, op: 'catalog', classes: MODELING_CLASS_GUIDE.length, text: rows, provenance: prov };
  }
  if (o.class) {
    const q = String(o.class).toLowerCase();
    const hit = MODELING_CLASS_GUIDE.find((r) => r.c === q) || MODELING_CLASS_GUIDE.find((r) => (r.c + ' ' + r.name).toLowerCase().includes(q));
    if (!hit) return { ok: false, error: 'unknown modeling class: ' + o.class, classes: MODELING_CLASS_GUIDE.map((r) => r.c), provenance: prov };
    return { ok: true, op: 'catalog', class: hit.c, text: renderClassText(hit), provenance: prov };
  }
  const L = [];
  L.push('plan 通道目录：' + PLAN_CATALOG.length + ' 个 family / ' + planOpNames().length + ' 个 op。'
    + '先按"我要干什么"选 family，再照"骨架"填 args；参数名写错会报"不认识的参数"。');
  L.push('按建模类型查：args={classes:true}（' + MODELING_CLASS_GUIDE.length + ' 类）· args={class:"x"} 单类 · 派子代理 args={handoff:true}。'
    + '不覆盖（别在这找工具）：生物/有机造型、电影级布光/体积/合成、角色动画绑定。');
  L.push('');
  for (const e of PLAN_CATALOG) {
    L.push('· ' + e.f + (e.prefix ? '（' + e.prefix + '*）' : '') + '：' + e.when);
    L.push('    ops: ' + e.ops.join(' '));
    // 默认页是「一页索引」：骨架封顶 90 字符（v2.6 P0：family 从 28→30，保持默认目录 ≤9600 字符）；
    // 完整骨架在 args={family:…} 与 handoff 块里。
    if (e.sk) L.push('    骨架: ' + (e.sk.length > 90 ? e.sk.slice(0, 90) + '…（完整见 args={family:…}）' : e.sk));
    // S6 起 not 里带了『；**别…**』硬警告（可操作那句）—— 默认页只显示它，
    // 完整 not 在 args={family:…} 与 handoff 块里；这样一页索引仍能保持短。
    const _not = String(e.not || '');
    const _wi = _not.indexOf('；**别');
    L.push('    别用: ' + (_wi >= 0 ? _not.slice(_wi + 1) : _not));
  }
  L.push('');
  L.push('顶层工具（先选工具，再选 op）：');
  for (const t of PLAN_TOOL_MAP) L.push('  ' + t.t + ' —— ' + t.use);
  L.push('');
  L.push('展开方式：op="catalog", args={family:"sculpt"} 看单族；op="sculpt_help" 等看算子细节；'
    + '机器/复盘要结构化明细用 args={full:true}。');
  L.push('工具参数细节（重工具的长尾说明）：args={tool:"rt_headless"}；可查 ' + Object.keys(TOOL_DETAIL).join(' / ') + '。');
  // v0.9.6（D3）：加载版本自证 —— 一行给出"进程里这份 vs 磁盘上那份"的内容指纹（不是 mtime）
  if (prov.stale === true) {
    L.push('⚠ 目录自证 FAIL：磁盘上的 engine.mjs 与**本进程加载的**不一致（loaded ' + String(prov.loadedHash)
      + ' ≠ current ' + String(prov.currentHash) + '）→ 下面这份目录是**旧版**：'
      + '重启后端再问一次；对拍用 op="catalog", args={verify:true}（起干净进程读磁盘当裁判）。');
  } else if (prov.diskVerdict && prov.diskVerdict.ok === true && prov.diskVerdict.matchesLoadedCatalog === false) {
    L.push('⚠ 目录自证 FAIL：干净进程读磁盘是 ' + String(prov.diskVerdict.families) + ' 个 family / '
      + String(prov.diskVerdict.opsCount) + ' 个 op，本进程回答的是 ' + String(prov.families) + ' / '
      + String(prov.opsCount) + ' → **本进程目录是旧版**（重启后端）。');
  } else {
    L.push('目录自证：loaded engine.mjs ' + String(prov.loadedHash) + ' · 磁盘 ' + String(prov.currentHash)
      + ' · ' + (prov.verdict === 'current' ? '一致' : String(prov.verdict)));
  }
  const out: Record<string, unknown> = { ok: true, op: 'catalog', text: L.join(String.fromCharCode(10)),
                families: PLAN_CATALOG.map((e) => e.f), ops_count: planOpNames().length,
                provenance: prov };
  if (o.full) {
    out.detail = PLAN_CATALOG.map((e) => ({ family: e.f, prefix: e.prefix, ops: e.ops, when: e.when, not: e.not,
                                            skeleton: e.sk, key: e.key }));
    out.tools = PLAN_TOOL_MAP;
  }
  return out;
}

function extractTag(out: unknown, tag: string): unknown {
  const rec = (out && typeof out === 'object') ? out as Record<string, unknown> : null;
  const txt = rec && typeof rec.result === 'string' ? rec.result : '';
  const m = txt.match(new RegExp(tag + ' ([\\s\\S]*)'));
  if (!m) throw new Error(tag + ' 无回包: ' + txt.slice(0, 200));
  return JSON.parse(m[1]);
}

function extractLoop(out: unknown): unknown {
  return extractTag(out, 'LOOP');
}

function jsonFromStdout(out: unknown): unknown {
  const rec = (out && typeof out === 'object') ? out as Record<string, unknown> : null;
  const txt = rec && typeof rec.result === 'string' ? rec.result : (typeof out === 'string' ? out : JSON.stringify(out));
  const m = txt.match(/\{[\s\S]*\}/);
  return m ? JSON.parse(m[0]) : null;
}

/** 路径映射见 config.mjs：winToWsl / wslToWin（Windows 上原样返回） */

/** 无头运行用的 python 脚本落到 Windows 可见目录；/mnt 不可写时退回包内 tmp + UNC */
/** v0.8.10（A3）：长输出"头+尾+省略标记"—— 旧版只回尾部，长 JSON 的头被吃掉 */
function clipMiddle(s: unknown, head: number, tail: number): string {
  const t = String(s == null ? '' : s);
  if (t.length <= head + tail) return t;
  return t.slice(0, head) + '\n…[省略 ' + String(t.length - head - tail) + ' 字符；全文见 logs 路径]…\n' + t.slice(-tail);
}

/**
 * v0.9.6（现场反馈：WSL 绝对路径的"二次前缀"）：`home/.../DSH/测试/x.py` 这种**漏了开头 /** 的路径
 * 会被当相对路径接到 cwd 后面，得到 `...DSH\\home\\sixtyseven67\\DSH\\测试\\x.py`。
 * 判据：补一个 "/" 之后确实存在 → 纠正并记一笔（同时写后端 stderr，便于排错）。
 */
function slipFixPath(p: unknown, notes?: string[] | null): string {
  const s = String(p == null ? '' : p);
  if (!s || s.startsWith('/') || s.startsWith('\\\\') || /^[A-Za-z]:[\\/]/.test(s)) return s;
  if (!/^(home|mnt|tmp|usr|opt|srv|var|etc|root|data)\//.test(s)) return s;
  const cand = '/' + s;
  let hit = false;
  try { hit = fs.existsSync(winToWsl(cand)) || fs.existsSync(cand); } catch (e) { hit = false; }
  if (!hit) return s;
  const msg = 'WSL 绝对路径漏了开头的 "/"：' + s + ' → 已按 ' + cand + ' 处理';
  if (notes) notes.push(msg);
  try { process.stderr.write('[dsh-blender] ' + msg + String.fromCharCode(10)); } catch (e) {}
  return cand;
}
export { extractTag, extractLoop, jsonFromStdout, clipMiddle, slipFixPath };

export function createDumpResult(E: { RESULTS_DIR: string }): { dumpResult: (obj: unknown, runId2?: string) => DumpResult } {
  const { RESULTS_DIR } = E;
  function dumpResult(obj: unknown, runId2?: string): DumpResult {
    try {
      const s = JSON.stringify(obj);
      fs.mkdirSync(RESULTS_DIR, { recursive: true });
      const f = path.join(RESULTS_DIR, String(runId2 || ('r-' + Date.now().toString(36))) + '.json');
      fs.writeFileSync(f, s, 'utf8');
      return { path: wslToWin(f), bytes: s.length };
    } catch (e) {
      return { path: null, bytes: 0, error: errorMessage(e).slice(0, 200) };
    }
  }
  return { dumpResult };
}
