// catalog 页渲染
import type { Provenance } from '../evidence.js'
import { engineProvenance } from '../evidence.js'
import {
  PLAN_CATALOG,
  PLAN_TOOL_MAP,
  TOOL_DETAIL,
  planOpNames,
  renderFamilyText,
} from '../../catalog/index.js'
import { MODELING_CLASS_GUIDE, renderClassText, renderHandoff } from '../../classes.js'

/** 默认页的索引行（family / 工具 / 展开方式） */
function catalogIndexLines(): string[] {
  const L = []
  L.push(
    'plan 通道目录：' +
      PLAN_CATALOG.length +
      ' 个 family / ' +
      planOpNames().length +
      ' 个 op。' +
      '先按"我要干什么"选 family，再照"骨架"填 args；参数名写错会报"不认识的参数"。',
  )
  L.push(
    '按建模类型查：args={classes:true}（' +
      MODELING_CLASS_GUIDE.length +
      ' 类）· args={class:"x"} 单类 · 派子代理 args={handoff:true}。' +
      '不覆盖（别在这找工具）：生物/有机造型、电影级布光/体积/合成、角色动画绑定。',
  )
  L.push('')
  for (const e of PLAN_CATALOG) {
    L.push('· ' + e.f + (e.prefix ? '（' + e.prefix + '*）' : '') + '：' + e.when)
    L.push('    ops: ' + e.ops.join(' '))
    // 默认页是「一页索引」：骨架封顶 90 字符（v2.6：family 从 28→30，保持默认目录 ≤9600 字符）；
    // 完整骨架在 args={family:…} 与 handoff 块里。
    if (e.sk)
      L.push(
        '    骨架: ' +
          (e.sk.length > 90 ? e.sk.slice(0, 90) + '…（完整见 args={family:…}）' : e.sk),
      )
    // not 文本里带了『；**别…**』硬警告（可操作那句）—— 默认页只显示它，
    // 完整 not 在 args={family:…} 与 handoff 块里；这样一页索引仍能保持短。
    const _not = String(e.not || '')
    const _wi = _not.indexOf('；**别')
    L.push('    别用: ' + (_wi >= 0 ? _not.slice(_wi + 1) : _not))
  }
  L.push('')
  L.push('顶层工具（先选工具，再选 op）：')
  for (const t of PLAN_TOOL_MAP) L.push('  ' + t.t + ' —— ' + t.use)
  L.push('')
  L.push(
    '展开方式：op="catalog", args={family:"sculpt"} 看单族；op="sculpt_help" 等看算子细节；' +
      '机器/复盘要结构化明细用 args={full:true}。',
  )
  L.push(
    '工具参数细节（重工具的长尾说明）：args={tool:"blender_run"}；可查 ' +
      Object.keys(TOOL_DETAIL).join(' / ') +
      '。',
  )
  return L
}

/** 目录自证行（loaded/current/diskVerdict） */
function catalogProvenanceLines(prov: Provenance): string[] {
  const L: string[] = []
  // v0.9.6：加载版本自证 —— 一行给出"进程里这份 vs 磁盘上那份"的内容指纹（不是 mtime）
  if (prov.stale === true) {
    L.push(
      '⚠ 目录自证 FAIL：磁盘上的 engine.mjs 与**本进程加载的**不一致（loaded ' +
        String(prov.loadedHash) +
        ' ≠ current ' +
        String(prov.currentHash) +
        '）→ 下面这份目录是**旧版**：' +
        '重启后端再问一次；对拍用 op="catalog", args={verify:true}（起干净进程读磁盘当裁判）。',
    )
  } else if (
    prov.diskVerdict &&
    prov.diskVerdict.ok === true &&
    prov.diskVerdict.matchesLoadedCatalog === false
  ) {
    L.push(
      '⚠ 目录自证 FAIL：干净进程读磁盘是 ' +
        String(prov.diskVerdict.families) +
        ' 个 family / ' +
        String(prov.diskVerdict.opsCount) +
        ' 个 op，本进程回答的是 ' +
        String(prov.families) +
        ' / ' +
        String(prov.opsCount) +
        ' → **本进程目录是旧版**（重启后端）。',
    )
  } else {
    L.push(
      '目录自证：loaded engine.mjs ' +
        String(prov.loadedHash) +
        ' · 磁盘 ' +
        String(prov.currentHash) +
        ' · ' +
        (prov.verdict === 'current' ? '一致' : String(prov.verdict)),
    )
  }
  return L
}

function renderCatalogIndex(o: Record<string, unknown>, prov: Provenance): Record<string, unknown> {
  const L = catalogIndexLines().concat(catalogProvenanceLines(prov))
  const out: Record<string, unknown> = {
    ok: true,
    op: 'catalog',
    text: L.join(String.fromCharCode(10)),
    families: PLAN_CATALOG.map((e) => e.f),
    ops_count: planOpNames().length,
    provenance: prov,
  }
  if (o.full) {
    out.detail = PLAN_CATALOG.map((e) => ({
      family: e.f,
      prefix: e.prefix,
      requires_session: e.requires_session,
      write_scope: e.write_scope,
      ops: e.ops,
      when: e.when,
      not: e.not,
      skeleton: e.sk,
      key: e.key,
    }))
    out.tools = PLAN_TOOL_MAP
  }
  return out
}

export function catalogPayload(opts?: unknown): Record<string, unknown> {
  const o: Record<string, unknown> =
    opts && typeof opts === 'object' ? (opts as Record<string, unknown>) : {}
  // v0.9.6：加载版本自证 —— 目录里也要带"本进程加载的是哪一代"，避免磁盘 mtime 冒充加载版本
  const prov: Provenance = engineProvenance({
    verify: o.verify === true || o.verify === 'true',
    verifyTimeoutMs: o.verifyTimeoutMs,
  })
  // v0.9.6：工具参数细节（重工具的长尾）也走这里 —— 一个入口覆盖"哪个 op / 哪个 family / 哪个工具"
  if (o.tool) {
    const t = String(o.tool).replace(/^blender_/, '')
    const hit = TOOL_DETAIL[t]
    if (!hit)
      return {
        ok: false,
        error: '没有这个工具的参数细节：' + o.tool,
        tools: Object.keys(TOOL_DETAIL),
        provenance: prov,
      }
    return { ok: true, op: 'catalog', tool: t, text: hit, provenance: prov }
  }
  if (o.family) {
    const e = PLAN_CATALOG.find((x) => x.f === String(o.family))
    if (!e)
      return {
        ok: false,
        error: 'unknown family: ' + o.family,
        families: PLAN_CATALOG.map((x) => x.f),
        provenance: prov,
      }
    return { ok: true, op: 'catalog', family: e.f, text: renderFamilyText(e), provenance: prov }
  }
  if (o.handoff) {
    // 子代理交接块：主模型一条命令拿到，粘进子代理提示词
    return { ok: true, op: 'catalog', handoff: true, text: renderHandoff(), provenance: prov }
  }
  if (o.classes) {
    const rows = MODELING_CLASS_GUIDE.map((r) => renderClassText(r)).join(
      String.fromCharCode(10) + String.fromCharCode(10),
    )
    return {
      ok: true,
      op: 'catalog',
      classes: MODELING_CLASS_GUIDE.length,
      text: rows,
      provenance: prov,
    }
  }
  if (o.class) {
    const q = String(o.class).toLowerCase()
    const hit =
      MODELING_CLASS_GUIDE.find((r) => r.c === q) ||
      MODELING_CLASS_GUIDE.find((r) => (r.c + ' ' + r.name).toLowerCase().includes(q))
    if (!hit)
      return {
        ok: false,
        error: 'unknown modeling class: ' + o.class,
        classes: MODELING_CLASS_GUIDE.map((r) => r.c),
        provenance: prov,
      }
    return { ok: true, op: 'catalog', class: hit.c, text: renderClassText(hit), provenance: prov }
  }
  return renderCatalogIndex(o, prov)
}
