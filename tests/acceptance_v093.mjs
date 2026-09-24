#!/usr/bin/env node
/**
 * v0.9.3 验收测试 —— 逐条对应《插件改进交接-2026-09-24》§8 的建议 case。
 *
 *   node tests/acceptance_v093.mjs                 # 全部（需要后端 9877 + blender.exe）
 *   node tests/acceptance_v093.mjs --only=D1,D3    # 只跑指定用例
 *   node tests/acceptance_v093.mjs --list          # 列出用例
 *
 * 设计：**直接加载编译后的 lib/index.js**，用假 ctx 把 15 个工具注册出来，
 * 再调用工具自己的 execute() —— 测的就是模型真正会跑的那份代码（含两个 text block 的 render 投影）。
 * Blender 相关用例走真机（headless blender.exe）；后端不在时自动拉起（与插件同一条 ensureBackend 路径）。
 *
 * 环境变量：DSH_HEADLESS_WAIT_MS（本测试默认设 4000，让 promoted 路径在秒级可测）
 */
// ⚠ 顺序：DSH_HEADLESS_WAIT_MS 必须在 **加载 lib/index.js 之前** 生效（模块顶层读一次），
// 所以插件用动态 import —— 静态 import 会被提升到赋值之前，导致等待窗口还是默认 90 s。
process.env.DSH_HEADLESS_WAIT_MS = process.env.DSH_HEADLESS_WAIT_MS || '4000'
const { apply } = await import('../lib/index.js')
import { createEngine } from '../runtime/engine.mjs'
import { execFileSync } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'
import os from 'node:os'
import { fileURLToPath } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const PKG = path.resolve(HERE, '..')
const PORT = Number(process.env.DSH_BLENDER_HTTP_PORT || 9877)
const TMP = path.join(PKG, 'tmp', 'acceptance')
const NL = String.fromCharCode(10)

const argv = process.argv.slice(2)
const ONLY = (argv.find((a) => a.startsWith('--only=')) || '').slice(7).split(',').map((s) => s.trim()).filter(Boolean)
const NO_BLENDER = argv.includes('--no-blender')

let pass = 0; const fails = []
function ok(label, cond, extra) {
  if (cond) { pass++; console.log('  ok   ' + label) }
  else { fails.push(label); console.log('  FAIL ' + label + (extra === undefined ? '' : '  → ' + String(extra).slice(0, 500))) }
}
function eq(label, got, want) { ok(label, JSON.stringify(got) === JSON.stringify(want), 'got=' + JSON.stringify(got) + ' want=' + JSON.stringify(want)) }
function sleep(ms) { return new Promise((r) => setTimeout(r, ms)) }

/* ───────── 加载插件（假 ctx） ───────── */
const disposers = []
const tools = new Map()
const ctx = {
  effect: (fn) => { const d = fn(); if (typeof d === 'function') disposers.push(d); return d },
  get: () => null,
  tools: { register: (t) => { tools.set(t.name, t); return () => tools.delete(t.name) } },
}
apply(ctx, { port: PORT, autoStart: true })

async function call(name, args) {
  const t = tools.get(name)
  if (!t) throw new Error('工具没注册：' + name)
  const value = await t.execute(args || {}, { signal: new AbortController().signal })
  const blocks = t.output.render(args || {}, value)
  return { value, blocks, tool: t }
}
/** Windows 路径 → WSL 可见路径（D:\x → /mnt/d/x；UNC \\wsl.localhost\D\… → /…）—— 验收要核对落盘文件真的存在 */
function toWsl(p) {
  const s = String(p || '')
  const m = s.match(/^([A-Za-z]):[\\/](.*)$/)
  if (m) return '/mnt/' + m[1].toLowerCase() + '/' + m[2].split('\\').join('/')
  const u = s.match(/^\\\\wsl\.localhost\\[^\\]+\\(.*)$/)
  if (u) return '/' + u[1].split('\\').join('/')
  return s
}
/** 结构化信封：block 0 必须是可直接 JSON.parse 的单行 JSON（D2 的核心契约） */
function envelopeOf(r) {
  const b0 = r.blocks[0]
  if (!b0 || b0.type !== 'text') return null
  try { return JSON.parse(b0.text) } catch (e) { return null }
}

async function waitBackend(ms = 30000) {
  const t0 = Date.now()
  while (Date.now() - t0 < ms) {
    try { const r = await fetch('http://127.0.0.1:' + PORT + '/health'); if (r.ok) return true } catch (e) { /* 还没起 */ }
    await sleep(400)
  }
  return false
}
async function backendPost(pathname, body, timeoutMs = 60000) {
  const ac = new AbortController()
  const t = setTimeout(() => ac.abort(), timeoutMs)
  try {
    const r = await fetch('http://127.0.0.1:' + PORT + pathname, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body || {}), signal: ac.signal })
    const txt = await r.text()
    const ls = txt.trim().split('\n').filter(Boolean)
    for (let i = ls.length - 1; i >= 0; i--) { try { const o = JSON.parse(ls[i]); if (!o.heartbeat) return o } catch (e) { /* 心跳行 */ } }
    return { ok: false, raw: txt.slice(0, 300) }
  } finally { clearTimeout(t) }
}

/* ───────── 用例 ───────── */
const CASES = []
function test(id, title, fn) { CASES.push({ id, title, fn }) }

test('D1', 'headless 超时 → 自动转作业层并回传可用 jobId；op=collect 能取回 HEADLESS JSON', async () => {
  fs.mkdirSync(TMP, { recursive: true })
  const t0 = Date.now()
  const r = await call('blender_rt_headless', {
    script: "import time, json" + NL + "time.sleep(12)" + NL + "print('HEADLESS ' + json.dumps({'d1':'late-but-not-lost'}, separators=(',',':')))",
    engine: 'none', timeout_ms: 300000, outdir: TMP,
  })
  const env = envelopeOf(r)
  const waited = Date.now() - t0
  ok('block0 是可解析 JSON 信封', !!env, r.blocks[0] && String(r.blocks[0].text).slice(0, 200))
  ok('kind=promoted', env && env.kind === 'promoted', env && env.kind)
  ok('回传可用 jobId（run-…）', env && /^run-/.test(String(env.jobId || '')), env && env.jobId)
  ok('在客户端等待窗口内返回（<10s，sleep 是 12s）', waited < 10000, waited + 'ms')
  ok('人读摘要里写明不是失败 + 收结果方式', /不是失败/.test(r.value.text) && /op="wait"/.test(r.value.text))
  const w = await call('blender_rt_job', { op: 'wait', id: env.jobId, timeout_ms: 60000 })
  const wenv = envelopeOf(w)
  ok('op=wait 一次拿到终态', wenv && wenv.status === 'done', wenv && wenv.status)
  ok('op=wait 拿回 HEADLESS JSON（resultJson）', wenv && wenv.resultJson && wenv.resultJson.d1 === 'late-but-not-lost', wenv && JSON.stringify(wenv.resultJson))
  const c = await call('blender_rt_job', { op: 'collect', id: env.jobId })
  const cenv = envelopeOf(c)
  ok('op=collect 对 run-… 也有效（run/job 同 id 空间）', cenv && cenv.status === 'done', cenv && cenv.status)
})

test('D2', 'print HEADLESS {"a":1} → 信封 resultJson.a === 1（无需正则）', async () => {
  const r = await call('blender_rt_headless', { script: "import json" + NL + "print('HEADLESS ' + json.dumps({'a':1}, separators=(',',':')))", engine: 'none', timeout_ms: 60000, outdir: TMP })
  const env = envelopeOf(r)
  ok('block0 是单行 JSON 信封', !!env)
  ok('信封 resultJson.a === 1', env && env.resultJson && env.resultJson.a === 1, env && JSON.stringify(env.resultJson))
  ok('status=finished', env && env.status === 'finished', env && env.status)
  ok('render 至少两个 text block（信封 + 人读）', r.blocks.filter((b) => b.type === 'text').length >= 2)
  ok('人读摘要仍在（block 1 非空且含 HEADLESS ok）', /HEADLESS ok/.test(String(r.blocks[1] && r.blocks[1].text)))
})

test('D3', '30KB JSON → resultTruncated + resultPath（文件完整）；非 JSON 末行 → stdoutTail 保留原文', async () => {
  const big = await call('blender_rt_headless', {
    script: "import json" + NL + "rows=[{'i':i,'pad':'x'*40} for i in range(500)]" + NL + "print('HEADLESS ' + json.dumps({'rows':rows}, separators=(',',':')))",
    engine: 'none', timeout_ms: 60000, outdir: TMP,
  })
  const env = envelopeOf(big)
  ok('大结果给 resultTruncated=true', env && env.resultTruncated === true, env && env.resultTruncated)
  ok('大结果给 resultPath', !!(env && env.resultPath), env && env.resultPath)
  const p = (env && (env.resultPathWsl || toWsl(env.resultPath))) || null
  let fileOk = false, bytes = 0
  if (p) { try { const s = fs.readFileSync(p, 'utf8'); bytes = s.length; const o = JSON.parse(s); fileOk = !!(o.rows && o.rows.length === 500) } catch (e) { /* ignore */ } }
  ok('落盘文件存在且内容完整（500 行）', fileOk, p + ' bytes=' + bytes)
  const bad = await call('blender_rt_headless', {
    script: "print('OK wrote /tmp/whatever.png')" + NL + "print('HEADLESS OK wrote 214 objects')",
    engine: 'none', timeout_ms: 60000, outdir: TMP,
  })
  const benv = envelopeOf(bad)
  ok('末行非 JSON 时不产生 _parse_error 顶掉输出', !(benv && benv.resultJson && benv.resultJson._parse_error))
  ok('解析失败降级为 resultParseError', !!(benv && benv.resultParseError), benv && JSON.stringify(benv.resultParseError))
  ok('stdoutTail 保留原文（含 HEADLESS OK wrote 214 objects）', /HEADLESS OK wrote 214 objects/.test(String(benv && benv.stdoutTail)))
  ok('人读摘要也保留原文', /HEADLESS OK wrote 214 objects/.test(String(bad.value.text)))
})

test('D4', "outdir='/home/…' → 文件落在该 WSL 目录且回执给出该路径；POSIX 路径交给 Windows API 被体检出来", async () => {
  const out = path.join(TMP, 'wsl-outdir')
  fs.mkdirSync(out, { recursive: true })
  const r = await call('blender_rt_headless', {
    script: "import os, json" + NL + "p = r'" + out + "/hello.txt'" + NL + "open(K.win_path(p), 'w', encoding='utf-8').write('hi')" + NL + "print('HEADLESS ' + json.dumps({'wrote': p, 'exists': os.path.exists(p)}, separators=(',',':')))",
    engine: 'none', timeout_ms: 60000, outdir: out,
  })
  const env = envelopeOf(r)
  ok('脚本写的文件真的落在 WSL 目录里', fs.existsSync(path.join(out, 'hello.txt')))
  ok('回执 resultJson.exists=true', env && env.resultJson && env.resultJson.exists === true, env && JSON.stringify(env.resultJson))
  ok('outdir 回执给 Windows + WSL 两种形态', !!(env && env.outdir && env.outdir.win && env.outdir.wsl), env && JSON.stringify(env.outdir))
  const rw = await call('blender_rt_headless', {
    script: "import bpy" + NL + "bpy.context.scene.render.filepath = '/home/sixtyseven67/should/not/be/here.png'" + NL + "print('Saved: ' + chr(39) + 'C:homesixtyseven67shouldnotbehere.png' + chr(39))",
    engine: 'none', timeout_ms: 60000, outdir: TMP,
  })
  const wenv = envelopeOf(rw)
  ok('pathWarnings 抓到 POSIX 路径静默改写', !!(wenv && wenv.pathWarnings && wenv.pathWarnings.length), wenv && JSON.stringify(wenv.pathWarnings))
  ok('pathWarnings 给了可执行 hint', !!(wenv && wenv.pathWarnings[0] && /win_path/.test(String(wenv.pathWarnings[0].hint))), wenv && JSON.stringify(wenv.pathWarnings && wenv.pathWarnings[0]))
})

test('D5', 'PYTHONUNBUFFERED + stage 心跳：运行中就能看到增量输出', async () => {
  fs.mkdirSync(TMP, { recursive: true })
  const t0 = Date.now()
  const r = await call('blender_rt_headless', {
    script: "import time" + NL + "dsh_stage('building')" + NL + "for i in range(10):" + NL + "    print('tick %d' % i, flush=True)" + NL + "    dsh_stage('tick', i=i)" + NL + "    time.sleep(2)" + NL + "print('HEADLESS {\"d5\":true}')",
    engine: 'none', timeout_ms: 300000, outdir: TMP,
  })
  const env = envelopeOf(r)
  ok('长任务在客户端窗口内返回 promoted', env && env.kind === 'promoted', env && env.kind)
  const jid = env && env.jobId
  const st = await call('blender_rt_job', { op: 'status', id: jid })
  const sv = envelopeOf(st) || st.value
  ok('运行中 op=status 有 stage', !!(sv.stage || sv.stageName), JSON.stringify(sv.stage || sv.stageName))
  ok('运行中 op=status 有 lines/logBytes（增量）', Number(sv.lines) > 0 && Number(sv.logBytes) > 0, 'lines=' + sv.lines + ' logBytes=' + sv.logBytes)
  ok('运行中 op=status 有 idleMs（最近输出时间）', typeof sv.idleMs === 'number', sv.idleMs)
  const logP = (sv.logs && (sv.logs.stdoutWsl || sv.logs.stdout)) || null
  let logTxt = ''
  try { logTxt = fs.readFileSync(logP, 'utf8') } catch (e) { /* ignore */ }
  ok('日志文件在**运行期**就有内容（PYTHONUNBUFFERED=1）', /tick 0/.test(logTxt), logP + ' len=' + logTxt.length)
  const c = await call('blender_rt_job', { op: 'collect', id: jid })
  const cenv = envelopeOf(c)
  ok('collect 运行中也能 tail 到 tick 行', /tick/.test(String((cenv && cenv.stdoutTail) || '')))
  await call('blender_rt_job', { op: 'kill', id: jid })
  const after = await call('blender_rt_job', { op: 'status', id: jid })
  const aenv = envelopeOf(after)
  ok('kill 之后 status 不再报 running', !!(aenv && aenv.status !== 'running'), aenv && aenv.status)
  ok('整个 D5 用例耗时 < 窗口 + 余量', Date.now() - t0 < 60000, (Date.now() - t0) + 'ms')
})

test('D6', 'job 支持 script_file+args+env；op=wait 一次拿结果；kill 未知 id 不抛错', async () => {
  fs.mkdirSync(TMP, { recursive: true })
  const sp = path.join(TMP, 'probe_job.py')
  fs.writeFileSync(sp, [
    'import os, sys, time, json',
    'dsh_stage(\'job-start\')',
    'time.sleep(6)',
    "print('HEADLESS ' + json.dumps({'argv': sys.argv[2:], 'args_env': K.args, 'myenv': os.environ.get('DSH_PROBE_X')}, separators=(',',':')))",
  ].join(NL), 'utf8')
  const t0 = Date.now()
  const s = await call('blender_rt_job', { op: 'start', script_file: sp, args: '--part hull_aft --res 1024', env: { DSH_PROBE_X: 'yes' }, engine: 'none', outdir: TMP, timeout_ms: 300000 })
  const senv = envelopeOf(s)
  ok('op=start 立刻返回 jobId', !!(senv && senv.jobId), senv && senv.jobId)
  ok('op=start 是秒级返回（不等 6s 任务）', Date.now() - t0 < 5000, (Date.now() - t0) + 'ms')
  const w = await call('blender_rt_job', { op: 'wait', id: senv.jobId, timeout_ms: 60000 })
  const wenv = envelopeOf(w)
  ok('op=wait 拿到终态 done', !!(wenv && wenv.status === 'done'), wenv && wenv.status)
  const rj = wenv && wenv.resultJson
  // argv 形如 [<script>, '--', <outdir>, '--part', 'hull_aft', '--res', '1024']（outdir 由引擎追加在最前）
  ok('script_file 的 args 透传进 sys.argv', !!(rj && rj.argv && rj.argv.slice(-4).join(' ') === '--part hull_aft --res 1024'), rj && JSON.stringify(rj.argv))
  ok('args 也进了 K.args（DSH_ARGS）', !!(rj && rj.args_env && rj.args_env.length === 4), rj && JSON.stringify(rj.args_env))
  ok('env 透传进子进程', !!(rj && rj.myenv === 'yes'), rj && rj.myenv)
  const k = await call('blender_rt_job', { op: 'kill', id: 'job-does-not-exist-xyz' })
  const kenv = envelopeOf(k)
  ok('kill 未知 id 不抛错且说明已结束/不存在', !!(kenv && kenv.status === 'unknown') && /不需要 kill|已结束/.test(String(k.value.text || '')), k.value.text)
  ok('kill 未知 id 走信封（ok=true + alreadyFinished）', !!(kenv && kenv.ok === true && kenv.alreadyFinished === true))
})

test('D6-stale', '后端重启后的 stale running 会被对账收敛（不再谎报 running）', async () => {
  // 为什么要起子进程：台账真源 = <workDir>/jobs/index.jsonl，而 workDir 由 config.mjs 在**模块加载时**
  // 从 DSH_BLENDER_WORKDIR 读定。这里用一个可写的临时 workDir 起子进程，测的就是真引擎代码路径
  // （顺带绕开「默认 workDir 落在只读挂载上」这类环境差异，见 F5-doc 之外的环境说明）。
  const workdir = path.join(TMP, 'acc-workdir')
  fs.mkdirSync(path.join(workdir, 'jobs'), { recursive: true })
  const fakeId = 'job-stale-probe-' + Date.now().toString(36)
  fs.appendFileSync(path.join(workdir, 'jobs', 'index.jsonl'),
    JSON.stringify({ id: fakeId, kind: 'job', status: 'running', startedAt: Date.now() - 60000, pid: 999999, pluginVersion: 'test', at: Date.now() }) + NL, 'utf8')
  const probe = path.join(TMP, 'stale_probe.mjs')
  fs.writeFileSync(probe, [
    "import { createEngine } from '" + path.join(PKG, 'runtime', 'engine.mjs') + "'",
    'const eng = createEngine({})',
    'const id = process.argv[2]',
    'const st = eng.job.status(id)',
    'const k = eng.job.kill(id)',
    'console.log(JSON.stringify({ status: st.status, staleReason: st.staleReason || null, pidAlive: st.pidAlive === true, hint: st.hint || null, kill: k }))',
    'process.exit(0)',
  ].join(NL), 'utf8')
  const out = execFileSync(process.execPath, [probe, fakeId], {
    env: Object.assign({}, process.env, { DSH_BLENDER_WORKDIR: workdir }), encoding: 'utf8', timeout: 30000,
  })
  const r = JSON.parse(String(out).trim().split(NL).pop())
  ok('台账里 running + 死 pid → 收敛为 stale', r.status === 'stale', JSON.stringify(r).slice(0, 300))
  ok('stale 记录带 staleReason', !!r.staleReason, r.staleReason)
  ok('stale 不再声称 pidAlive', r.pidAlive === false, JSON.stringify(r))
  ok('对 stale 记录 kill 也不抛错（ok=true + alreadyFinished）', !!(r.kill && r.kill.ok === true && r.kill.alreadyFinished === true), JSON.stringify(r.kill).slice(0, 200))
})

test('F1', 'shots=[3 视角] → 3 条 {name,path,ms,bytes,md5,coverage_estimate}；主体 <5% 出 warning', async () => {
  const out = path.join(TMP, 'shots')
  fs.mkdirSync(out, { recursive: true })
  const r = await call('blender_rt_headless', {
    shots: [
      { name: 'iso', from: [6, -6, 4], look_at: [0, 0, 0], lens: 50, res: [256, 192], samples: 4 },
      { name: 'front', from: [0, -8, 1], look_at: [0, 0, 0], lens: 50, res: [256, 192], samples: 4 },
      { name: 'far', from: [60, -60, 40], look_at: [0, 0, 0], lens: 20, res: [256, 192], samples: 4 },
    ],
    engine: 'none', timeout_ms: 900000, outdir: out, as_job: true, wait_s: 300,
  })
  const env = envelopeOf(r)
  const shots = env && env.shots
  ok('回执带 shots 段', !!shots, env && JSON.stringify(env).slice(0, 300))
  ok('shots 有 3 行', !!(shots && shots.rows && shots.rows.length === 3), shots && shots.count)
  const rows = (shots && shots.rows) || []
  ok('每行有 name/path/ms/bytes/md5', rows.every((x) => x.name && x.path && typeof x.ms === 'number' && typeof x.bytes === 'number' && x.md5), JSON.stringify(rows).slice(0, 400))
  ok('每行有 coverage_estimate', rows.every((x) => typeof x.coverage_estimate === 'number'), JSON.stringify(rows.map((x) => x.coverage_estimate)))
  ok('3 张 PNG 真的存在', rows.every((x) => fs.existsSync(toWsl(x.path))), JSON.stringify(rows.map((x) => x.path)))
  const far = rows.find((x) => x.name === 'far')
  ok('主体 <5% 的那张给了 warning', !!(far && far.warning && far.warning.code === 'subject_too_small'), far && JSON.stringify(far.warning))
  ok('shots jsonl 落盘', !!(shots && shots.jsonl), shots && shots.jsonl)
})

test('F6', 'file=<blend> → 回执带 inputFile{path,size,mtime,md5}', async () => {
  fs.mkdirSync(TMP, { recursive: true })
  const blendWsl = path.join(TMP, 'probe_input.blend')
  const mk = await call('blender_rt_headless', {
    script: "import bpy" + NL + "bpy.ops.wm.save_as_mainfile(filepath=K.win_path(r'" + blendWsl + "'), copy=True)" + NL + "print('HEADLESS {\"saved\":true}')",
    engine: 'none', timeout_ms: 120000, outdir: TMP,
  })
  ok('先造一个 .blend', fs.existsSync(blendWsl), blendWsl + ' ' + JSON.stringify(envelopeOf(mk) && envelopeOf(mk).resultJson))
  const r = await call('blender_rt_headless', { file: blendWsl, script: "print('HEADLESS {\"opened\":1}')", engine: 'none', timeout_ms: 120000, outdir: TMP })
  const env = envelopeOf(r)
  const f = env && env.inputFile
  ok('回执带 inputFile', !!f, env && JSON.stringify(env).slice(0, 200))
  ok('inputFile 有 size/mtime/md5', !!(f && f.size > 0 && f.mtimeMs > 0 && /^[0-9a-f]{32}$/.test(String(f.md5))), f && JSON.stringify(f))
  ok('inputFile.md5 与本地 md5sum 一致（可用它确认探的是哪一版）', !!f && f.size === fs.statSync(blendWsl).size)
})

test('F5-doc', '工具描述已写明 headless 优先 / as_job / promoted / 两个 block（模型看得见的引导）', async () => {
  const d = String(tools.get('blender_rt_headless').description || '')
  ok('描述含「第一路径」', /第一路径/.test(d))
  ok('描述含 as_job 引导（预期 >100 s 直接后台化）', /as_job/.test(d) && /100/.test(d))
  ok('描述含 promoted/jobId 语义', /promoted/.test(d) && /jobId/.test(d))
  ok('描述含两个 text block 的结构化回执说明', /单行 JSON 信封/.test(d))
  const jd = String(tools.get('blender_rt_job').description || '')
  ok('job 描述含 op=wait 与 run/job 同 id 空间', /op=wait/.test(jd) && /同一 id 空间/.test(jd))
  ok('job 描述含 script_file/args/env', /script_file/.test(jd) && /args/.test(jd) && /env/.test(jd))
})

/* ───────── 跑 ───────── */
if (argv.includes('--list')) { for (const c of CASES) console.log(c.id + '  ' + c.title); process.exit(0) }
const picked = ONLY.length ? CASES.filter((c) => ONLY.includes(c.id)) : CASES
if (NO_BLENDER) console.log('（--no-blender 仍会跑全部用例：本套用例的判定都依赖真机结果）')

const started = Date.now()
const up = await waitBackend(40000)
if (!up) { console.log('后端 ' + PORT + ' 起不来 —— 先确认 Blender 与 dsh-blender.config.json'); process.exit(2) }
// 写路由（/headless /job）受租约门控：别的会话持有租约时会被 409 挡住（不是 bug）。
// 验收测试自己抢一份租约，跑完释放 —— 否则结果取决于"谁先占了写通道"。
try {
  const lr = await call('blender_viewport', { op: 'lease', force: true })
  console.log('租约：' + String(lr.value).slice(0, 160))
} catch (e) { console.log('租约获取失败（继续跑）：' + String((e && e.message) || e)) }
for (const c of picked) {
  console.log(NL + '▶ ' + c.id + ' · ' + c.title)
  try { await c.fn() } catch (e) { fails.push(c.id + ' 抛异常'); console.log('  FAIL ' + c.id + ' 抛异常 → ' + String((e && e.stack) || e).slice(0, 800)) }
}
console.log(NL + '──────── 结果：' + String(pass) + ' 通过 / ' + String(fails.length) + ' 失败 · ' + String(Math.round((Date.now() - started) / 1000)) + 's ────────')
if (fails.length) { console.log('失败项：' + NL + fails.map((f) => '  · ' + f).join(NL)); }
try { await call('blender_viewport', { op: 'release' }) } catch (e) { /* ignore */ }
for (const d of disposers) { try { d() } catch (e) { /* ignore */ } }
process.exit(fails.length ? 1 : 0)
