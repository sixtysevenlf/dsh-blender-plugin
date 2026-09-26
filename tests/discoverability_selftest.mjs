#!/usr/bin/env node
/**
 * tests/discoverability_selftest.mjs —— v0.9.6（D1）：工具可发现性
 *
 * 动机（实测，见 CHANGELOG v0.9.6 / docs/上游整合评估-*.md）：
 *   10 天 8,116 次真实调用里 plan 通道只占 1.6%（剔掉自检后），sculpt/fix/uv/print/sweep 五族真实使用 0 次；
 *   111 条失败里 68% 是超时，真正的"选错通道/参数"只有 3 条。
 *   ⇒ 瓶颈是"不知道有"，不是"报错"。于是：描述压成索引 + op="catalog" 目录 + 错误即纠正。
 *
 * 本测试固定四件事（不需要 Blender）：
 *   A catalog 的体量与内容（默认短文本；单族更短；full 才给结构化明细）
 *   B 未知 op 的 did_you_mean 最近邻（纯函数）
 *   C rt_cmd 跨通道纠错：把 plan op 当 addon 命令发要被当场纠正（纯函数 + 真 HTTP 路由）
 *   D 描述预算：rt_plan 的 description 必须 < 2000 字符（防止再膨胀回 4.7k 字）
 *
 * 用法：node tests/discoverability_selftest.mjs
 */
import { spawn } from 'node:child_process'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { PLAN_CATALOG, catalogPayload, planOpNames, planOpSuggestion, planOpCrossChannelHint,
         catalogFingerprint } from '../runtime/engine.mjs'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const PKG = path.join(HERE, '..')
const PORT = Number(process.env.DSH_SELFTEST_DISCOVER_PORT || 9897)
const TMP = fs.mkdtempSync(path.join(os.tmpdir(), 'dsh-discover-selftest-'))

let pass = 0, fail = 0
const failures = []
function ok(name, cond, detail) {
  if (cond) { pass++; console.log('  ✓ ' + name) }
  else { fail++; failures.push(name); console.log('  ✗ ' + name + (detail ? (' — ' + String(detail).slice(0, 220)) : '')) }
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
async function getJson(p, route) { try { return await (await fetch('http://127.0.0.1:' + String(p) + route)).json() } catch (e) { return null } }
async function rpc(p, route, body, ms) {
  const ctl = new AbortController(); const t = setTimeout(() => ctl.abort(), ms || 60000)
  try {
    const r = await fetch('http://127.0.0.1:' + String(p) + route, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body || {}), signal: ctl.signal })
    return { status: r.status, body: await r.json() }
  } finally { clearTimeout(t) }
}

console.log('== 工具可发现性自检（D1）==')

// ── A：catalog 体量与内容
// ⚠ 不写死家族数（v0.9.6 前这里写 26，磁盘早已 27 → 测试假失败）：用"下限 + 关键家族 + 与描述一致"三重判据，
//   真正的门是 D 段的"描述里的数字必须等于运行时 catalog 的数字"。
const KEY_FAMILIES = ['sculpt', 'fix', 'uv', 'print', 'sweep', 'vehicle', 'gate', 'material', 'render_guard', 'generator']
const cat = catalogPayload({})
ok('catalog 覆盖 >= 26 个 family 且含关键家族',
  cat.families.length >= 26 && KEY_FAMILIES.every((f) => cat.families.indexOf(f) >= 0),
  cat.families.length + ' / ' + JSON.stringify(KEY_FAMILIES.filter((f) => cat.families.indexOf(f) < 0)))
ok('catalog 覆盖 >= 100 个 op', cat.ops_count >= 100, cat.ops_count)
// 体量门：**按家族摊**（每个 family 一个"什么时候用/ops/最小骨架/别用"块），避免"加一族就得改常数"
const perFamily = cat.text.length / cat.families.length
ok('catalog 默认是短文本（3k–9600 字符，且每个 family <= 360 字符）',
  cat.text.length >= 3000 && cat.text.length <= 9600 && perFamily <= 360,
  cat.text.length + ' / family=' + perFamily.toFixed(1))
ok('catalog 默认不带结构化明细（要 full:true 才给）', !cat.detail && !cat.tools)
ok('catalog 文本含"什么时候用"与"别用"判据', cat.text.indexOf('别用') > 0, true)
const catSculpt = catalogPayload({ family: 'sculpt' })
ok('单族展开更短（< 1500 字符）', catSculpt.text.length < 1500, catSculpt.text.length)
ok('单族展开含最小骨架', catSculpt.text.indexOf('sculpt_apply') > 0)
const catFull = catalogPayload({ full: true })
ok('full:true 才带结构化明细（条数与 families 一致）',
  Array.isArray(catFull.detail) && catFull.detail.length === cat.families.length, catFull.detail && catFull.detail.length)
ok('未知 family 要报错并列出候选', catalogPayload({ family: 'nope' }).ok === false)

// ── A2：v0.9.6 修的两个参数名（现场照 catalog 抄会直接失败）
const genText = catalogPayload({ family: 'generator' }).text
ok('generator 骨架用 code= 而不是 script=（旧目录把 script 当参数名，照抄必炸）',
  genText.indexOf('code:') > 0 && genText.indexOf('script:') < 0, genText.split('最小骨架:')[1])
ok('generator_run 骨架用嵌套 args=（PARAMS 走这一层，不会被摊平）',
  genText.indexOf('generator_run", args={') > 0, genText.split('最小骨架:')[1])

// ── A3：加载版本自证（内容指纹，不是 mtime）
const fp1 = catalogFingerprint()
const fp2 = catalogFingerprint()
ok('catalogHash 稳定（同一进程两次调用一致）', fp1.catalogHash === fp2.catalogHash && fp1.catalogHash.length === 12,
  fp1.catalogHash)
ok('catalog 回执带 provenance（loadedHash / currentHash / verdict）',
  !!(cat.provenance && cat.provenance.loadedHash && cat.provenance.currentHash && cat.provenance.verdict),
  JSON.stringify(cat.provenance && { l: cat.provenance.loadedHash, c: cat.provenance.currentHash, v: cat.provenance.verdict }))
ok('provenance 用的是内容哈希（12 位十六进制），并明确写着不含 mtime',
  /^[0-9a-f]{12}$/.test(String(cat.provenance.loadedHash)) && cat.provenance.note.indexOf('mtime') >= 0,
  String(cat.provenance.loadedHash) + ' / ' + String(cat.provenance.note).slice(0, 60))
ok('本进程刚 import → verdict=current（stale=false）',
  cat.provenance.verdict === 'current' && cat.provenance.stale === false, cat.provenance.verdict)
ok('catalog 文本里带"目录自证"一行（加载版本可自证）', cat.text.indexOf('目录自证') > 0)
const catVerify = catalogPayload({ verify: true })
ok('args={verify:true} 起干净进程读磁盘当裁判（diskVerdict.ok）',
  !!(catVerify.provenance.diskVerdict && catVerify.provenance.diskVerdict.ok === true),
  JSON.stringify(catVerify.provenance.diskVerdict).slice(0, 200))
ok('干净进程的 catalogHash 与本进程一致（= 目录不是旧版）',
  catVerify.provenance.diskVerdict.catalogHash === fp1.catalogHash
  && catVerify.provenance.diskVerdict.matchesLoadedCatalog === true,
  JSON.stringify(catVerify.provenance.diskVerdict).slice(0, 200))
ok('干净进程看到的 family/op 数与本进程一致',
  catVerify.provenance.diskVerdict.families === cat.families.length
  && catVerify.provenance.diskVerdict.opsCount === cat.ops_count,
  JSON.stringify(catVerify.provenance.diskVerdict).slice(0, 200))

ok('material 家族在目录里（build/apply/bake）',
  catalogPayload({ family: 'material' }).text.indexOf('material_bake') > 0)
ok('render_guard 家族在目录里（state/wait/reset）',
  catalogPayload({ family: 'render_guard' }).text.indexOf('render_wait') > 0)
ok('render_state 不需要 Blender 也能答（读磁盘标记）',
  catalogPayload({}).text.indexOf('material') > 0)

// ── B：未知 op 最近邻
const s1 = planOpSuggestion('audti_mesh')
ok('audti_mesh → audit_mesh（带骨架）', !!s1 && s1.op === 'audit_mesh' && !!s1.skeleton, JSON.stringify(s1 && s1.op))
const s2 = planOpSuggestion('sculpt_appli')
ok('sculpt_appli → sculpt_apply', !!s2 && s2.op === 'sculpt_apply', JSON.stringify(s2 && s2.op))
const s3 = planOpSuggestion('qc_render_view')
ok('qc_render_view → qc_render_views', !!s3 && s3.op === 'qc_render_views', JSON.stringify(s3 && s3.op))
ok('完全不相干的词不硬猜（返回 null）', planOpSuggestion('zzzzzzzzzz') === null)

// ── C：跨通道纠错
const h1 = planOpCrossChannelHint('qc_render_catalog')
ok('qc_render_catalog → 指回 blender_rt_plan（实测真实发生过的弯路）',
  !!h1 && h1.indexOf('blender_rt_plan(op="qc_render_catalog"') > 0, h1)
const h2 = planOpCrossChannelHint('audit_mesh')
ok('audit_mesh → 指回 blender_rt_plan(op="audit_mesh")', !!h2 && h2.indexOf('blender_rt_plan(op="audit_mesh"') > 0, h2)
const h3 = planOpCrossChannelHint('get_scene_info')
ok('真 addon 命令不误判（放行）', h3 === null, h3)
const h4 = planOpCrossChannelHint('sweep_analyz')
ok('拼错的 plan op → 给最近邻建议', !!h4 && h4.indexOf('sweep_analyze') > 0, h4)
ok('planOpNames 与 catalog 一致（无孤儿 op）', planOpNames().length === cat.ops_count)

// ── D：描述预算（别让 rt_plan 再膨胀）
const srcTs = fs.readFileSync(path.join(PKG, 'src', 'index.ts'), 'utf8')
const i0 = srcTs.indexOf("name: 'blender_rt_plan'")
const i1 = srcTs.indexOf('parameters:', i0)
const descBlock = srcTs.slice(i0, i1)
const descChars = (descBlock.match(/'((?:[^'\\]|\\.)*)'/g) || []).join('').length
ok('rt_plan 描述 < 2000 字符（压缩前 4,708）', descChars < 2000, descChars)
ok('描述里保留"不确定就 op=catalog"的指路', descBlock.indexOf('catalog') > 0)
ok('描述覆盖 16 family / 117 op 的说法与 catalog 一致',
  descBlock.indexOf(String(cat.families.length) + ' 个 family') > 0 && descBlock.indexOf(String(cat.ops_count) + ' 个 op') > 0)

// ── D2：描述预算与"工具参数细节"入口（重工具的长尾搬进 catalog）
const detail = catalogPayload({ tool: 'rt_headless' })
ok('args={tool:"rt_headless"} 能取到参数细节', detail.ok === true && detail.text.indexOf('blender_rt_headless') === 0,
  JSON.stringify(detail).slice(0, 120))
ok('细节里有长尾信息（as_job / promoted / preload）',
  detail.text.indexOf('as_job') > 0 && detail.text.indexOf('promoted') > 0 && detail.text.indexOf('preload') > 0)
ok('细节体量合理（800–2500 字符，按需取不常驻）', detail.text.length >= 800 && detail.text.length <= 2500, detail.text.length)
const missDetail = catalogPayload({ tool: 'rt_jobb' })
ok('未知 tool 报错并列出可查清单', missDetail.ok === false && Array.isArray(missDetail.tools) && missDetail.tools.length >= 5)
ok('catalog 索引里指路了"工具参数细节"', catalogPayload({}).text.indexOf('工具参数细节') > 0)
// 曾经的假信息源：engine.mjs 里 TOOL_DETAIL.rt_plan 硬写"16 family / 117 op"，catalog 已经 27/176 了
const planDetail = catalogPayload({ tool: 'rt_plan' })
ok('rt_plan 细节里的 family/op 数由 PLAN_CATALOG 现算（不再硬编码而过期）',
  planDetail.ok === true
  && planDetail.text.indexOf(String(cat.families.length) + ' family') > 0
  && planDetail.text.indexOf(String(cat.ops_count) + ' op') > 0
  && planDetail.text.indexOf('117 op') < 0,
  planDetail.text.split('\n')[0])
ok('rt_plan 细节里写明了目录自证的口径（内容指纹 / 不看 mtime / verify:true 裁判）',
  planDetail.text.indexOf('mtime') > 0 && planDetail.text.indexOf('verify:true') > 0
  && planDetail.text.indexOf('loadedHash') > 0, planDetail.text.slice(0, 120))

// 只量"真正发给模型的" description + parameters（execute 里的代码字符串不算 schema）
function schemaChars(name) {
  const i = srcTs.indexOf("name: '" + name + "'")
  const d0 = srcTs.indexOf('description:', i)
  const p0 = srcTs.indexOf('parameters: {', d0)
  const p1 = srcTs.indexOf('\n    },', p0)
  const seg = srcTs.slice(d0, p1)
  return (seg.match(/'((?:[^'\\]|\\.)*)'/g) || []).join('').length
}
const TOOL_NAMES = (srcTs.match(/name: '(blender_rt_[a-z_]+|blender_viewport)'/g) || [])
  .map((x) => x.slice(7, -1))
const rows = TOOL_NAMES.map((n) => [n, schemaChars(n)])
const total = rows.reduce((a, b) => a + b[1], 0)
const worst = rows.slice().sort((a, b) => b[1] - a[1])[0]
ok('15 个工具 schema 合计 <= 12000 字符（v0.9.4 基线 17,147）', total <= 12000, String(total))
ok('单工具 schema 都 < 1600 字符（v0.9.4 最大 4,020）', worst[1] < 1600, worst[0] + '=' + String(worst[1]))
ok('rt_headless 已压到 < 1600 字符（原 4,020）', (rows.find((b) => b[0] === 'blender_rt_headless') || [0, 1e9])[1] < 1600)
ok('重工具都在 catalog 里有细节条目', ['rt_headless', 'rt_job', 'rt_worker', 'rt_see', 'rt_loop', 'viewport'].every((k) => catalogPayload({ tool: k }).ok === true))

// ── 真 HTTP 路由（不需要 Blender：catalog 本地直出、cmd 纠错也在本地）
console.log('  隔离后端：http://127.0.0.1:' + String(PORT))
const backend = spawn(process.execPath, [path.join(PKG, 'runtime', 'server.mjs'), '--port', String(PORT)], {
  env: Object.assign({}, process.env, { DSH_BLENDER_WORKDIR: TMP, DSH_BLENDER_HTTP_PORT: String(PORT) }),
  stdio: ['ignore', 'pipe', 'pipe'],
})
let log = ''
backend.stdout.on('data', (d) => { log += d.toString('utf8') })
backend.stderr.on('data', (d) => { log += d.toString('utf8') })
process.on('exit', () => { try { backend.kill('SIGKILL') } catch (e) {} })
let up = false
for (let i = 0; i < 40; i++) { if (await getJson(PORT, '/health')) { up = true; break } await sleep(300) }
ok('隔离后端已就绪', up, log.slice(-200))
if (up) {
  const r1 = await rpc(PORT, '/plan', { op: 'catalog', args: {} })
  ok('/plan op=catalog 不依赖 Blender 也能回目录', r1.body && r1.body.ok === true && (r1.body.result || {}).text,
    JSON.stringify(r1.body).slice(0, 160))
  const r2 = await rpc(PORT, '/plan', { op: 'catalog', args: { family: 'print' } })
  ok('/plan op=catalog args={family:print} 只回该族', r2.body && r2.body.ok === true
    && String((r2.body.result || {}).family) === 'print', JSON.stringify(r2.body).slice(0, 160))
  const r2b = await rpc(PORT, '/plan', { op: 'catalog', args: { verify: true } })
  ok('/plan op=catalog args={verify:true} 端到端带磁盘对拍（干净进程裁判）',
    !!(r2b.body && r2b.body.ok === true && r2b.body.result && r2b.body.result.provenance
       && r2b.body.result.provenance.diskVerdict && r2b.body.result.provenance.diskVerdict.ok === true),
    JSON.stringify(r2b.body).slice(0, 220))
  const r3 = await rpc(PORT, '/cmd', { name: 'qc_render_catalog', params: {} })
  const res3 = String((r3.body && r3.body.result && r3.body.result.result) || '')
  ok('/cmd 收到 plan op 名 → 当场纠正（CMD_HINT）', res3.indexOf('CMD_HINT') === 0, res3.slice(0, 160))
  const r4 = await rpc(PORT, '/cmd', { name: 'describe_node_type', params: { bl_idname: 'ShaderNodeTexNoise' } })
  ok('/cmd 对真 addon 命令不拦（无 Blender 时是连接类错误，而不是 CMD_HINT）',
    !(String((r4.body && r4.body.result && r4.body.result.result) || '').indexOf('CMD_HINT') === 0),
    JSON.stringify(r4.body).slice(0, 160))
  try { backend.kill('SIGKILL') } catch (e) {}
}

console.log('')
console.log('通过 ' + String(pass) + ' 项' + (fail ? ('，失败 ' + String(fail) + ' 项：' + failures.join(' / ')) : '，全部通过'))
process.exit(fail ? 1 : 0)
