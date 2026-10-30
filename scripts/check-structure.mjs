#!/usr/bin/env node
/**
 * scripts/check-structure.mjs —— B1 入口与文件地图门禁（T4/T5）。
 *
 * 断言：
 *   ① src/index.ts ≤ 150 行，且只含 import / export（可选 apply(...) 调用）——不得有业务逻辑；
 *   ② 15 个 tool handler 全部下沉：14 个在 src/tools/*.ts，plan 在 src/plan/tool.ts，
 *      名称集合与 tests/fixtures/capability.lock.json 一致；
 *   ③ §4.2 / §4.3 目标文件全部存在；
 *   ④ src 内部相对 import 图无环（.js 说明符映射回 .ts）。
 *
 * 用法：
 *   node scripts/check-structure.mjs                 # 人读摘要，失败退出 1
 *   node scripts/check-structure.mjs --json          # 只打 JSON
 *   node scripts/check-structure.mjs --out <path>    # 写 structure-report.json（同时打摘要）
 */
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const ROOT = path.resolve(HERE, '..')
const argv = process.argv.slice(2)
const jsonOnly = argv.includes('--json')
const outIdx = argv.indexOf('--out')
const outPath = outIdx >= 0 ? argv[outIdx + 1] : null
if (outIdx >= 0 && (!outPath || outPath.startsWith('--'))) {
  console.error('check-structure: --out 需要一个路径参数')
  process.exit(2)
}

const failures = []
const rel = (p) => path.relative(ROOT, p).split(path.sep).join('/')

// ---------- ① 入口文件 ----------
const INDEX = path.join(ROOT, 'src', 'index.ts')
let indexReport = { path: rel(INDEX), lines: 0, entryOnly: false, violations: [] }
if (!fs.existsSync(INDEX)) {
  failures.push('src/index.ts 不存在')
} else {
  const text = fs.readFileSync(INDEX, 'utf8')
  const lines = text.split(/\r?\n/)
  indexReport.lines = lines.length
  if (lines.length > 150) failures.push('src/index.ts 行数 ' + lines.length + ' > 150')
  let inBlockComment = false
  lines.forEach((raw, i) => {
    const line = raw.trim()
    if (!line) return
    if (inBlockComment) {
      if (line.includes('*/')) inBlockComment = false
      return
    }
    if (line.startsWith('//')) return
    if (line.startsWith('/*')) {
      if (!line.includes('*/')) inBlockComment = true
      return
    }
    if (/^(import|export)\b/.test(line)) return
    if (/^apply\s*\(/.test(line)) return
    indexReport.violations.push({ line: i + 1, text: line.slice(0, 120) })
  })
  indexReport.entryOnly = indexReport.violations.length === 0
  for (const v of indexReport.violations) {
    failures.push('src/index.ts:' + v.line + ' 含非入口语句: ' + v.text)
  }
}

// ---------- ② 15 个 tool handler ----------
const TOOLS_DIR = path.join(ROOT, 'src', 'tools')
const toolDecls = new Map() // name -> [files]
function scanToolFile(file) {
  if (!fs.existsSync(file)) return
  const text = fs.readFileSync(file, 'utf8')
  for (const m of text.matchAll(/name:\s*'(blender_[a-z_]+)'/g)) {
    const name = m[1]
    if (!toolDecls.has(name)) toolDecls.set(name, [])
    const list = toolDecls.get(name)
    if (!list.includes(rel(file))) list.push(rel(file))
  }
}
const toolFiles = fs.existsSync(TOOLS_DIR)
  ? fs
      .readdirSync(TOOLS_DIR)
      .filter((f) => f.endsWith('.ts'))
      .sort()
  : []
for (const f of toolFiles) scanToolFile(path.join(TOOLS_DIR, f))
scanToolFile(path.join(ROOT, 'src', 'plan', 'tool.ts'))

const LOCK = path.join(ROOT, 'tests', 'fixtures', 'capability.lock.json')
let expected = null
if (fs.existsSync(LOCK)) {
  try {
    expected = JSON.parse(fs.readFileSync(LOCK, 'utf8')).tools || null
  } catch {
    expected = null
  }
}
const names = [...toolDecls.keys()].sort()
const toolReport = { count: names.length, names, files: Object.fromEntries([...toolDecls].map(([k, v]) => [k, v])) }
if (names.length !== 15) failures.push('tool handler 数 ' + names.length + ' != 15')
if (expected) {
  const exp = [...new Set(expected)].sort()
  for (const n of exp) if (!toolDecls.has(n)) failures.push('缺少 tool handler: ' + n)
  for (const n of names) if (!exp.includes(n)) failures.push('多出 tool handler: ' + n)
} else {
  failures.push('tests/fixtures/capability.lock.json 不存在或没有 tools 列表，无法对拍 15 tool')
}
for (const [name, files] of toolDecls) {
  const inTools = files.some((f) => f.startsWith('src/tools/'))
  const inPlan = files.includes('src/plan/tool.ts')
  if (!inTools && !inPlan) failures.push('tool ' + name + ' 未落在 src/tools/*.ts 或 src/plan/tool.ts: ' + files.join(','))
}
if (!fs.existsSync(path.join(TOOLS_DIR, 'index.ts'))) failures.push('src/tools/index.ts（注册汇总）不存在')

// ---------- ③ 目标文件地图 ----------
const TARGET_FILES = [
  'src/index.ts',
  'src/constants.ts',
  'src/host/exec-context.ts',
  'src/contracts/lossless.ts',
  'src/contracts/tool.ts',
  'src/host/backend.ts',
  'src/host/http.ts',
  'src/utils/frame-cache.ts',
  'src/utils/image.ts',
  'src/plan/headless-target.ts',
  'src/ui/cards.ts',
  'src/headless/receipts.ts',
  'src/tools/schema-diagnostic.ts',
  'src/plugin.ts',
  'src/host/skill.ts',
  'src/plan/tool.ts',
  'src/tools/index.ts',
  'src/tui-host.ts',
  'src/tools/viewport.ts',
  'src/tools/see.ts',
  'src/tools/do.ts',
  'src/tools/watch.ts',
  'src/tools/cmd.ts',
  'src/tools/commands.ts',
  'src/tools/loop.ts',
  'src/tools/perf.ts',
  'src/tools/opt.ts',
  'src/tools/headless.ts',
  'src/tools/worker.ts',
  'src/tools/txn.ts',
  'src/tools/preset.ts',
  'src/tools/job.ts'
]
const missing = TARGET_FILES.filter((f) => !fs.existsSync(path.join(ROOT, f)))
for (const f of missing) failures.push('目标文件缺失: ' + f)
const extraTools = toolFiles.filter((f) => !TARGET_FILES.includes('src/tools/' + f))
if (extraTools.length) failures.push('src/tools 下有意料外的文件: ' + extraTools.join(', '))

// ---------- ④ import 图无环 ----------
const SRC = path.join(ROOT, 'src')
const graph = new Map()
function collect(dir) {
  for (const ent of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, ent.name)
    if (ent.isDirectory()) collect(p)
    else if (ent.name.endsWith('.ts') && !ent.name.endsWith('.d.ts')) graph.set(p, importsOf(p))
  }
}
function resolveSpec(fromFile, spec) {
  if (!spec.startsWith('.')) return null
  const base = path.resolve(path.dirname(fromFile), spec)
  const noExt = base.replace(/\.(js|mjs|cjs)$/, '')
  for (const cand of [base, noExt + '.ts', noExt + '.d.ts', path.join(noExt, 'index.ts')]) {
    if (fs.existsSync(cand) && fs.statSync(cand).isFile()) return cand
  }
  return null
}
function importsOf(file) {
  const text = fs.readFileSync(file, 'utf8')
  const out = new Set()
  const re = /(?:^|\n)\s*(?:import|export)\s+(?:[^'"]*?\sfrom\s+)?['"]([^'"]+)['"]/g
  for (const m of text.matchAll(re)) {
    const target = resolveSpec(file, m[1])
    if (target) out.add(target)
  }
  // 动态 import('./x.js') 也纳入图
  for (const m of text.matchAll(/import\(\s*['"]([^'"]+)['"]\s*\)/g)) {
    const target = resolveSpec(file, m[1])
    if (target) out.add(target)
  }
  return out
}
if (fs.existsSync(SRC)) collect(SRC)
const cycles = []
const state = new Map() // 0=未访问 1=栈上 2=完成
const stack = []
function dfs(node) {
  const s = state.get(node) || 0
  if (s === 2) return
  if (s === 1) {
    const at = stack.indexOf(node)
    cycles.push(stack.slice(at).concat(node).map(rel))
    return
  }
  state.set(node, 1)
  stack.push(node)
  for (const next of graph.get(node) || []) dfs(next)
  stack.pop()
  state.set(node, 2)
}
for (const node of graph.keys()) dfs(node)
const edges = [...graph].reduce((n, [, set]) => n + set.size, 0)
for (const c of cycles) failures.push('import 环: ' + c.join(' -> '))

const report = {
  ok: failures.length === 0,
  tool: 'check-structure',
  generatedAt: new Date().toISOString(),
  node: process.version,
  index: indexReport,
  tools: toolReport,
  targetFiles: { required: TARGET_FILES.length, missing },
  importGraph: { files: graph.size, edges, cycles },
  failures
}
const text = JSON.stringify(report, null, 2) + '\n'
if (outPath) {
  const abs = path.resolve(ROOT, outPath)
  fs.mkdirSync(path.dirname(abs), { recursive: true })
  fs.writeFileSync(abs, text, 'utf8')
}
if (jsonOnly) process.stdout.write(text)
else {
  console.log('check-structure: ' + (report.ok ? 'PASS' : 'FAIL'))
  console.log('  index: ' + indexReport.lines + ' 行，entry-only=' + indexReport.entryOnly)
  console.log('  tools: ' + names.length + ' 个 handler（' + Object.keys(toolReport.files).length + ' 个名字）')
  console.log('  target files: ' + (TARGET_FILES.length - missing.length) + '/' + TARGET_FILES.length)
  console.log('  import graph: ' + graph.size + ' 文件 / ' + edges + ' 边 / ' + cycles.length + ' 环')
  for (const f of failures) console.log('  x ' + f)
  if (outPath) console.log('  报告: ' + outPath)
}
process.exit(failures.length ? 1 : 0)
