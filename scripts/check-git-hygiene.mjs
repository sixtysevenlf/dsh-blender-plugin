#!/usr/bin/env node
/**
 * scripts/check-git-hygiene.mjs —— 仓库卫生检查（需要 git 检出）。
 *
 * 目标：lib/ 是 prepack 重建的发布产物，不得被 git 跟踪；.gitignore 必须保留忽略规则。
 * 失败的语义是「仓库状态不对」，不是「环境跑不了」：缺 .git 直接失败，不做自动跳过。
 * 只应在 git 检出里运行：CI（PR / release）与本地 clone。
 *
 * 用法：
 *   node scripts/check-git-hygiene.mjs              # 检查仓库根
 *   node scripts/check-git-hygiene.mjs --json       # 机器可读
 *   node scripts/check-git-hygiene.mjs --root <dir> # 指定检出目录（测试用）
 */
import fs from 'node:fs'
import path from 'node:path'
import { spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const argv = process.argv.slice(2)
const jsonOnly = argv.includes('--json')
const rootArg = argv.indexOf('--root')
const ROOT = path.resolve(
  rootArg >= 0 && argv[rootArg + 1] ? argv[rootArg + 1] : path.resolve(HERE, '..'),
)

const failures = []
const steps = []

function runGit(gitArgs) {
  return spawnSync('git', gitArgs, { cwd: ROOT, encoding: 'utf8', windowsHide: true })
}

function errorText(r) {
  if (r.error) return String(r.error.message || r.error)
  return (
    String(r.stderr || r.stdout || '')
      .trim()
      .split(/\r?\n/)[0] || 'unknown git error'
  )
}

function realPath(p) {
  const abs = path.resolve(p)
  try {
    return fs.realpathSync(abs)
  } catch {
    return abs
  }
}

function samePath(a, b) {
  const norm = (p) => realPath(p).replace(/\\/g, '/')
  return process.platform === 'win32'
    ? norm(a).toLowerCase() === norm(b).toLowerCase()
    : norm(a) === norm(b)
}

// ① 必须是 git 检出，且检出根目录就是这个目录
const inside = runGit(['rev-parse', '--is-inside-work-tree'])
if (inside.error || inside.status !== 0 || String(inside.stdout || '').trim() !== 'true') {
  steps.push({ step: 'git-worktree', exit: 1, output: 'not a git checkout' })
  failures.push('不是 git 检出（缺少 .git）：' + errorText(inside))
} else {
  const top = runGit(['rev-parse', '--show-toplevel'])
  if (top.error || top.status !== 0 || !samePath(String(top.stdout || '').trim(), ROOT)) {
    steps.push({ step: 'git-worktree', exit: 1, output: 'not the checkout root' })
    failures.push('git 检出根目录不是当前目录：' + String(top.stdout || '').trim())
  } else {
    steps.push({ step: 'git-worktree', exit: 0, output: 'git checkout ok' })

    // ② lib/ 不得被跟踪
    const ls = runGit(['ls-files', '--', 'lib'])
    const tracked = String(ls.stdout || '')
      .trim()
      .split(/\r?\n/)
      .filter(Boolean)
    if (ls.error || ls.status !== 0) {
      steps.push({ step: 'lib-not-tracked', exit: 1, output: 'git ls-files failed' })
      failures.push('git ls-files lib 失败：' + errorText(ls))
    } else if (tracked.length) {
      steps.push({ step: 'lib-not-tracked', exit: 1, output: tracked.length + ' tracked' })
      failures.push(
        'lib/ 仍被 git 跟踪（应为构建产物）：' +
          tracked.slice(0, 10).join(', ') +
          (tracked.length > 10 ? ' …' : ''),
      )
    } else {
      steps.push({ step: 'lib-not-tracked', exit: 0, output: '0 tracked' })
    }

    // ③ .gitignore 必须保留 lib 忽略规则
    const ignorePath = path.join(ROOT, '.gitignore')
    const ignoreText = fs.existsSync(ignorePath) ? fs.readFileSync(ignorePath, 'utf8') : ''
    const hasRule = ignoreText.split(/\r?\n/).some((line) => /^\/?lib\/?$/.test(line.trim()))
    steps.push({
      step: 'gitignore-lib-rule',
      exit: hasRule ? 0 : 1,
      output: hasRule ? '/lib/' : 'missing',
    })
    if (!hasRule) failures.push('.gitignore 缺少 /lib 忽略规则')
  }
}

const report = {
  ok: failures.length === 0,
  tool: 'check-git-hygiene',
  generatedAt: new Date().toISOString(),
  root: ROOT,
  failures,
  steps,
}
if (jsonOnly) process.stdout.write(JSON.stringify(report, null, 2) + '\n')
else {
  console.log('check-git-hygiene: ' + (report.ok ? 'PASS' : 'FAIL'))
  for (const s of steps) {
    const text = Array.isArray(s.output) ? s.output.join('\n') : String(s.output || '')
    console.log(
      '  ' +
        (s.exit === 0 ? '✓' : '✗') +
        ' ' +
        s.step +
        (text ? ' — ' + text.split(/\r?\n/)[0] : ''),
    )
  }
  for (const f of failures) console.log('  x ' + f)
  if (failures.length) {
    console.log(
      '  hint: lib/ 是构建产物；若被跟踪，运行 git rm --cached -r lib；.gitignore 需保留 /lib 规则',
    )
  }
}
process.exitCode = failures.length ? 1 : 0
