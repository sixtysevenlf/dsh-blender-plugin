#!/usr/bin/env node
/**
 * scripts/typecheck.mjs —— v0.9.6：把 `npm run typecheck` 变成**可复现**的一步
 *
 * 为什么要有它
 * ---------------------------------------------------------------------------
 * 旧脚本是 `tsc -p tsconfig.json --noEmit`。本包 **不把 typescript 打进依赖**（devDependencies 里声明了，
 * 但 node_modules 里没有），所以这台机器上 `npm run typecheck` 只会回一句
 *   sh: tsc: not found   （或 Windows 上的 'tsc' 不是内部或外部命令）
 * 既没说"去哪儿找"，也没说"为什么没有"，换台机器/换个人就只能猜。
 *
 * 本脚本的规矩（有意写死在这里，别再退化）
 *   · **不全局装**：不做 `npm i -g`、不做 `npx` 自动下载；只用已经存在的 tsc。
 *   · **不写死宿主绝对路径**：候选路径全部相对 $HOME / 本包 / 环境变量推导。
 *   · 找不到时给**逐候选诊断**（每个候选分别"路径不存在 / 有 packages 但没装依赖 / 未设置"）+
 *     四条不需要全局安装的修法，退出码 2（与 tsc 自己的编译失败区分开）。
 *
 * 用法
 *   node scripts/typecheck.mjs                 # 等价 npm run typecheck
 *   node scripts/typecheck.mjs --print         # 只打印解析到的 tsc 路径（给 scripts/build.sh 用）
 *   node scripts/typecheck.mjs --pretty false  # 其余参数原样转发给 tsc
 *   DSH_TSC=/path/to/tsc node scripts/typecheck.mjs
 *   DSH_CHECKOUT=/path/to/dsh-checkout npm run typecheck
 */
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const PKG = path.resolve(HERE, '..')
const TSCONFIG = path.join(PKG, 'tsconfig.json')
const EXE = process.platform === 'win32' ? '.cmd' : ''

const argv = process.argv.slice(2)
const printOnly = argv.indexOf('--print') >= 0
// 去掉 `--print`；顺便吃掉一个前导 `--`（`npm run typecheck -- --pretty false` 里 npm 会去掉它，
// 但直接 `node scripts/typecheck.mjs -- --pretty false` 时会原样传进来 → tsc 会报 Unknown compiler option '--'）
const rest = argv.filter((a) => a !== '--print')
const tscArgs = (rest[0] === '--') ? rest.slice(1) : rest

/** .bin 里的入口：POSIX 是 `tsc`，Windows 是 `tsc.cmd`（.cmd 也要能被 spawnSync 执行） */
function binTsc(dir) {
  if (!dir) return null
  for (const name of ['tsc' + EXE, 'tsc']) {
    const p = path.join(dir, name)
    try { if (fs.existsSync(p)) return p } catch (e) { /* ignore */ }
  }
  return null
}

/** 检查一个"dsh checkout"：它得同时有 packages/（源码树）和 node_modules/.bin/tsc（编译工具） */
function probeCheckout(dir) {
  if (!dir) return { dir: null, state: 'unset', tsc: null, why: '未设置' }
  const abs = path.resolve(String(dir).replace(/^~(?=$|[\\/])/, os.homedir()))
  let hasPackages = false
  try { hasPackages = fs.existsSync(path.join(abs, 'packages')) } catch (e) { /* ignore */ }
  if (!fs.existsSync(abs)) return { dir: abs, state: 'missing', tsc: null, why: '目录不存在' }
  const tsc = binTsc(path.join(abs, 'node_modules', '.bin'))
  if (tsc) return { dir: abs, state: 'ok', tsc: tsc, why: '有 packages/ 且有 node_modules/.bin/tsc' }
  if (hasPackages) return { dir: abs, state: 'no-deps', tsc: null, why: '有 packages/，但 node_modules/.bin/tsc 不存在（依赖没装）' }
  return { dir: abs, state: 'not-checkout', tsc: null, why: '不是 dsh checkout（没有 packages/）' }
}

const CANDIDATE_CHECKOUTS = [
  process.env.DSH_CHECKOUT,
  path.join(os.homedir(), 'dsh-harness'),
  path.join(os.homedir(), 'dsh'),
  path.join(os.homedir(), '.dsh', 'dsh-harness'),
].filter(Boolean)

const steps = []
let resolved = null

// ① env DSH_TSC（明确指定优先）
if (process.env.DSH_TSC) {
  const p = process.env.DSH_TSC
  const ok = (() => { try { return fs.existsSync(p) } catch (e) { return false } })()
  steps.push({ name: '① env DSH_TSC', detail: p + (ok ? '（存在）' : '（**不存在**）'), ok: ok })
  if (ok) resolved = { tsc: p, from: 'DSH_TSC' }
} else {
  steps.push({ name: '① env DSH_TSC', detail: '未设置', ok: false })
}

// ② 本包 node_modules/.bin
if (!resolved) {
  const local = binTsc(path.join(PKG, 'node_modules', '.bin'))
  steps.push({ name: '② 本包 node_modules/.bin/tsc', detail: local || (path.join(PKG, 'node_modules', '.bin') + ' 下没有 tsc'), ok: !!local })
  if (local) resolved = { tsc: local, from: 'local' }
}

// ③ 本包 node_modules/typescript/bin/tsc（npm install 之后可能没有 .bin 软链）
if (!resolved) {
  const t = path.join(PKG, 'node_modules', 'typescript', 'bin', 'tsc')
  const ok = (() => { try { return fs.existsSync(t) } catch (e) { return false } })()
  steps.push({ name: '③ 本包 node_modules/typescript/bin/tsc', detail: ok ? t : t + ' 不存在', ok: ok })
  if (ok) resolved = { tsc: t, from: 'local-typescript' }
}

// ④ 自动探测的 dsh checkout（DSH_CHECKOUT + $HOME 下三个常见位置，全部相对 home，不写死宿主路径）
const probes = CANDIDATE_CHECKOUTS.map(probeCheckout)
for (let i = 0; i < probes.length; i++) {
  const pr = probes[i]
  const label = (i === 0 && process.env.DSH_CHECKOUT) ? '④ DSH_CHECKOUT' : '④ 自动探测 checkout[' + String(i) + ']'
  steps.push({ name: label, detail: (pr.dir || '(空)') + ' → ' + pr.why, ok: pr.state === 'ok' })
  if (!resolved && pr.state === 'ok') resolved = { tsc: pr.tsc, from: 'checkout:' + pr.dir }
}

// ⑤ PATH 上的 tsc（只查，不装）
if (!resolved) {
  const which = spawnSync(process.platform === 'win32' ? 'where.exe' : 'which', ['tsc'], { encoding: 'utf8' })
  const hit = (which.status === 0 ? String(which.stdout).split(/\r?\n/).filter(Boolean)[0] : null)
  steps.push({ name: '⑤ PATH 上的 tsc', detail: hit || '找不到（不自动安装）', ok: !!hit })
  if (hit) resolved = { tsc: hit.trim(), from: 'PATH' }
}

function diagnosis() {
  const L = []
  L.push('typecheck: 找不到 TypeScript 编译器（tsc）。')
  L.push('本脚本**不会**全局安装依赖，也不做 npx 自动下载 —— 只按固定顺序找现成的 tsc。逐候选结果：')
  for (const s of steps) L.push('  ' + (s.ok ? '✓' : '✗') + ' ' + s.name + ' → ' + s.detail)
  const noDeps = probes.filter((p) => p.state === 'no-deps')
  L.push('')
  L.push('修法（任选一条，都不需要全局安装）：')
  if (noDeps.length) L.push('  A. checkout 找到了但依赖没装：cd ' + noDeps[0].dir + ' && pnpm install   （然后重跑 npm run typecheck）')
  else L.push('  A. 指定一个有依赖的 checkout：DSH_CHECKOUT=<checkout 路径> npm run typecheck')
  L.push('  B. 直接指定编译器：DSH_TSC=<某个 tsc 的绝对路径> npm run typecheck')
  L.push('  C. 只要本地类型检查（不动全局）：在本包目录跑 npm install --no-save typescript')
  L.push('  D. 完全不装：跳过 npm run typecheck，改用仓库自带测试（node tests/*.mjs，不依赖 tsc）')
  L.push('')
  L.push('本包 tsconfig：' + TSCONFIG)
  return L.join('\n')
}

if (!resolved) {
  console.error(diagnosis())
  process.exit(2)
}

if (printOnly) { process.stdout.write(resolved.tsc + '\n'); process.exit(0) }

if (!fs.existsSync(TSCONFIG)) {
  console.error('typecheck: 找不到 tsconfig：' + TSCONFIG)
  process.exit(2)
}

// ⚠ 永远带 `--noEmit`：本脚本是"类型检查"，不是"构建"。漏了它，
// 只要调用方传了任何参数（如 `-- pretty false`），tsc 就会按 cwd 找 tsconfig 并**写 lib/**（目录被覆盖）。
const hasProject = tscArgs.some((a) => a === '-p' || a === '--project' || String(a).indexOf('--project=') === 0)
const base = hasProject ? ['--noEmit'] : ['-p', TSCONFIG, '--noEmit']
console.error('typecheck: 用 ' + resolved.tsc + '（来源 ' + resolved.from + '）')
const r = spawnSync(resolved.tsc, base.concat(tscArgs), { stdio: 'inherit' })
if (r.error) {
  console.error('typecheck: 执行失败：' + String(r.error.message || r.error))
  process.exit(2)
}
process.exit(typeof r.status === 'number' ? r.status : 1)
