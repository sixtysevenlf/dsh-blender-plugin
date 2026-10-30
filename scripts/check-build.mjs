#!/usr/bin/env node
/**
 * scripts/check-build.mjs —— prepack / CI 的构建检查（T5）。
 *
 * 不写仓库里的 lib/（那是发布产物，由 scripts/build.sh 在发版时重建），而是：
 *   ① 用与 npm run typecheck 同一个 tsc（scripts/typecheck.mjs --print）把 src 编译到临时目录；
 *   ② 断言入口 / 15 工具 / 类型声明确实产出，且 src 里没有 @ts-nocheck；
 *   ③ 运行 scripts/check-structure.mjs（入口行数、文件地图、15 handler、无环）；
 *   ④ 运行 scripts/build-client.mjs --check（lib/client.js 是否与 src/client 同步）。
 *
 * 用法：
 *   node scripts/check-build.mjs            # 人读摘要
 *   node scripts/check-build.mjs --json     # 机器可读
 */
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const ROOT = path.resolve(HERE, '..')
const jsonOnly = process.argv.includes('--json')
const failures = []
const steps = []

function run(label, file, args) {
  const r = spawnSync(file, args, { cwd: ROOT, encoding: 'utf8', env: process.env })
  const out = ((r.stdout || '') + (r.stderr || '')).trim()
  steps.push({ step: label, exit: r.status, output: out.split(/\r?\n/).slice(-40) })
  if (r.error) failures.push(label + ': 执行失败 ' + String(r.error.message || r.error))
  else if (r.status !== 0) failures.push(label + ': 退出码 ' + String(r.status))
  return r
}

// ① tsc 解析
const printed = spawnSync(process.execPath, [path.join(HERE, 'typecheck.mjs'), '--print'], {
  cwd: ROOT,
  encoding: 'utf8',
  env: process.env
})
if (printed.status !== 0) {
  failures.push('解析 tsc 失败: ' + (printed.stderr || printed.stdout || '').trim())
}
const tsc = (printed.stdout || '').trim()
steps.push({ step: 'resolve-tsc', exit: printed.status, output: tsc })

// ② 临时编译
let tmp = null
if (!failures.length) {
  tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'dsh-build-check-'))
  const cfg = {
    extends: path.join(ROOT, 'tsconfig.json'),
    compilerOptions: {
      outDir: path.join(tmp, 'lib'),
      declarationDir: path.join(tmp, 'lib', 'types'),
      sourceMap: false,
      incremental: false,
      typeRoots: [path.join(ROOT, 'node_modules', '@types')]
    }
  }
  const cfgPath = path.join(tmp, 'tsconfig.json')
  fs.writeFileSync(cfgPath, JSON.stringify(cfg, null, 2))
  run('tsc-emit', process.execPath, [tsc, '-p', cfgPath])
  if (!failures.length) {
    const required = [
      'lib/index.js',
      'lib/plugin.js',
      'lib/tools/index.js',
      'lib/tools/viewport.js',
      'lib/plan/tool.js',
      'lib/host/backend.js',
      'lib/host/http.js',
      'lib/ui/cards.js',
      'lib/headless/receipts.js',
      'lib/types/index.d.ts'
    ]
    const missing = required.filter((f) => !fs.existsSync(path.join(tmp, f)))
    for (const f of missing) failures.push('构建产物缺失: ' + f)
    steps.push({ step: 'artifacts', exit: missing.length ? 1 : 0, output: required.join(', ') })
    const entryBytes = fs.existsSync(path.join(tmp, 'lib', 'index.js')) ? fs.statSync(path.join(tmp, 'lib', 'index.js')).size : -1
    steps.push({ step: 'entry-bytes', exit: 0, output: String(entryBytes) + ' B' })
  }
}

// ③ 源码卫生：不再有 @ts-nocheck
const nocheck = []
function walk(dir) {
  if (!fs.existsSync(dir)) return
  for (const ent of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, ent.name)
    if (ent.isDirectory()) walk(p)
    // T4 要求 src/tui-host.ts 原样保留（它自带 @ts-nocheck，后续类型化单列任务处理）
    else if (ent.name === 'tui-host.ts') continue
    else if (ent.name.endsWith('.ts') && fs.readFileSync(p, 'utf8').includes('@ts-nocheck')) {
      nocheck.push(path.relative(ROOT, p).split(path.sep).join('/'))
    }
  }
}
walk(path.join(ROOT, 'src'))
for (const f of nocheck) failures.push('src 仍有 @ts-nocheck: ' + f)
steps.push({ step: 'no-ts-nocheck', exit: nocheck.length ? 1 : 0, output: nocheck.join(', ') || 'clean' })

// ④ 结构门禁
run('check-structure', process.execPath, [path.join(HERE, 'check-structure.mjs')])

// ⑤ 客户端产物同步
run('build-client-check', process.execPath, [path.join(HERE, 'build-client.mjs'), '--check'])

if (tmp) {
  try {
    fs.rmSync(tmp, { recursive: true, force: true })
  } catch {
    /* 临时目录清理失败不影响结论 */
  }
}

const report = {
  ok: failures.length === 0,
  tool: 'check-build',
  generatedAt: new Date().toISOString(),
  node: process.version,
  failures,
  steps
}
if (jsonOnly) process.stdout.write(JSON.stringify(report, null, 2) + '\n')
else {
  console.log('check-build: ' + (report.ok ? 'PASS' : 'FAIL'))
  for (const s of steps) {
    const text = Array.isArray(s.output) ? s.output.join(String.fromCharCode(10)) : String(s.output || '')
    console.log('  ' + (s.exit === 0 ? '✓' : '✗') + ' ' + s.step + (text ? ' — ' + text.split(/\r?\n/)[0] : ''))
  }
  for (const f of failures) console.log('  x ' + f)
}
process.exit(failures.length ? 1 : 0)
