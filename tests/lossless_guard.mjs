#!/usr/bin/env node
/**
 * tests/lossless_guard.mjs —— v0.9.4（P0 返回通道）回归门
 *
 * 为什么有这个文件（外部反馈 2026-09-25《M1A1 分件建模》）：
 *   宿主 DSH 对每个工具的**返回值**跑 dsh-util-values 的 walkJsonValue，规则比
 *   JSON.stringify 严格得多：undefined 值 / NaN / ±Infinity / -0 / Date / Map / Set /
 *   类实例 / 循环引用 一律判 `value is not lossless JSON`，并把**整条工具通道**打死。
 *   v0.9.3 的 envelope 里一个 `promoted: undefined` 就造成了：
 *     - blender_rt_headless **每次**失败（任务其实跑完、结果已在磁盘，只是回执被拒）
 *     - blender_rt_job op=status/collect/wait 同病 → 「任务能交出去但收不回来」
 *   所以本测试用**宿主的真校验器**（不是规则的副本）断言：本插件任何一条回执都过得了门。
 *
 * 用法：
 *   node tests/lossless_guard.mjs            # 离线用例（不需要 Blender/后端）
 *   node tests/lossless_guard.mjs --live     # 追加真机用例（需要后端 9877 + blender.exe）
 *   DSH_UTIL_VALUES=<path>                   # 显式指定宿主真校验器
 */
import { createRequire } from 'node:module'
import { existsSync, readdirSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const PKG = path.join(HERE, '..')
const LIVE = process.argv.includes('--live')

let pass = 0, fail = 0, skipped = 0
const failures = []
function ok(name, cond, detail) {
  if (cond) { pass++; console.log('  ✓ ' + name) }
  else { fail++; failures.push(name + (detail ? (' — ' + detail) : '')); console.log('  ✗ ' + name + (detail ? (' — ' + detail) : '')) }
}
function skip(name, why) { skipped++; console.log('  · skip ' + name + ' — ' + why) }
function section(t) { console.log('\n== ' + t + ' ==') }

/* ── 1. 找宿主的**真**校验器 ───────────────────────────────────────────────── */
function findValidatorIn(dir, depth) {
  if (depth < 0 || !existsSync(dir)) return null
  let entries = []
  try { entries = readdirSync(dir, { withFileTypes: true }) } catch (e) { return null }
  for (const e of entries) {
    if (!e.isDirectory()) continue
    if (e.name === 'dsh-util-values') {
      const p = path.join(dir, e.name, 'lib', 'index.js')
      if (existsSync(p)) return p
    }
  }
  for (const e of entries) {
    if (!e.isDirectory()) continue
    if (e.name === 'node_modules' || e.name.startsWith('.')) continue
    const hit = findValidatorIn(path.join(dir, e.name), depth - 1)
    if (hit) return hit
  }
  return null
}
function candidatePaths() {
  const out = []
  if (process.env.DSH_UTIL_VALUES) out.push(process.env.DSH_UTIL_VALUES)
  try { out.push(createRequire(pathToFileURL(path.join(PKG, 'package.json'))).resolve('@deepseek-ai/dsh-util-values')) } catch (e) { /* 继续 */ }
  out.push('/usr/lib/node_modules/@deepseek-ai/dsh/node_modules/@deepseek-ai/dsh-util-values/lib/index.js')
  for (const root of [process.env.DSH_CHECKOUT, path.join(os.homedir(), 'dsh-harness'), path.join(os.homedir(), 'dsh')].filter(Boolean)) {
    const hit = findValidatorIn(root, 5)
    if (hit) out.push(hit)
  }
  return out.filter((p) => p && existsSync(p))
}
async function loadValidator() {
  for (const p of candidatePaths()) {
    try {
      const m = await import(pathToFileURL(p).href)
      if (typeof m.snapshotJsonValue === 'function') return { fn: m.snapshotJsonValue, from: p }
    } catch (e) { /* 试下一个 */ }
  }
  const local = (v) => {
    const seen = new Set()
    const walk = (x) => {
      if (x === null) return true
      const t = typeof x
      if (t === 'string' || t === 'boolean') return true
      if (t === 'number') return Number.isFinite(x) && !Object.is(x, -0)
      if (t !== 'object') return false
      if (seen.has(x)) return false
      seen.add(x)
      if (Array.isArray(x)) { if (Object.keys(x).length !== x.length) return false; const r = x.every(walk); seen.delete(x); return r }
      if (Object.getPrototypeOf(x) !== Object.prototype && Object.getPrototypeOf(x) !== null) return false
      for (const k of Object.keys(x)) if (!walk(x[k])) { seen.delete(x); return false }
      seen.delete(x); return true
    }
    return walk(v) ? v : undefined
  }
  return { fn: local, from: '(local-copy — 未找到宿主真校验器)' }
}

/* ── 2. 断言：这个值必须过得了宿主门 ───────────────────────────────────────── */
function mustPass(V, label, value) {
  const det = V.fn(value)
  ok(label, det !== undefined, det === undefined ? '宿主判：value is not lossless JSON' : null)
  return det
}
function mustReject(V, label, value) {
  ok(label, V.fn(value) === undefined, V.fn(value) !== undefined ? '宿主竟然接受了（校验器行为变了？）' : null)
}

const V = await loadValidator()
console.log('宿主真校验器：' + V.from)

const mod = await import(pathToFileURL(path.join(PKG, 'lib', 'index.js')).href)
const I = mod.__internals
ok('lib/index.js 暴露测试接缝 __internals', !!I && typeof I.losslessSanitize === 'function' && typeof I.receiptEnvelope === 'function')
if (!I) { console.log('\n致命：测试接缝缺失，先 npm run build'); process.exit(1) }
console.log('被测量的插件版本：v' + I.PLUGIN_VERSION)

/* ── 3. 控制组：证明校验器确实是严的（否则下面的绿都是假的） ───────────────── */
section('控制组（校验器严格性自证）')
mustReject(V, 'promoted:undefined 必须被拒（v0.9.3 的真实病因）', { kind: 'headless', promoted: undefined })
mustReject(V, 'NaN 必须被拒', { x: NaN })
mustReject(V, 'Infinity 必须被拒', { x: Infinity })
mustReject(V, '-0 必须被拒', { x: -0 })
mustReject(V, 'JSON.parse("-0") 必须被拒', { x: JSON.parse('-0') })
mustReject(V, 'Date 必须被拒', { d: new Date() })
mustReject(V, '数组里的 undefined 必须被拒', { a: [1, undefined] })
mustPass(V, '对照：null 值合法', { x: null })

/* ── 4. 出口消毒器：敌意载荷 → 过门 ───────────────────────────────────────── */
section('出口消毒器 losslessSanitize')
const hostile = {
  ok: true, nan: NaN, inf: Infinity, ninf: -Infinity, neg0: -0,
  drop: undefined, when: new Date('2026-09-25T00:00:00Z'), map: new Map([['a', 1]]),
  set: new Set([1, 2]), buf: new Uint8Array([1, 2, 3]), big: 10n,
  fn: () => 1, arr: [1, undefined, NaN, -0, { deep: undefined, keep: 2 }],
  nested: { a: { b: { c: undefined, d: NaN } } }, unicode: '焊缝 193mm',
}
const s1 = I.losslessSanitize(hostile)
mustPass(V, '消毒后整体过门', s1.value)
ok('每个问题字段都留下 fixes 记录（不静默）', s1.fixes.length >= 8, 'fixes=' + s1.fixes.length)
ok('NaN → null', s1.value.nan === null)
ok('Infinity → null', s1.value.inf === null)
ok('-0 → 0（且不是 -0）', s1.value.neg0 === 0 && !Object.is(s1.value.neg0, -0))
ok('undefined 键被丢弃', !('drop' in s1.value))
ok('Date → ISO 字符串', typeof s1.value.when === 'string' && s1.value.when.indexOf('2026-09-25') === 0)
ok('Map/Set → 数组', Array.isArray(s1.value.map) && Array.isArray(s1.value.set))
ok('二进制 → {bytes:N}', !!s1.value.buf && s1.value.buf.bytes === 3)
ok('bigint → number', s1.value.big === 10)
ok('数组元素 undefined → null（长度不塌）', Array.isArray(s1.value.arr) && s1.value.arr.length === 5 && s1.value.arr[1] === null)
ok('嵌套 undefined 键被丢弃、NaN 变 null', !('c' in s1.value.nested.a.b) && s1.value.nested.a.b.d === null)
const cyc = { a: 1 }; cyc.self = cyc
const s2 = I.losslessSanitize(cyc)
mustPass(V, '循环引用过门', s2.value)
ok('循环引用 → [Circular]', s2.value.self === '[Circular]')
const dag = { x: 1 }; const shared = { s: dag, t: dag }
mustPass(V, 'DAG 共享引用（非循环）过门', I.losslessSanitize(shared).value)

/* ── 5. 真实回执构造器：sync / job / promoted 三条路 ──────────────────────── */
section('真实回执（receiptEnvelope / headlessReceipt / promotedReceipt）')
// 5.1 **干净**输入：回执构造器必须**不需要消毒**就直接过门 —— 这就是 v0.9.3 搞挂的那条路
//     （envelope 里一个 promoted:undefined 让 headless/job 全线失败）。
const cleanResult = {
  status: 'done', ok: true, id: 'run-1789', runId: 'run-1789', ms: 1082, exitCode: 0, timedOut: false,
  resultJson: { ok: true, count: 83, pitch: 0.176, name: '履带节距' },
  resultTruncated: false, resultPath: 'D:\\DSH\\blender\\tmp\\jobs\\run-1789\\result.json',
  stdoutTail: 'HEADLESS {"ok":true}' + String.fromCharCode(10), stderrTail: '', outdir: 'D:\\DSH\\blender\\tmp',
  artifacts: [{ name: 'a.blend', bytes: 1024, fresh: true }, 'b.png'],
  logs: { stdout: 'D:\\x\\stdout.log', stderr: 'D:\\x\\stderr.log' },
  pathWarnings: [{ code: 'posix-path-to-win-api', evidence: '/home/x', hint: '用 K.win_path' }],
  engine: 'EEVEE', gpu: { ok: true, configured: 'OPTIX', after: { device_type: 'OPTIX', gpu_enabled: ['CUDA'] } },
  stage: { name: 'building' }, stageAt: 1790000000000, idleMs: 12, lines: 40, logBytes: 2048,
  scriptFile: { resolved: 'D:\\x\\s.py', given: 's.py', bytes: 120 }, inputFile: { path: 'D:\\x\\in.blend', size: 10, md5: 'abc' },
}
const envSync = I.receiptEnvelope(cleanResult, { mode: 'sync', jobId: null, notes: [] })
mustPass(V, 'sync 回执（干净输入）直接过门 ← v0.9.3 这里必挂', envSync)
ok('sync 回执里没有 promoted 键（是"有才给键"，不是 undefined 值）', !('promoted' in envSync))
ok('sync 回执 resultJson 保真', !!envSync.resultJson && envSync.resultJson.count === 83)
ok('sync 回执不依赖消毒（receiptEnvelope 自身就是 lossless）', I.losslessSanitize(envSync).fixes.length === 0,
  'fixes=' + JSON.stringify(I.losslessSanitize(envSync).fixes.slice(0, 4)))
const envJob = I.receiptEnvelope(Object.assign({}, cleanResult, { status: 'finished', kind: 'job' }), { mode: 'job', jobId: 'job-42', notes: ['后台化完成'] })
mustPass(V, 'job 回执（干净输入）直接过门', envJob)
ok('job 回执 mode/status 正确', envJob.mode === 'job' && envJob.ok === true && envJob.jobId === 'job-42')
const promoted = I.promotedReceipt({ status: 'running', id: 'run-1', runId: 'run-1', jobId: 'run-1' }, 'run-1', '等待窗口到点')
mustPass(V, 'promoted 回执过门', promoted)
ok('promoted 回执（人读文本）非空', typeof promoted.text === 'string' && promoted.text.length > 20)
ok('promoted 回执 envelope.promoted === true', promoted.envelope.promoted === true)
const hrec = I.headlessReceipt(cleanResult, { mode: 'sync', jobId: null, notes: [] })
mustPass(V, 'headlessReceipt（text+envelope 整体）过门', hrec)
ok('headlessReceipt 两个块都在', typeof hrec.text === 'string' && !!hrec.envelope)
// 5.2 反证：谁把 promoted 键写回 undefined，这条立刻变红（防回归）
mustReject(V, '反证：envelope 带 promoted:undefined 必被拒（所以只能"有才给键"）',
  Object.assign({}, envSync, { promoted: undefined }))

// 5.3 敌意输入：后端把 -0 / NaN / undefined 塞进来 → 出口消毒器兜底（不静默）
const evilResult = Object.assign({}, cleanResult, {
  ms: NaN, idleMs: -0, exitCode: undefined, resultJson: { v: [Infinity, -0, null], neg_zero: JSON.parse('-0') },
  artifacts: [{ name: 'x', bytes: undefined }], shots: { ok: true, rows: [{ name: 'front', ms: NaN, coverage_estimate: -0 }] },
})
const evilEnv = I.receiptEnvelope(evilResult, { mode: 'job', jobId: 'job-9', notes: [] })
ok('敌意回执原样确实过不了门（说明消毒器是必需的，不是装饰）', V.fn(evilEnv) === undefined)
const evilSani = I.losslessSanitize(evilEnv)
mustPass(V, '敌意回执经出口消毒后过门', evilSani.value)
ok('敌意回执的消毒留下了 fixes 记录', evilSani.fixes.length > 0, 'fixes=' + evilSani.fixes.length)
ok('shot 行里的 -0 coverage 被消成 0', evilSani.value.shots.rows[0].coverage_estimate === 0)

/* ── 6. 真机（--live）：真的把工具跑一遍，再拿真校验器验 ─────────────────── */
section('真机用例（' + (LIVE ? '--live' : '默认跳过；加 --live 打开') + '）')
if (!LIVE) {
  skip('blender_rt_headless 真实回执', '未加 --live')
  skip('blender_rt_job op=status 真实回执', '未加 --live')
} else {
  const tools = new Map()
  const ctx = {
    effect: (fn) => { try { const d = fn && fn(); return typeof d === 'function' ? d : () => {} } catch (e) { return () => {} } },
    tools: { register: (spec) => { if (spec && spec.name) tools.set(spec.name, spec); return () => {} } },
    get: () => undefined,
    logger: { info() {}, warn() {}, error() {} },
  }
  let cfg = { port: 9877, autoStart: false }
  try {
    const c = await import(pathToFileURL(path.join(PKG, 'runtime', 'config.mjs')).href)
    if (c.CFG && c.CFG.httpPort) cfg = { port: c.CFG.httpPort, autoStart: false }
  } catch (e) { /* 用默认端口 */ }
  mod.apply(ctx, cfg)
  ok('apply() 注册出全部工具（v0.9.4 起含启动能力）', tools.size >= 15, 'tools=' + tools.size)

  const headless = tools.get('blender_rt_headless')
  ok('blender_rt_headless 已注册', !!headless)
  if (headless) {
    const py1 = ['import json', 'print("HEADLESS " + json.dumps({"ok": True, "phase": "lossless-guard"}))'].join(String.fromCharCode(10))
    const out1 = await headless.execute({ script: py1, engine: 'none', timeout_ms: 120000 })
    mustPass(V, 'L1 blender_rt_headless 真实回执过门（v0.9.3 必挂）', out1)
    ok('L1 envelope.resultJson 取到脚本结果', !!(out1 && out1.envelope && out1.envelope.resultJson && out1.envelope.resultJson.ok === true),
      'resultJson=' + JSON.stringify(out1 && out1.envelope && out1.envelope.resultJson))
    const py2 = ['import json', 'print("HEADLESS " + json.dumps({"ok": True, "neg_zero": -0.0, "list": [-0.0, 1.0]}))'].join(String.fromCharCode(10))
    const out2 = await headless.execute({ script: py2, engine: 'none', timeout_ms: 120000 })
    mustPass(V, 'L2 结果里带 -0 的回执过门', out2)
    const rj = out2 && out2.envelope && out2.envelope.resultJson
    ok('L2 -0 已被消毒成 0', !!rj && rj.neg_zero === 0 && !Object.is(rj.neg_zero, -0), 'resultJson=' + JSON.stringify(rj))
    const job = tools.get('blender_rt_job')
    if (job && out1 && out1.envelope && (out1.envelope.runId || out1.envelope.id)) {
      const id = out1.envelope.runId || out1.envelope.id
      const st = await job.execute({ op: 'status', id: id })
      mustPass(V, 'L3 blender_rt_job op=status 真实回执过门', st)
    } else skip('L3 blender_rt_job op=status', '没拿到 runId')
  }
}

/* ── 7. 汇总 ─────────────────────────────────────────────────────────────── */
console.log(String.fromCharCode(10) + '─'.repeat(64))
console.log('lossless-guard：' + pass + ' 通过 / ' + fail + ' 失败 / ' + skipped + ' 跳过'
  + (V.from.indexOf('local-copy') >= 0 ? '（⚠ 用的是本地规则副本，未能加载宿主真校验器）' : ''))
if (fail) {
  console.log('失败项：')
  for (const f of failures) console.log('  - ' + f)
  process.exit(1)
}
console.log('OK：工具回执全部过得了宿主 lossless-JSON 门。')
process.exit(0)


