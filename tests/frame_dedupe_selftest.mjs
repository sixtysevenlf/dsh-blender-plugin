#!/usr/bin/env node
/**
 * tests/frame_dedupe_selftest.mjs —— v0.9.4（P0-2）**帧去重纯逻辑**回归
 *
 * 为什么单独钉它：P0-2 省的是"重复帧的附件"（实测同一画面连发 5 次 hash 全等、单帧 116,870 B
 * + 模型侧视觉 token），代价是"agent 可能以为看过图"。所以判据必须机械可验：
 *   · **同一个键 + 同一个 hash** 才判重；force 一律放行；不同通道/不同视角绝不互相压；
 *   · 空 hash 不判重（拿不到 hash 就当新帧，宁可多发一张，也不许静默丢图）。
 * 用法：node tests/frame_dedupe_selftest.mjs      （不需要 Blender / 后端）
 */
process.env.DSH_HEADLESS_WAIT_MS = process.env.DSH_HEADLESS_WAIT_MS || '50'
const { __internals } = await import('../lib/index.js')
const { frameDedupe, resetFrameCache } = __internals

let pass = 0
const failures = []
function ok(cond, name, detail) {
  if (cond) { pass += 1; return }
  failures.push(name + (detail === undefined ? '' : ' :: ' + JSON.stringify(detail)))
}
function eq(a, b, name) { ok(JSON.stringify(a) === JSON.stringify(b), name, { got: a, want: b }) }

if (typeof frameDedupe !== 'function' || typeof resetFrameCache !== 'function') {
  console.log(JSON.stringify({ suite: 'frame-dedupe', ok: false, error: '__internals 没暴露 frameDedupe/resetFrameCache' }, null, 1))
  process.exit(1)
}

resetFrameCache()

// 1) 首次出现 ⇒ 不是重复
const a1 = frameDedupe('viewport:560', 'h1')
eq(a1.dup, false, '首次：dup=false')
eq(a1.repeats, 0, '首次：repeats=0')

// 2) 同键同 hash ⇒ 判重并计数
const a2 = frameDedupe('viewport:560', 'h1')
const a3 = frameDedupe('viewport:560', 'h1')
eq(a2.dup, true, '同键同 hash：dup=true')
eq(a2.repeats, 1, '第二次：repeats=1')
eq(a3.repeats, 2, '第三次：repeats=2')

// 3) force ⇒ 放行且把计数清零（下一次同 hash 从 1 重新数）
const a4 = frameDedupe('viewport:560', 'h1', true)
eq(a4.dup, false, 'force：dup=false（强制重发）')
eq(a4.repeats, 0, 'force：计数重置')
eq(frameDedupe('viewport:560', 'h1').repeats, 1, 'force 之后重新从 1 计数')

// 4) 同 hash 但不同通道 ⇒ 不互相压
eq(frameDedupe('do-see:560', 'h1').dup, false, '不同通道（do-see）不被 viewport 压')
eq(frameDedupe('view:[996,560,[7,-7,5],[0,0,1]]', 'h1').dup, false, '自定义视角通道独立')
eq(frameDedupe('area:0', 'h1').dup, false, '区域截图通道独立')

// 5) 同通道但 hash 变了（画面变了）⇒ 正常发图
eq(frameDedupe('viewport:560', 'h2').dup, false, 'hash 变化：正常发图')

// 6) 空 hash ⇒ 绝不判重（宁多发一张，不静默丢图）
eq(frameDedupe('viewport:420', '').dup, false, '空 hash 第一次')
eq(frameDedupe('viewport:420', '').dup, false, '空 hash 永远不判重')

// 7) 键很多也不炸（内部有上限），且最近的键照常工作
for (let i = 0; i < 60; i += 1) frameDedupe('k' + i, 'same')
eq(frameDedupe('k59', 'same').dup, true, '键很多时最近的键照常判重')

const total = pass + failures.length
console.log(JSON.stringify({
  suite: 'frame-dedupe', phase: 'P0-2', total, pass, fail: failures.length, failures,
  note: '只测纯逻辑：同键同 hash 才判重；force 放行；通道隔离；空 hash 不判重。真机附件是否真的没发由 acceptance/live 用例覆盖。',
}, null, 2))
process.exit(failures.length === 0 ? 0 : 1)
