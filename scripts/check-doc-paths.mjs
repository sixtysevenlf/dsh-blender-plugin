#!/usr/bin/env node
// 文档链接门禁：相对链接、命令存在性、入链图与不存在路径引用。
//
// 硬检查（失败即退出 1）：相对链接目标存在、不越出仓库、npm run 的脚本存在。
// 新增检查（6.6 的准备，默认 warn；--enforce 只对"超出基线的增量"失败）：
//   1. 每个非归档文档至少有一条入链（中英文对偶互链按普通链接计）。
//   2. 文档正文里引用的仓库路径必须存在（禁止 runtime/catalog.ts 这类幽灵路径）。
// 基线：config/doc-links-baseline.json 收集历史遗留（orphanDocs / missingRefs），
// 清理后用 --write-baseline 收缩；--enforce 阻止新增。
import { existsSync, mkdirSync, readFileSync, readdirSync, statSync, writeFileSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const DEFAULT_ROOT = path.resolve(HERE, '..')
const DEFAULT_BASELINE = 'config/doc-links-baseline.json'
const LEGACY_ROOT_DOCS = ['README.md', 'AGENTS.md', 'CONTRIBUTING.md', 'MIGRATIONS.md']
const EXTENDED_ROOT_DOCS = [...LEGACY_ROOT_DOCS, 'SECURITY.md']
const ARCHIVE_PREFIX = 'docs/archive/'
const PATH_ROOTS = [
  'src',
  'runtime',
  'scripts',
  'tests',
  'config',
  'docs',
  'extension',
  'skills',
  'coverage',
  'lib',
  'dist',
  'node_modules',
  '.github',
  '.dsh',
]
const ROOT_FILES = [
  'package.json',
  'pyproject.toml',
  'vitest.config.ts',
  'eslint.config.mjs',
  'tsconfig.json',
  'tsconfig.test.json',
  'tsconfig.scripts.json',
  '.gitignore',
  'README.md',
  'AGENTS.md',
  'CONTRIBUTING.md',
  'MIGRATIONS.md',
  'SECURITY.md',
  'CHANGELOG.md',
]
const SKIP_REF_PREFIXES = ['lib/', 'node_modules/', 'dist/', 'coverage/', '.venv/', 'runtime/ext/']

const argv = process.argv.slice(2)
const jsonOnly = argv.includes('--json')
const enforce = argv.includes('--enforce')
const writeBaseline = argv.includes('--write-baseline')

function usage(message) {
  console.error('check-doc-paths: ' + message)
  process.exit(2)
}

function option(name) {
  const index = argv.indexOf(name)
  if (index < 0) return null
  const value = argv[index + 1]
  if (!value || value.startsWith('--')) usage(name + ' 需要一个参数')
  return value
}

const rootArg = option('--root')
const baselineArg = option('--baseline')
const ROOT = rootArg ? path.resolve(process.cwd(), rootArg) : DEFAULT_ROOT
const BASELINE = baselineArg ? path.resolve(ROOT, baselineArg) : path.join(ROOT, DEFAULT_BASELINE)

if (!existsSync(ROOT) || !statSync(ROOT).isDirectory()) usage('--root 不是目录: ' + ROOT)

const stripCode = (text) => text.replace(/```[\s\S]*?```/g, '').replace(/`[^`\n]*`/g, '')

const failures = []

function rel(p) {
  return path.relative(ROOT, p).split(path.sep).join('/')
}

/* ---------- 1. 既有硬检查 ---------- */

let pkg = null
try {
  pkg = JSON.parse(readFileSync(path.join(ROOT, 'package.json'), 'utf8'))
} catch (error) {
  const reason = error instanceof Error ? error.message : String(error)
  failures.push('package.json 无法读取/解析: ' + reason)
}

const legacyDocs = [...LEGACY_ROOT_DOCS]
const docsDir = path.join(ROOT, 'docs')
if (existsSync(docsDir) && statSync(docsDir).isDirectory()) {
  for (const file of readdirSync(docsDir)) {
    if (file.endsWith('.md')) legacyDocs.push('docs/' + file)
  }
}

for (const relPath of legacyDocs) {
  const file = path.join(ROOT, relPath)
  if (!existsSync(file)) continue
  const text = stripCode(readFileSync(file, 'utf8'))
  for (const match of text.matchAll(/\[[^\]]*\]\(([^)]+)\)/g)) {
    const target = match[1].trim()
    if (!target || target.startsWith('#') || /^[a-z]+:/i.test(target)) continue
    const clean = target.split('#')[0].split('?')[0]
    if (!clean) continue
    const resolved = path.resolve(path.dirname(file), clean)
    if (!resolved.startsWith(ROOT)) failures.push(relPath + ': link escapes repo: ' + target)
    else if (!existsSync(resolved)) failures.push(relPath + ': missing link target: ' + target)
  }
  if (pkg && pkg.scripts) {
    for (const match of text.matchAll(/npm run ([a-z0-9:_-]+)/g)) {
      if (!Object.prototype.hasOwnProperty.call(pkg.scripts, match[1])) {
        failures.push(relPath + ': unknown npm script: ' + match[1])
      }
    }
  }
}

/* ---------- 2. 扩展文档集合 ---------- */

const rootDocs = EXTENDED_ROOT_DOCS.filter((relPath) => existsSync(path.join(ROOT, relPath)))
const allDocs = []

function walkDocs(dir) {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name)
    if (entry.isDirectory()) {
      walkDocs(full)
      continue
    }
    if (entry.isFile() && entry.name.endsWith('.md')) allDocs.push(rel(full))
  }
}

if (existsSync(docsDir) && statSync(docsDir).isDirectory()) walkDocs(docsDir)

allDocs.sort()
// 归档文档不算"必须被链接"的目标，但它可以作为入链来源（历史文档里的互链也是链接）。
const extendedDocs = rootDocs
  .concat(allDocs.filter((relPath) => !relPath.startsWith(ARCHIVE_PREFIX)))
  .sort()
const extendedSet = new Set(extendedDocs)
// 入链来源：所有文档（含归档），目标是上面的非归档集合。
const sourceDocs = [...new Set([...rootDocs, ...allDocs])]

/* ---------- 3. 入链图 ---------- */

function markdownTargets(text) {
  const targets = []
  for (const match of text.matchAll(/\[[^\]]*\]\(([^)]+)\)/g)) {
    const target = match[1].trim()
    if (!target || target.startsWith('#') || /^[a-z]+:/i.test(target)) continue
    const clean = target.split('#')[0].split('?')[0]
    if (clean) targets.push(clean)
  }
  return targets
}

const inbound = new Map(extendedDocs.map((doc) => [doc, new Set()]))
for (const doc of sourceDocs) {
  const file = path.join(ROOT, doc)
  let text
  try {
    text = stripCode(readFileSync(file, 'utf8'))
  } catch {
    continue
  }
  for (const target of markdownTargets(text)) {
    const resolved = path.resolve(path.dirname(file), target)
    if (!resolved.startsWith(ROOT)) continue
    const relTarget = rel(resolved)
    if (!extendedSet.has(relTarget)) continue
    if (relTarget === doc) continue
    inbound.get(relTarget).add(doc)
  }
}
const inboundReport = {}
const orphanDocs = []
for (const doc of extendedDocs) {
  const count = inbound.get(doc).size
  inboundReport[doc] = count
  if (count === 0) orphanDocs.push(doc)
}

/* ---------- 4. 幽灵路径引用 ---------- */

const refRootPattern = new RegExp(
  '(?<![A-Za-z0-9_.\\-/])((?:' +
    PATH_ROOTS.map((root) => root.replace(/\./g, '\\.')).join('|') +
    ')\\/[A-Za-z0-9_\\u4e00-\\u9fff.\\-\\/]*)',
  'g',
)
const rootFilePattern = new RegExp(
  '(?<![A-Za-z0-9_.\\-/])(' +
    ROOT_FILES.map((name) => name.replace(/\./g, '\\.')).join('|') +
    ')(?![A-Za-z0-9_.\\-])',
  'g',
)

function cleanRef(raw) {
  let token = raw
    .trim()
    .replace(/[.,;:!?，。；：！？、)\]】」』"'\u3002\uff0c\uff1b\uff1a\uff09\u3011]+$/g, '')
  token = token.split('#')[0].split('?')[0]
  const symbol = token.match(/^([^:]+):([A-Za-z_$][A-Za-z0-9_$]*)$/)
  if (symbol) token = symbol[1]
  const scope = token.split('::')[0]
  if (scope) token = scope
  return token
}

function existsPath(token) {
  const candidates = [path.resolve(ROOT, token)]
  const fromDoc = path.resolve(path.dirname(path.join(ROOT, currentDoc)), token)
  if (fromDoc !== candidates[0]) candidates.push(fromDoc)
  for (const candidate of candidates) {
    if (!candidate.startsWith(ROOT)) continue
    if (existsSync(candidate)) return true
  }
  return false
}

let currentDoc = ''
const missingRefs = []
const analyzedText = (text) => {
  // 幽灵路径经常写在行内 code span 里，所以这里只去掉围栏代码块和反引号，保留文本。
  const withoutFences = text.replace(/```[\s\S]*?```/g, ' ').replace(/`/g, '')
  const withoutUrls = withoutFences.replace(/https?:\/\/[^\s)>\]"']+/g, ' ')
  return withoutUrls.replace(/\[([^\]]*)\]\([^)]+\)/g, '$1')
}

for (const doc of extendedDocs) {
  currentDoc = doc
  const file = path.join(ROOT, doc)
  let text
  try {
    text = readFileSync(file, 'utf8')
  } catch {
    continue
  }
  const lines = analyzedText(text).split(/\r?\n/)
  const seen = new Set()
  lines.forEach((line, index) => {
    const tokens = []
    for (const match of line.matchAll(refRootPattern)) {
      const next = line[match.index + match[0].length] || ''
      if (/[*?{}]/.test(next)) continue
      tokens.push(match[1])
    }
    for (const match of line.matchAll(rootFilePattern)) tokens.push(match[1])
    for (const raw of tokens) {
      const token = cleanRef(raw)
      if (!token || token.length < 3) continue
      if (/[*?{}<>…]/.test(token)) continue
      if (token.includes('//') || /^[a-z]+:/i.test(token)) continue
      if (SKIP_REF_PREFIXES.some((prefix) => token.startsWith(prefix))) continue
      if (existsPath(token)) continue
      const key = doc + '\u0000' + token
      if (seen.has(key)) continue
      seen.add(key)
      missingRefs.push({ doc, line: index + 1, target: token })
    }
  })
}
currentDoc = ''
missingRefs.sort((a, b) => (a.doc === b.doc ? a.line - b.line : a.doc.localeCompare(b.doc)))

/* ---------- 5. 基线（ratchet） ---------- */

const baselineReport = {
  path: rel(BASELINE),
  present: false,
  review: null,
  acceptedOrphans: 0,
  acceptedMissingRefs: 0,
  matches: false,
  newOrphans: [],
  newMissingRefs: [],
}

const acceptedOrphans = new Set()
const acceptedRefs = new Set()
let baselineReview = null
if (existsSync(BASELINE)) {
  try {
    const raw = JSON.parse(readFileSync(BASELINE, 'utf8'))
    if (typeof raw !== 'object' || raw === null || raw.version !== 1) {
      throw new Error('需要 {version:1, ...}')
    }
    baselineReport.present = true
    if (raw.review && typeof raw.review === 'object') baselineReview = raw.review
    baselineReport.review = baselineReview
    for (const doc of Array.isArray(raw.orphanDocs) ? raw.orphanDocs : [])
      acceptedOrphans.add(String(doc))
    for (const entry of Array.isArray(raw.missingRefs) ? raw.missingRefs : []) {
      if (entry && typeof entry.doc === 'string' && typeof entry.target === 'string') {
        acceptedRefs.add(entry.doc + '\u0000' + entry.target)
      }
    }
    baselineReport.acceptedOrphans = acceptedOrphans.size
    baselineReport.acceptedMissingRefs = acceptedRefs.size
    const currentOrphanSet = new Set(orphanDocs)
    const currentRefSet = new Set(missingRefs.map((entry) => entry.doc + '\u0000' + entry.target))
    const sameSet = (a, b) => a.size === b.size && [...a].every((item) => b.has(item))
    baselineReport.matches =
      sameSet(currentOrphanSet, acceptedOrphans) && sameSet(currentRefSet, acceptedRefs)
  } catch (error) {
    const reason = error instanceof Error ? error.message : String(error)
    failures.push('baseline 无法读取/解析 ' + rel(BASELINE) + ': ' + reason)
  }
}

function validReviewDate(value) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(String(value))) return false
  const parsed = new Date(String(value) + 'T00:00:00Z')
  return !Number.isNaN(parsed.getTime()) && parsed.toISOString().slice(0, 10) === String(value)
}

// A baseline that accepts missing refs must name an owner and a review date:
// an unowned exception is exactly how the ghost runtime/catalog.ts survived.
// --write-baseline is the bootstrap path, so it writes first and lets the next
// non-write run report the missing review.
if (!writeBaseline && baselineReport.present && missingRefs.length > 0) {
  if (!baselineReview) {
    failures.push(
      'baseline ' + rel(BASELINE) + ': missingRefs 非空，必须提供 review {owner, reviewBy, reason}',
    )
  } else {
    if (!baselineReview.owner || String(baselineReview.owner).trim() === '')
      failures.push('baseline ' + rel(BASELINE) + ': review.owner 不能为空')
    if (!baselineReview.reason || String(baselineReview.reason).trim() === '')
      failures.push('baseline ' + rel(BASELINE) + ': review.reason 不能为空')
    if (!validReviewDate(baselineReview.reviewBy)) {
      failures.push('baseline ' + rel(BASELINE) + ': review.reviewBy 必须是 YYYY-MM-DD')
    } else {
      const today = new Date().toISOString().slice(0, 10)
      if (String(baselineReview.reviewBy) < today) {
        failures.push(
          'baseline ' +
            rel(BASELINE) +
            ': review 已过期 (' +
            baselineReview.reviewBy +
            ' < ' +
            today +
            ')',
        )
      }
    }
  }
}

for (const doc of orphanDocs) {
  if (!acceptedOrphans.has(doc)) baselineReport.newOrphans.push(doc)
}
for (const entry of missingRefs) {
  if (!acceptedRefs.has(entry.doc + '\u0000' + entry.target))
    baselineReport.newMissingRefs.push(entry)
}
/* ---------- 6. 输出 ---------- */

const snapshot = {
  version: 1,
  tool: 'check-doc-paths',
  generatedAt: new Date().toISOString(),
  docs: extendedDocs,
  inbound: inboundReport,
  orphanDocs,
  missingRefs: missingRefs.map((entry) => ({ doc: entry.doc, target: entry.target })),
  ...(baselineReview ? { review: baselineReview } : {}),
}

if (writeBaseline && failures.length === 0) {
  mkdirSync(path.dirname(BASELINE), { recursive: true })
  writeFileSync(BASELINE, JSON.stringify(snapshot, null, 2) + '\n')
  baselineReport.path = rel(BASELINE)
  baselineReport.present = true
  baselineReport.acceptedOrphans = orphanDocs.length
  baselineReport.acceptedMissingRefs = missingRefs.length
  baselineReport.newOrphans = []
  baselineReport.newMissingRefs = []
  baselineReport.matches = true
}

const newFindings = baselineReport.newOrphans.length + baselineReport.newMissingRefs.length
const ok = failures.length === 0 && (!enforce || newFindings === 0)
const report = {
  ok,
  tool: 'check-doc-paths',
  mode: enforce ? 'enforce' : 'warn',
  root: rel(ROOT) || '.',
  legacy: { docs: legacyDocs, failures },
  docs: { extendedCount: extendedDocs.length, paths: extendedDocs },
  findings: { inbound: inboundReport, orphanDocs, missingRefs },
  baseline: baselineReport,
}

if (jsonOnly) {
  console.log(JSON.stringify(report, null, 2))
} else if (failures.length > 0) {
  console.error('check-doc-paths: FAIL')
  for (const failure of failures.slice(0, 40)) console.error('  x ' + failure)
  if (failures.length > 40) console.error('  ... ' + (failures.length - 40) + ' more')
} else if (!ok) {
  console.error('check-doc-paths: FAIL (enforce)')
  for (const doc of baselineReport.newOrphans) console.error('  x new orphan doc: ' + doc)
  for (const entry of baselineReport.newMissingRefs.slice(0, 20)) {
    console.error(
      '  x new missing path ref: ' + entry.doc + ':' + entry.line + ' -> ' + entry.target,
    )
  }
} else {
  const verb = writeBaseline ? 'BASELINE WRITTEN' : enforce ? 'PASS (enforce)' : 'PASS'
  console.log(
    'check-doc-paths: ' +
      verb +
      ' (' +
      legacyDocs.length +
      ' legacy docs; ' +
      extendedDocs.length +
      ' extended docs, ' +
      orphanDocs.length +
      ' orphan, ' +
      missingRefs.length +
      ' missing path refs)',
  )
  if (baselineReport.present) {
    console.log(
      '  baseline ' +
        rel(BASELINE) +
        ': accepted ' +
        baselineReport.acceptedOrphans +
        ' orphan / ' +
        baselineReport.acceptedMissingRefs +
        ' missing refs' +
        (baselineReport.matches ? ' (in sync)' : ' (drift; --write-baseline after review)'),
    )
  } else {
    console.log(
      '  baseline ' +
        rel(BASELINE) +
        ': missing (run --write-baseline after collecting legacy findings)',
    )
  }
  if (orphanDocs.length > 0) console.log('  orphans: ' + orphanDocs.join(', '))
}

if (failures.length > 0) process.exit(1)
if (enforce && newFindings > 0) process.exit(1)
