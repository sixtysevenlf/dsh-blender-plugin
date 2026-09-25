#!/usr/bin/env node
/**
 * tests/lease_stale_selftest.mjs —— v0.9.4：死会话的写租约必须能被自动回收
 *
 * 实测踩到（2026-09-25 本机）：一个 DSH 会话崩掉/被关掉之后，它的写租约还在后端内存里
 * 继续生效到 TTL（默认 1 h），把别的会话的写通道整段挡死 —— 而那个会话早就不存在了。
 * 本测试用**独立后端 + 独立工作目录**验证：
 *   A 活着的持有者照样挡（不能误抢）
 *   B 持有者进程不存在 → 下一个写请求自动回收并放行
 *   C 非本机约定命名的 holder（plugin-pid-<pid> 之外）→ 判不了存活 → 仍然挡（保守）
 *   D 真实写路由（/headless）在"死持有者"之后不再 409
 *
 * 用法：node tests/lease_stale_selftest.mjs
 */
import { spawn } from 'node:child_process'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const PKG = path.join(HERE, '..')
const PORT = Number(process.env.DSH_SELFTEST_LEASE_PORT || 9896)
const TMP = fs.mkdtempSync(path.join(os.tmpdir(), 'dsh-lease-selftest-'))
const ME = 'plugin-pid-' + String(process.pid)

let pass = 0, fail = 0
const failures = []
function ok(name, cond, detail) {
  if (cond) { pass++; console.log('  ✓ ' + name) }
  else { fail++; failures.push(name + (detail ? (' — ' + detail) : '')); console.log('  ✗ ' + name + (detail ? (' — ' + detail) : '')) }
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
async function post(p, body, timeoutMs) {
  const ctl = new AbortController()
  const t = setTimeout(() => ctl.abort(), timeoutMs || 120000)
  try {
    const r = await fetch('http://127.0.0.1:' + String(p) + '/', { signal: ctl.signal })
    return await r.json()
  } finally { clearTimeout(t) }
}
async function rpc(p, route, body, timeoutMs) {
  const ctl = new AbortController()
  const t = setTimeout(() => ctl.abort(), timeoutMs || 120000)
  try {
    const r = await fetch('http://127.0.0.1:' + String(p) + route, {
      method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body || {}), signal: ctl.signal,
    })
    return { status: r.status, body: await r.json() }
  } finally { clearTimeout(t) }
}
async function getJson(p, route) {
  try { return await (await fetch('http://127.0.0.1:' + String(p) + route)).json() } catch (e) { return null }
}

console.log('== 死会话租约回收自检 ==')
console.log('隔离后端：http://127.0.0.1:' + String(PORT) + ' · 工作目录 ' + TMP)
const backend = spawn(process.execPath, [path.join(PKG, 'runtime', 'server.mjs'), '--port', String(PORT)], {
  env: Object.assign({}, process.env, { DSH_BLENDER_WORKDIR: TMP, DSH_BLENDER_HTTP_PORT: String(PORT) }),
  stdio: ['ignore', 'pipe', 'pipe'],
})
let log = ''
backend.stdout.on('data', (d) => { log += d.toString('utf8') })
backend.stderr.on('data', (d) => { log += d.toString('utf8') })
const cleanup = () => { try { backend.kill('SIGKILL') } catch (e) {} }
process.on('exit', cleanup)

let up = false
for (let i = 0; i < 40; i++) { if (await getJson(PORT, '/health')) { up = true; break } await sleep(300) }
ok('隔离后端已就绪', up, log.slice(-300))
if (!up) { console.log('后端起不来，终止'); process.exit(1) }

// 造一个"已经死掉的 pid"：起一个进程等它退出
const deadPid = await new Promise((resolve) => {
  const c = spawn(process.execPath, ['-e', 'process.exit(0)'], { stdio: 'ignore' })
  const pid = c.pid
  c.on('exit', () => setTimeout(() => resolve(pid), 200))
})
const DEAD = 'plugin-pid-' + String(deadPid)
let alive = true
try { process.kill(deadPid, 0) } catch (e) { alive = false }
ok('构造出的 holder pid 确实已不存在', alive === false, 'pid=' + String(deadPid))

// ── A：活着的持有者必须照样挡（不能误抢）
const a1 = await rpc(PORT, '/lease', { holder: ME })
ok('A1 本进程（活）拿到租约', a1.status === 200 && a1.body.ok === true, JSON.stringify(a1.body).slice(0, 200))
const a2 = await rpc(PORT, '/lease', { holder: 'plugin-pid-' + String(process.ppid || 1) })
ok('A2 另一个活着的人来抢 → 被挡（409 denied）', a2.status === 409 && a2.body.error === 'leased', JSON.stringify(a2.body).slice(0, 200))
const a3 = await getJson(PORT, '/who')
ok('A3 /who 报告 holderAlive=true（能判活）', !!a3 && a3.lease && a3.lease.holderAlive === true, JSON.stringify(a3 && a3.lease))

// ── B：持有者进程不存在 → 下一个写请求自动回收
const b1 = await rpc(PORT, '/lease', { holder: DEAD, force: true })
ok('B1 死 pid 强制拿到租约（构造现场）', b1.status === 200 && b1.body.ok === true, JSON.stringify(b1.body).slice(0, 200))
const b2 = await getJson(PORT, '/who')
ok('B2 /who 报告 holderAlive=false（判死）', !!b2 && b2.lease && b2.lease.holderAlive === false, JSON.stringify(b2 && b2.lease))
const b3 = await rpc(PORT, '/lease', { holder: ME })     // 不带 force！
ok('B3 活会话不带 force 也能拿到（死租约被自动回收）', b3.status === 200 && b3.body.ok === true, JSON.stringify(b3.body).slice(0, 240))
const b4 = await getJson(PORT, '/health')
ok('B4 统计里记了 staleLeasesReclaimed', !!b4 && b4.stats && Number(b4.stats.staleLeasesReclaimed) >= 1, JSON.stringify(b4 && b4.stats && { n: b4.stats.staleLeasesReclaimed, note: b4.stats.lastLeaseNote }))
ok('B5 回收事件有可读说明（不静默）', !!b4 && typeof b4.stats.lastLeaseNote === 'string' && b4.stats.lastLeaseNote.indexOf('回收') >= 0, String(b4 && b4.stats && b4.stats.lastLeaseNote))

// ── C：非约定命名的 holder（判不了存活）→ 保守地继续挡
const c1 = await rpc(PORT, '/lease', { holder: 'orca-model-session', force: true })
ok('C1 换成非约定 holder 拿到租约', c1.status === 200, JSON.stringify(c1.body).slice(0, 160))
const c2 = await getJson(PORT, '/who')
ok('C2 holderAlive=null（判不了存活）', !!c2 && c2.lease && c2.lease.holderAlive === null, JSON.stringify(c2 && c2.lease))
const c3 = await rpc(PORT, '/lease', { holder: ME })
ok('C3 判不了存活 → 仍然挡（保守，不误抢）', c3.status === 409 && c3.body.error === 'leased', JSON.stringify(c3.body).slice(0, 200))

// ── D：真实写路由：死持有者之后不再 409
const d0 = await rpc(PORT, '/lease', { holder: DEAD, force: true })
ok('D0 现场重置为死持有者', d0.status === 200, JSON.stringify(d0.body).slice(0, 160))
const d1 = await rpc(PORT, '/headless', {
  holder: ME, engine: 'none', timeoutMs: 60000,
  script: 'import json' + String.fromCharCode(10) + 'print("HEADLESS " + json.dumps({"ok": True, "phase": "lease-selftest"}))',
}, 90000)
const leased = d1.body && (d1.body.error === 'leased' || (d1.body.result && d1.body.result.error === 'leased'))
ok('D1 写路由没有被死租约挡住（不再 409 leased）', !leased, JSON.stringify(d1.body).slice(0, 260))
ok('D2 写路由真的跑出了结果', !!(d1.body && d1.body.result && d1.body.result.resultJson && d1.body.result.resultJson.ok === true),
  JSON.stringify(d1.body && d1.body.result && d1.body.result.resultJson))

console.log('')
console.log('─'.repeat(64))
console.log('lease-stale-selftest：' + pass + ' 通过 / ' + fail + ' 失败')
if (fail) { for (const f of failures) console.log('  - ' + f); cleanup(); process.exit(1) }
console.log('OK：活持有者照样挡，死持有者自动回收，写通道不会被幽灵租约锁死。')
cleanup()
process.exit(0)
