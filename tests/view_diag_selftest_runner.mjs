#!/usr/bin/env node
/**
 * tests/view_diag_selftest_runner.mjs —— 跑 tests/view_diag_selftest.py（直接打后端 HTTP，不需要 DSH 工具层）
 *
 * 为什么要有它：view.py 的诊断原语（coverage / scene_bbox / objects_in_frame / frame_looks_empty）
 * 与 v0.9.4（P0-1）的**惰性化判据**（跑了诊断 ⟺ 显式要求 or 帧字节数 < 阈值）只在 Blender 里跑得出来，
 * node 单测覆盖不到。这个 runner 把"怎么跑"固化成 `npm run test:diag`。
 *
 * 租约：/headless 是写路由，会被别的会话的写租约挡住（409 leased）。本 runner 按
 *   `DSH_LEASE_HOLDER` → `GET /who` 上活着的 holder 的顺序找一个 holder 带上；
 *   找不到就如实报 409，**不 force 抢占**（抢了会打断正在用 Blender 的那个会话）。
 *
 * 前置：后端在 127.0.0.1:9877（blender_viewport op=start）、Blender 在跑。
 * 用法：node tests/view_diag_selftest_runner.mjs [port]
 */
const PORT = Number(process.argv[2] || process.env.DSH_BLENDER_PORT || 9877)
const BASE = 'http://127.0.0.1:' + String(PORT)

const script = [
  "_p = K.win_path(r'/home/sixtyseven67/DSH/dsh-blender-plugin/tests/view_diag_selftest.py')",
  "exec(compile(open(_p, encoding='utf-8').read(), 'view_diag_selftest.py', 'exec'), globals())",
].join('\n')

async function pickHolder() {
  if (process.env.DSH_LEASE_HOLDER) return process.env.DSH_LEASE_HOLDER
  try {
    const r = await fetch(BASE + '/who')
    const j = await r.json()
    const l = (j && j.lease) || {}
    if (l.active && l.holderAlive && l.holder) return String(l.holder)
  } catch (e) { /* 后端没起：下面 post 会给出可读错误 */ }
  return null
}

const holder = await pickHolder()
const payload = { preload: 'view', engine: 'none', factory_startup: true, script: script, timeout_ms: 180000 }
if (holder) payload.holder = holder

let body = null
try {
  const r = await fetch(BASE + '/headless', {
    method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(payload),
  })
  body = await r.json()
} catch (e) {
  console.error('拿不到后端（' + BASE + '）：' + String((e && e.message) || e))
  console.error('先跑 blender_viewport(op="start")，或 bash tests/engine_probe.sh 自检后端。')
  process.exit(2)
}

if (body && body.error === 'leased') {
  console.error('写通道被别的会话占用（holder=' + String(body.holder) + '，剩余 ' + String(Math.round(Number(body.expiresInMs || 0) / 1000)) + 's）。')
  console.error('不抢占：等它结束，或设 DSH_LEASE_HOLDER=<你的 holder> 再跑。')
  process.exit(3)
}

const res = body && body.result ? body.result : null
console.log(JSON.stringify({
  suite: 'view-diag-selftest', port: PORT, holder: holder || null,
  ok: !!(res && res.ok), passed: res && res.passed, failed: res && res.failed,
  failures: (res && res.failures) || [], status: body && body.status,
}))
if (!res || !res.ok) {
  if (body && body.text) console.error(String(body.text).slice(-3000))
  process.exit(1)
}
process.exit(0)
