#!/usr/bin/env node
/**
 * scripts/pyrun.mjs —— v0.9.6：用**可复现的顺序**找一个 python 解释器来跑 tests/*.py
 *
 * 为什么有它：本包有若干纯 python 自检（tests/generator_dispatch_selftest.py 等）不依赖 Blender，
 * 但 `python3` 这个名字不是所有平台都有（Windows 上通常是 `python`，有的机器只有 `py`）。
 * 直接写 `python3 xxx.py` 会得到 `python3: not found` —— 没有上下文、没有修法。
 *
 * 顺序（不装任何东西）：env DSH_PYTHON → python3 → python → py -3
 * 找不到就打印逐候选诊断 + 修法，退出码 2（与测试自身的失败区分开）。
 *
 * 用法：node scripts/pyrun.mjs tests/generator_dispatch_selftest.py [附加参数…]
 *      DSH_PYTHON=/usr/bin/python3.12 node scripts/pyrun.mjs tests/xxx.py
 */
import path from 'node:path'
import { spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const PKG = path.resolve(HERE, '..')
const args = process.argv.slice(2)

if (!args.length) {
  console.error('pyrun: 用法 node scripts/pyrun.mjs <脚本.py> [参数…]')
  process.exit(2)
}
const scriptFile = path.resolve(PKG, args[0])
const rest = args.slice(1)

const cands = []
if (process.env.DSH_PYTHON) cands.push({ cmd: process.env.DSH_PYTHON, pre: [], label: 'env DSH_PYTHON' })
cands.push({ cmd: 'python3', pre: [], label: 'python3' })
cands.push({ cmd: 'python', pre: [], label: 'python' })
cands.push({ cmd: 'py', pre: ['-3'], label: 'py -3（Windows 启动器）' })

const tried = []
for (const c of cands) {
  const probe = spawnSync(c.cmd, c.pre.concat(['-c', 'print(1)']), { encoding: 'utf8' })
  const ok = probe.status === 0 && String(probe.stdout).trim() === '1'
  tried.push({ label: c.label, cmd: c.cmd, ok: ok, why: ok ? '可用' : (probe.error ? String(probe.error.message).slice(0, 80) : '退出码 ' + String(probe.status)) })
  if (!ok) continue
  const r = spawnSync(c.cmd, c.pre.concat([scriptFile]).concat(rest), { stdio: 'inherit', cwd: PKG })
  if (r.error) { console.error('pyrun: 执行失败：' + String(r.error.message || r.error)); process.exit(2) }
  process.exit(typeof r.status === 'number' ? r.status : 1)
}

console.error('pyrun: 找不到 python 解释器（不安装任何东西）')
for (const t of tried) console.error('  ✗ ' + t.label + ' → ' + t.why)
console.error('修法：DSH_PYTHON=<python 绝对路径> node scripts/pyrun.mjs ' + args[0])
process.exit(2)
