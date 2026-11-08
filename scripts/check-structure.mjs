#!/usr/bin/env node
// @ts-check
/**
 * scripts/check-structure.mjs —— 入口与文件地图门禁。
 *
 * 断言：
 *   ① src/index.ts ≤ 150 行，且只含 import / export（可选 apply(...) 调用）——不得有业务逻辑；
 *   ② 6 个模型 tool handler 全部下沉到 src/tools/<tool>/index.ts（3.1 六工具面），
 *      名称集合与 tests/fixtures/capability.lock.json 一致；
 *   ③ 架构文件全部存在；
 *   ④ src 内部相对 import 图无环（.js 说明符映射回 .ts）；
 *   ⑤ 生产源码单文件 ≤600 行（例外登记在 config/exceptions.json 的 file-budget 条目）。
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

// ---------- ② 6 个模型 tool handler ----------
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
// ② 起 tool handler 允许下沉到 src/tools/<tool>/*.ts（拆分），扫描递归且记相对路径。
function walkToolFiles(dir) {
  const out = []
  for (const ent of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, ent.name)
    if (ent.isDirectory()) out.push(...walkToolFiles(p))
    else if (ent.name.endsWith('.ts')) out.push(rel(p))
  }
  return out
}
const toolFiles = fs.existsSync(TOOLS_DIR) ? walkToolFiles(TOOLS_DIR).sort() : []
for (const f of toolFiles) scanToolFile(path.join(ROOT, f))

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
const toolReport = {
  count: names.length,
  names,
  files: Object.fromEntries([...toolDecls].map(([k, v]) => [k, v])),
}
if (names.length !== 6) failures.push('tool handler 数 ' + names.length + ' != 6')
if (expected) {
  const exp = [...new Set(expected)].sort()
  for (const n of exp) if (!toolDecls.has(n)) failures.push('缺少 tool handler: ' + n)
  for (const n of names) if (!exp.includes(n)) failures.push('多出 tool handler: ' + n)
} else {
  failures.push('tests/fixtures/capability.lock.json 不存在或没有 tools 列表，无法对拍 6 tool')
}
for (const [name, files] of toolDecls) {
  if (!files.some((f) => f.startsWith('src/tools/'))) {
    failures.push('tool ' + name + ' 未落在 src/tools/: ' + files.join(','))
  }
}
if (!fs.existsSync(path.join(TOOLS_DIR, 'index.ts')))
  failures.push('src/tools/index.ts（注册汇总）不存在')

// ---------- ③ 目标文件地图 ----------
const TARGET_FILES = [
  'src/index.ts',
  'src/constants.ts',
  'src/host/exec-context.ts',
  'src/contracts/lossless.ts',
  'src/contracts/tool.ts',
  'src/host/backend/index.ts',
  'src/host/backend/spawn.ts',
  'src/host/backend/process-scan.ts',
  'src/host/runtime-bridge.ts',
  'src/host/http.ts',
  'src/utils/frame-cache.ts',
  'src/utils/image.ts',
  'src/ui/cards.ts',
  'src/headless/receipts/index.ts',
  'src/headless/receipts/envelope.ts',
  'src/headless/receipts/human-text.ts',
  'src/tools/schema-diagnostic.ts',
  'src/tools/migration.ts',
  'src/plugin.ts',
  'src/plugin-lifecycle.ts',
  'src/plugin-surfaces.ts',
  'src/tools/errors.ts',
  'src/host/skill.ts',
  'src/tools/index.ts',
  'src/tools/shared/session.ts',
  'src/tools/session/index.ts',
  'src/tools/session/execute.ts',
  'src/tools/session/presenter.ts',
  'src/tools/session/txn.ts',
  'src/tools/session/txn-response.ts',
  'src/tools/op/index.ts',
  'src/tools/op/execute.ts',
  'src/tools/op/read-only.ts',
  'src/tools/op/plan.ts',
  'src/tools/op/plan-response.ts',
  'src/tools/op/loop.ts',
  'src/tools/op/loop-response.ts',
  'src/tools/op/opt.ts',
  'src/tools/op/preset.ts',
  'src/tools/op/preset-response.ts',
  'src/tools/py/index.ts',
  'src/tools/py/execute.ts',
  'src/tools/py/envelope.ts',
  'src/tools/see/index.ts',
  'src/tools/see/execute.ts',
  'src/tools/see/capture.ts',
  'src/tools/see/validate.ts',
  'src/tools/see/sequence.ts',
  'src/tools/see/presenter.ts',
  'src/tools/run/index.ts',
  'src/tools/run/execute.ts',
  'src/tools/run/presenter.ts',
  'src/tools/inspect/index.ts',
  'src/tools/inspect/execute.ts',
  'src/tools/inspect/presenter.ts',
  'src/tui/index.ts',
  'src/tui/register/index.ts',
  'src/tui/register/lifecycle.ts',
  'src/tui/register/commands.ts',
]
const missing = TARGET_FILES.filter((f) => !fs.existsSync(path.join(ROOT, f)))
for (const f of missing) failures.push('目标文件缺失: ' + f)
const extraTools = toolFiles.filter((f) => !TARGET_FILES.includes(f))
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

// ---------- ⑤ 生产源码单文件行预算 ----------
// src 与 runtime 的 TypeScript、addon 的 Python 单文件 ≤600 行；
// 例外只来自 config/exceptions.json 的 kind=file-budget 条目（reason/owner/reviewBy
// 由 check:exceptions 校验），脚本内不再维护内联白名单。runtime/ext 是第三方
// 生成代码，不在本预算范围。runtime/py 的 Blender 侧脚本库
// 使用 1000 行预算（单体函数本身较宽），同样禁止继续膨胀。
const LINE_BUDGET = 600
const PY_LINE_BUDGET = 1000
const EXCEPTIONS_FILE = path.join(ROOT, 'config', 'exceptions.json')
const BUDGET_EXEMPT = new Set()
try {
  const registry = JSON.parse(fs.readFileSync(EXCEPTIONS_FILE, 'utf8'))
  const entries = Array.isArray(registry.entries) ? registry.entries : []
  for (const entry of entries) {
    if (entry && entry.kind === 'file-budget' && typeof entry.path === 'string') {
      BUDGET_EXEMPT.add(entry.path)
    }
  }
} catch (error) {
  failures.push(
    'config/exceptions.json 不可读: ' + (error instanceof Error ? error.message : String(error)),
  )
}
function walkSource(dir, out, exts) {
  if (!fs.existsSync(dir)) return
  for (const ent of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, ent.name)
    if (ent.isDirectory()) {
      if (ent.name === 'ext' && rel(p) === 'runtime/ext') continue
      if (ent.name === 'node_modules' || ent.name === '__pycache__') continue
      walkSource(p, out, exts)
    } else if (exts.some((ext) => ent.name.endsWith(ext)) && !ent.name.endsWith('.d.ts')) {
      out.push(p)
    }
  }
}
const rawBudgetFiles = []
walkSource(path.join(ROOT, 'src'), rawBudgetFiles, ['.ts'])
walkSource(path.join(ROOT, 'runtime'), rawBudgetFiles, ['.ts'])
walkSource(path.join(ROOT, 'extension'), rawBudgetFiles, ['.py'])
// 生成文件（头行声明 AUTO-GENERATED）是 config/routing.json 的派生产物，由
// check:routes 校验新鲜度；它们不是手工生产源码，不参与行预算，否则每次扩表
// 都要改一次脚本白名单。
const GENERATED_HEADER = '// AUTO-GENERATED FILE'
const isGeneratedSource = (file) => {
  try {
    return fs.readFileSync(file, 'utf8').startsWith(GENERATED_HEADER)
  } catch {
    return false
  }
}
const budgetFiles = rawBudgetFiles.filter((file) => !isGeneratedSource(file))
const generatedSkipped = rawBudgetFiles.filter(isGeneratedSource).map((file) => rel(file))
const budgetViolations = []
for (const file of budgetFiles) {
  const key = rel(file)
  const lines = fs.readFileSync(file, 'utf8').split(/\r?\n/).length
  if (lines > LINE_BUDGET && !BUDGET_EXEMPT.has(key)) {
    budgetViolations.push({ path: key, lines })
    failures.push('单文件行预算: ' + key + ' ' + lines + ' > ' + LINE_BUDGET)
  }
}
const budgetReport = {
  budget: LINE_BUDGET,
  files: budgetFiles.length,
  generatedSkipped,
  exempt: [...BUDGET_EXEMPT],
  violations: budgetViolations,
}
const pyBudgetFiles = []
walkSource(path.join(ROOT, 'runtime', 'py'), pyBudgetFiles, ['.py'])
const pyBudgetViolations = []
for (const file of pyBudgetFiles) {
  const key = rel(file)
  const lines = fs.readFileSync(file, 'utf8').split(/\r?\n/).length
  if (lines > PY_LINE_BUDGET) {
    pyBudgetViolations.push({ path: key, lines })
    failures.push('单文件行预算: ' + key + ' ' + lines + ' > ' + PY_LINE_BUDGET)
  }
}
const pyBudgetReport = {
  budget: PY_LINE_BUDGET,
  files: pyBudgetFiles.length,
  violations: pyBudgetViolations,
}

const report = {
  ok: failures.length === 0,
  tool: 'check-structure',
  generatedAt: new Date().toISOString(),
  node: process.version,
  index: indexReport,
  tools: toolReport,
  targetFiles: { required: TARGET_FILES.length, missing },
  importGraph: { files: graph.size, edges, cycles },
  lineBudget: budgetReport,
  pyLineBudget: pyBudgetReport,
  failures,
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
  console.log(
    '  tools: ' +
      names.length +
      ' 个 handler（' +
      Object.keys(toolReport.files).length +
      ' 个名字）',
  )
  console.log(
    '  target files: ' + (TARGET_FILES.length - missing.length) + '/' + TARGET_FILES.length,
  )
  console.log(
    '  import graph: ' + graph.size + ' 文件 / ' + edges + ' 边 / ' + cycles.length + ' 环',
  )
  console.log(
    '  line budget: ' +
      (budgetFiles.length - budgetViolations.length) +
      '/' +
      budgetFiles.length +
      ' 文件 ≤ ' +
      LINE_BUDGET,
  )
  console.log(
    '  py line budget: ' +
      (pyBudgetFiles.length - pyBudgetViolations.length) +
      '/' +
      pyBudgetFiles.length +
      ' 文件 ≤ ' +
      PY_LINE_BUDGET,
  )
  for (const f of failures) console.log('  x ' + f)
  if (outPath) console.log('  报告: ' + outPath)
}
process.exit(failures.length ? 1 : 0)
