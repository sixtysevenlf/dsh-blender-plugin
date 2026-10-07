#!/usr/bin/env node
/**
 * P2 · 声明式搜索（plan op `shape_search`）自检
 *
 * 为什么需要：它是本包里**唯一由 JS 本地编排**的 plan op（不进 python dispatch），
 * 一旦路由 / 校验 / spec 生成任一处接错，模型拿到的不是"能跑的内环"而是一串看不懂的报错。
 *
 * 覆盖：
 *   A 目录面    —— op 进了 planOpNames / shape 家族 / 不是只读 op（会改场景 ⇒ 要过写租约）
 *   B spec 生成 —— grid（含 pyLit 布尔）/ random / aabb_err / 多视平均 四种形态
 *   C 校验面    —— 缺 params / 缺 ref / 非法 objective / grid 组合爆炸 → ok:false + 可照抄骨架
 *   D 真路由    —— 起隔离后端 POST /plan，证明"接上了"（dry_run 不需要 Blender）
 *
 * 用法：node tests/shape_search_selftest.mjs
 */
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { spawn, spawnSync } from 'node:child_process'
import { fileURLToPath, pathToFileURL } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const ROOT = path.join(HERE, '..')
const engine = await import(pathToFileURL(path.join(ROOT, 'runtime', 'engine.mjs')).href)
const PORT = Number(process.env.DSH_SELFTEST_SEARCH_PORT || 9895)
const TMP = fs.mkdtempSync(path.join(os.tmpdir(), 'dsh-shape-search-'))

let pass = 0
let fail = 0
const failures = []
function ok(name, cond, detail) {
  if (cond) { pass++; console.log('  ✓ ' + name) }
  else { fail++; failures.push(name); console.log('  ✗ ' + name + (detail === undefined ? '' : ' — ' + String(detail).slice(0, 220))) }
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
async function getJson(p, route) { try { return await (await fetch('http://127.0.0.1:' + String(p) + route)).json() } catch (e) { return null } }
async function rpc(p, route, body, ms) {
  const ctl = new AbortController()
  const t = setTimeout(() => ctl.abort(), ms || 60000)
  try {
    const r = await fetch('http://127.0.0.1:' + String(p) + route, {
      method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body || {}), signal: ctl.signal,
    })
    return { status: r.status, body: await r.json() }
  } finally { clearTimeout(t) }
}

console.log('== 声明式搜索自检（P2 · shape_search）==')

// ── A：目录面
const names = engine.planOpNames()
ok('planOpNames 含 shape_search', names.indexOf('shape_search') >= 0, names.length + ' 个 op')
const cat = engine.catalogPayload({})
ok('planOpNames 条数 == catalog ops_count（无孤儿 / 无漏列）', names.length === cat.ops_count, names.length + ' vs ' + cat.ops_count)
const shapeRow = engine.PLAN_CATALOG.find((f) => f.f === 'shape') || {}
ok('shape 家族 ops 里列了 search', (shapeRow.ops || []).indexOf('search') >= 0, JSON.stringify(shapeRow.ops))
ok('shape_search 不在只读白名单（它会通过 apply 改场景 ⇒ 要写租约）', !engine.PLAN_READ_ONLY.has('shape_search'))
const famText = engine.catalogPayload({ family: 'shape' }).text
ok('单族展开文本里出现 shape_search（模型查目录就能看见）', famText.indexOf('search') > 0, famText.slice(0, 120))

// ── B/C/D：真 HTTP（dry_run 在启动内环前就返回 ⇒ 不需要 Blender）
const backend = spawn(process.execPath, [path.join(ROOT, 'runtime', 'server.mjs'), '--port', String(PORT)], {
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

const VIEW = { from: [0, -6, 1.0], look_at: [0, 0, 0.5], lens: 50, ortho: true }
const APPLY = 'ob.location.y = p["dy_mm"] / 1000.0\n    bpy.context.view_layer.update()'
const callSearch = (args) => rpc(PORT, '/plan', { op: 'shape_search', args: args })

if (up) {
  // B1 网格：13 档 → grid，且布尔必须渲染成 Python 的 True（不是 JSON 的 true）
  const r1 = await callSearch({ objective: 'silhouette_iou', ref: 'D:/ref/side.png', view: VIEW, apply: APPLY,
                               params: { dy_mm: { min: -30, max: 30, step: 5 } }, iterations: 200, budget_ms: 30000, dry_run: true })
  const res1 = (r1.body && r1.body.result) || {}
  ok('dry_run 回执 ok=true', res1.ok === true && res1.dry_run === true, JSON.stringify(res1).slice(0, 200))
  ok('小空间自动选 grid，并把迭代数收敛到组合数', res1.spec_echo && res1.spec_echo.strategy === 'grid' && res1.spec_echo.combos === 13 && res1.spec_echo.iterations === 13,
    JSON.stringify(res1.spec_echo))
  const spec1 = res1.spec || {}
  ok('spec 三段齐备且是有内容的字符串', typeof spec1.setup === 'string' && typeof spec1.step === 'string' && typeof spec1.measure === 'string'
    && spec1.setup.length > 10 && spec1.step.indexOf('_axes') >= 0 && spec1.measure.indexOf('silhouette_iou') >= 0,
    JSON.stringify({ setup: typeof spec1.setup, step: typeof spec1.step, measure: typeof spec1.measure }))
  ok('setup 里定义了 apply(p)（op=export 的复现约定）', spec1.setup.indexOf('def apply(p):') > 0)
  ok('pyLit：JSON 的 true 必须落成 Python 的 True', spec1.measure.indexOf('True') > 0 && !/[:,\[\s]true[,}\]]/.test(spec1.measure),
    (spec1.measure.match(/.{0,40}ortho.{0,20}/) || ['(无 ortho)'])[0])

  // B2 随机：三维大区间 + 迭代数很小 → random + uniform
  const r2 = await callSearch({ objective: 'silhouette_iou', ref: 'D:/ref/side.png', view: VIEW, apply: APPLY,
                               params: { a: { min: 0, max: 100 }, b: { min: 0, max: 100 }, c: { min: 0, max: 100 } },
                               iterations: 5, budget_ms: 5000, dry_run: true })
  const res2 = (r2.body && r2.body.result) || {}
  ok('大空间自动选 random', res2.spec_echo && res2.spec_echo.strategy === 'random', JSON.stringify(res2.spec_echo))
  ok('random 的 step 用 random.uniform', (res2.spec || {}).step && res2.spec.step.indexOf('random.uniform') > 0, ((res2.spec || {}).step || '').slice(0, 120))

  // B2b 生成的 Python 必须**真能编译**：实测踩过"apply 续行多缩进 4 格 → IndentationError"，
  //     只做字符串断言抓不到（ci 里也不会有人肉眼看回执）。
  const PY_PROBE = [
    'import sys, json, random',
    'specs = json.load(sys.stdin)',
    'for s in specs:',
    "    for k in ('setup', 'step', 'measure'):",
    "        compile(str(s.get(k, 'pass')), '<' + k + '>', 'exec')",
    "ns = {'random': random, 'i': 0}",
    "ns['ns'] = ns",   // 与 runner 的命名空间自引用一致（v1.0.3 修：没有它 ns[...] 就是 NameError）
    'seen = []',
    'for i in range(5):',
    "    ns['i'] = i",
    "    exec(compile(str(specs[0]['step']), '<step>', 'exec'), ns)",
    "    seen.append(json.dumps(ns['params'], sort_keys=True))",
    "print(json.dumps({'distinct': len(set(seen)), 'sample': seen[0]}))",
  ].join('\n')
  const pythonCompile = (specs) => {
    let exe = null
    for (const c of ['python3', 'python']) { const r = spawnSync(c, ['-V']); if (r.status === 0) { exe = c; break } }
    if (!exe) return { ok: true, skipped: 'no python3' }
    const r = spawnSync(exe, ['-c', PY_PROBE], { input: JSON.stringify(specs), encoding: 'utf8' })
    if (r.status !== 0) return { ok: false, error: String(r.stderr || '').split('\n').slice(-4).join(' | ').slice(0, 300) }
    let stepRun = null
    try { stepRun = JSON.parse(String(r.stdout).trim().split('\n').pop()) } catch (e) { stepRun = null }
    return { ok: true, stepRun: stepRun }
  }
  const pyc1 = pythonCompile([spec1, (res2.spec || {})])
  ok('生成的 setup/step/measure 能被 python3 真编译（grid 与 random 两种 spec）', pyc1.ok === true,
    pyc1.error || pyc1.skipped || 'compile failed')
  if (pyc1.stepRun) {
    ok('grid 的 step 用桩 ns 跑 5 轮 → 5 组不同参数', pyc1.stepRun.distinct === 5, JSON.stringify(pyc1.stepRun))
  }

  // B2c 嵌套块（if/else / for）是 normBody 的高危路径：缩进归一化不能把相对层次压平
  const APPLY_NESTED = "if p['dy_mm'] > 0:\n    ob.location.y = p['dy_mm'] / 1000.0\nelse:\n    ob.location.y = -p['dy_mm'] / 1000.0\n    bpy.context.view_layer.update()"
  const r5 = await callSearch({ objective: 'silhouette_iou', ref: 'D:/ref/side.png', view: VIEW, apply: APPLY_NESTED,
                               params: { dy_mm: { min: -10, max: 10, step: 5 } }, iterations: 10, budget_ms: 5000, dry_run: true })
  const spec5 = ((r5.body && r5.body.result) || {}).spec || {}
  const pyc2 = pythonCompile([spec5])
  ok('嵌套块 apply（if/else）也编译通过，且相对层次保留',
    pyc2.ok === true && spec5.setup.indexOf('    ob.location.y = p') > 0 && spec5.setup.indexOf('        ob.location.y = -p') > 0,
    pyc2.error || (spec5.setup || '').split('\n').slice(3).join(' | ').slice(0, 200))

  // B3 枚举 + aabb_err（不出图的目标）；显式 strategy:"random" 才走枚举抽样（auto 在小空间会选 grid）
  const r3 = await callSearch({ objective: 'aabb_err', target: 'GEO-hull', target_size: [1.0, 2.0, 3.0], strategy: 'random',
                               apply: APPLY, params: { n: { values: [6, 10, 16] }, k: { min: 0, max: 1, step: 0.5 } },
                               iterations: 60, budget_ms: 10000, dry_run: true })
  const res3 = (r3.body && r3.body.result) || {}
  const spec3 = res3.spec || {}
  ok('aabb_err 生成的目标函数正确', res3.ok === true && spec3.measure.indexOf('aabb_err') >= 0 && spec3.measure.indexOf('[1, 2, 3]') >= 0,
    (spec3.measure || '').slice(0, 140))
  ok('枚举维度走 random.choice', spec3.step.indexOf('random.choice') >= 0 && spec3.step.indexOf('[6, 10, 16]') >= 0, (spec3.step || '').slice(0, 160))

  // B4 多视：取平均 + 逐视指标
  const r4 = await callSearch({ objective: 'silhouette_iou', ref: 'D:/ref/side.png', views: [VIEW, { from: [6, 0, 1.0], look_at: [0, 0, 0.5], lens: 50 }],
                               apply: APPLY, params: { dy_mm: { min: -10, max: 10, step: 10 } }, iterations: 10, budget_ms: 5000, dry_run: true })
  const spec4 = ((r4.body && r4.body.result) || {}).spec || {}
  ok('多视取平均并回逐视指标', spec4.measure.indexOf('sum(_ss)') > 0 && spec4.measure.indexOf('per_view') > 0, (spec4.measure || '').slice(0, 140))

  // C 校验面：都要 ok:false 且给可照抄骨架
  const cases = [
    ['缺 params', { objective: 'silhouette_iou', ref: 'D:/r.png', view: VIEW, apply: APPLY }],
    ['缺 ref', { objective: 'silhouette_iou', view: VIEW, apply: APPLY, params: { d: { min: 0, max: 1 } } }],
    ['非法 objective', { objective: 'nope', params: { d: { min: 0, max: 1 } } }],
    ['渲染类目标缺 apply', { objective: 'silhouette_iou', ref: 'D:/r.png', view: VIEW, params: { d: { min: 0, max: 1 } } }],
    ['grid 组合爆炸（显式 strategy:"grid"）', { objective: 'aabb_err', target: 'X', target_size: [1, 1, 1], apply: APPLY, strategy: 'grid',
                        params: { a: { min: 0, max: 100, step: 1 }, b: { min: 0, max: 100, step: 1 }, c: { min: 0, max: 100, step: 1 },
                                  d: { min: 0, max: 100, step: 1 } } }],
  ]
  for (const [label, args] of cases) {
    const rr = await callSearch(Object.assign({ dry_run: true }, args))
    const res = (rr.body && rr.body.result) || {}
    ok('校验：' + label + ' → ok:false + 骨架', res.ok === false && typeof res.skeleton === 'string' && res.skeleton.indexOf('shape_search') > 0,
      JSON.stringify(res).slice(0, 180))
  }
  try { backend.kill('SIGKILL') } catch (e) {}
}

console.log('')
console.log('通过 ' + String(pass) + ' 项' + (fail ? ('，失败 ' + String(fail) + ' 项：' + failures.join(' / ')) : '，全部通过'))
process.exit(fail ? 1 : 0)
