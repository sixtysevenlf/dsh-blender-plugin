#!/usr/bin/env node
// @ts-check
/**
 * scripts/check-plan-ids.mjs —— 计划 ID 门禁（6.5 的准备原型，当前只做 warn 模式）。
 *
 * 目标：把"计划 ID 形状"的 token 从代码、测试、脚本和文档里
 * 收敛掉；只有明确登记为例外的地方（历史文档、领域词汇）才允许保留。清理完成后在 CI 用
 * --enforce 阻止新增。
 *
 * 配置：config/plan-ids.json
 *   version   必须为 1
 *   scan      { extensions, skipDirs, skipFiles }，缺省见 DEFAULT_*
 *   prefixes  [{ prefix: 'D', kind: 'decision' }, ...]；prefix 必填单字母大写
 *   exemptions [{ path, reason, prefixes? }]
 *             path 是仓库根相对路径；目录用结尾 '/' 匹配其下所有文件
 *             prefixes 可选，只豁免列出的 token 前缀（其余仍计入残留）
 *
 * 基线：config/plan-id-baseline.json 记录已登记的残留（每个文件的 residual 数）。
 *   默认模式输出 warn 摘要，总是在扫描/配置成功时退出 0；
 *   --enforce 只对"超出基线的新增残留"失败（ratchet，清理后刷基线即可回到绿）；
 *   --write-baseline 用当前扫描结果刷新基线。
 *
 * 用法：
 *   node scripts/check-plan-ids.mjs                     # warn 摘要
 *   node scripts/check-plan-ids.mjs --json              # JSON 报告
 *   node scripts/check-plan-ids.mjs --enforce           # 新增残留即失败
 *   node scripts/check-plan-ids.mjs --write-baseline    # 刷新 config/plan-id-baseline.json
 *   node scripts/check-plan-ids.mjs --root <dir>        # 在别的仓库根上扫描（测试用）
 *   node scripts/check-plan-ids.mjs --config <path>     # 指定配置（相对 --root）
 *   node scripts/check-plan-ids.mjs --baseline <path>   # 指定基线（相对 --root）
 */
import { existsSync, mkdirSync, readFileSync, readdirSync, statSync, writeFileSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const DEFAULT_ROOT = path.resolve(HERE, '..')
const DEFAULT_CONFIG = 'config/plan-ids.json'
const DEFAULT_BASELINE = 'config/plan-id-baseline.json'
const MIN_REASON_LENGTH = 8
const PREFIX_RE = /^[A-Z]$/
const KIND_RE = /^[a-z][a-z0-9-]*$/
const TOKEN_RE =
  /(?<![A-Za-z0-9_.\-/])([TFBCDSPEAM])(0|[1-9][0-9]?)(?:\.([0-9]{1,2}))?([a-z])?(?![A-Za-z0-9_])/g

const DEFAULT_SCAN = {
  extensions: [
    '.ts',
    '.tsx',
    '.mts',
    '.cts',
    '.js',
    '.mjs',
    '.cjs',
    '.py',
    '.json',
    '.md',
    '.yml',
    '.yaml',
  ],
  skipDirs: [
    '.git',
    'node_modules',
    'lib',
    'coverage',
    '.venv',
    'dist',
    '__pycache__',
    '.dsh',
    '.dsh-ops',
    'openspec',
    'runtime/ext',
  ],
  skipFiles: ['package-lock.json', 'package.json'],
}

const argv = process.argv.slice(2)
const jsonOnly = argv.includes('--json')
const enforce = argv.includes('--enforce')
const writeBaseline = argv.includes('--write-baseline')

function usage(message) {
  console.error('check-plan-ids: ' + message)
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
const configArg = option('--config')
const baselineArg = option('--baseline')
const ROOT = rootArg ? path.resolve(process.cwd(), rootArg) : DEFAULT_ROOT
const CONFIG = configArg ? path.resolve(ROOT, configArg) : path.join(ROOT, DEFAULT_CONFIG)
const BASELINE = baselineArg ? path.resolve(ROOT, baselineArg) : path.join(ROOT, DEFAULT_BASELINE)

if (!existsSync(ROOT) || !statSync(ROOT).isDirectory()) usage('--root 不是目录: ' + ROOT)

const failures = []

function rel(p) {
  return path.relative(ROOT, p).split(path.sep).join('/')
}

function isPlainObject(value) {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function readJson(file, label) {
  try {
    return JSON.parse(readFileSync(file, 'utf8'))
  } catch (error) {
    const reason = error instanceof Error ? error.message : String(error)
    failures.push(label + ' 无法读取/解析 ' + rel(file) + ': ' + reason)
    return null
  }
}

/* ---------- 配置 ---------- */

const rawConfig = readJson(CONFIG, 'config')
const prefixes = new Map()
const configPrefixes = new Map()
let scan = { ...DEFAULT_SCAN }
let exemptions = []

if (rawConfig !== null) {
  if (!isPlainObject(rawConfig)) failures.push('config 顶层必须是对象')
  else {
    if (rawConfig.version !== 1) failures.push('config.version 必须为 1')
    if (!Array.isArray(rawConfig.prefixes) || rawConfig.prefixes.length === 0) {
      failures.push('config.prefixes 必须是非空数组')
    } else {
      rawConfig.prefixes.forEach((entry, index) => {
        const label = 'config.prefixes[' + index + ']'
        if (!isPlainObject(entry)) {
          failures.push(label + ' 必须是对象')
          return
        }
        if (typeof entry.prefix !== 'string' || !PREFIX_RE.test(entry.prefix)) {
          failures.push(label + '.prefix 必须是单个大写字母')
        } else if (configPrefixes.has(entry.prefix)) {
          failures.push(label + '.prefix 重复: ' + entry.prefix)
        } else {
          configPrefixes.set(entry.prefix, entry.kind)
        }
        if (typeof entry.kind !== 'string' || !KIND_RE.test(entry.kind)) {
          failures.push(label + '.kind 必须是 kebab-case 标识')
        }
      })
      for (const [prefix, kind] of configPrefixes) prefixes.set(prefix, kind)
    }
    if (rawConfig.scan !== undefined) {
      if (!isPlainObject(rawConfig.scan)) failures.push('config.scan 必须是对象')
      else scan = { ...DEFAULT_SCAN, ...rawConfig.scan }
    }
    for (const [key, expected] of [
      ['extensions', (value) => typeof value === 'string' && value.startsWith('.')],
      ['skipDirs', (value) => typeof value === 'string' && value.length > 0],
      ['skipFiles', (value) => typeof value === 'string' && value.length > 0],
    ]) {
      const value = scan[key]
      if (!Array.isArray(value)) {
        failures.push('config.scan.' + key + ' 必须是数组')
        scan[key] = DEFAULT_SCAN[key]
      } else if (!value.every(expected)) {
        failures.push('config.scan.' + key + ' 含有非法条目')
        scan[key] = DEFAULT_SCAN[key]
      }
    }
    if (!Array.isArray(rawConfig.exemptions)) {
      failures.push('config.exemptions 必须是数组')
    } else {
      rawConfig.exemptions.forEach((entry, index) => {
        const label = 'config.exemptions[' + index + ']'
        if (!isPlainObject(entry)) {
          failures.push(label + ' 必须是对象')
          return
        }
        if (typeof entry.reason !== 'string' || entry.reason.trim().length < MIN_REASON_LENGTH) {
          failures.push(label + '.reason 至少 ' + MIN_REASON_LENGTH + ' 字符')
        }
        if (typeof entry.path !== 'string' || entry.path.trim() === '') {
          failures.push(label + '.path 必须是非空字符串')
          return
        }
        const normalized = entry.path.replace(/\\/g, '/')
        if (path.isAbsolute(entry.path) || normalized.split('/').includes('..')) {
          failures.push(label + '.path 必须是仓库内相对路径: ' + entry.path)
          return
        }
        if (!existsSync(path.join(ROOT, normalized))) {
          failures.push(label + '.path 不存在: ' + normalized)
          return
        }
        const declared = entry.prefixes
        if (declared !== undefined) {
          if (!Array.isArray(declared) || declared.length === 0) {
            failures.push(label + '.prefixes 必须是非空数组')
          } else {
            for (const prefix of declared) {
              if (typeof prefix !== 'string' || !prefixes.has(prefix)) {
                failures.push(label + '.prefixes 含有未登记前缀: ' + String(prefix))
              }
            }
          }
        }
        exemptions.push({
          path: normalized,
          reason: entry.reason.trim(),
          prefixes: Array.isArray(declared) ? declared : null,
        })
      })
    }
  }
}

const configFailures = failures.length > 0
if (configFailures) exemptions = []

/* ---------- 扫描 ---------- */

const extensionSet = new Set(scan.extensions)
const skipDirSet = scan.skipDirs
const skipFileSet = new Set(scan.skipFiles)
const files = []

function isSkippedDir(relPath, name) {
  for (const skip of skipDirSet) {
    if (skip.includes('/')) {
      if (relPath === skip || relPath.startsWith(skip + '/')) return true
    } else if (name === skip) {
      return true
    }
  }
  return false
}

function walk(dir) {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name)
    const relPath = rel(full)
    if (entry.isDirectory()) {
      if (!isSkippedDir(relPath, entry.name)) walk(full)
      continue
    }
    if (!entry.isFile()) continue
    if (relPath === rel(CONFIG) || relPath === rel(BASELINE)) continue
    if (skipFileSet.has(relPath) || skipFileSet.has(entry.name)) continue
    if (!extensionSet.has(path.extname(entry.name))) continue
    files.push(relPath)
  }
}

if (!configFailures) walk(ROOT)

const sortedExemptions = [...exemptions].sort((a, b) => b.path.length - a.path.length)

function findExemption(relPath, prefix) {
  for (const exemption of sortedExemptions) {
    const match = exemption.path.endsWith('/')
      ? relPath.startsWith(exemption.path)
      : relPath === exemption.path
    if (!match) continue
    if (exemption.prefixes && !exemption.prefixes.includes(prefix)) continue
    return exemption
  }
  return null
}

const hits = []
const byFile = new Map()
let exemptCount = 0
const byPrefix = {}
const byKind = {}

for (const relPath of files.sort()) {
  const lines = readFileSync(path.join(ROOT, relPath), 'utf8').split(/\r?\n/)
  let fileHits = 0
  let fileExempt = 0
  lines.forEach((line, index) => {
    for (const match of line.matchAll(TOKEN_RE)) {
      const prefix = match[1]
      const kind = prefixes.get(prefix) || 'unknown'
      const exemption = findExemption(relPath, prefix)
      const token = match[0]
      const exempt = exemption !== null
      fileHits += 1
      if (exempt) {
        exemptCount += 1
        fileExempt += 1
      } else {
        byPrefix[prefix] = (byPrefix[prefix] || 0) + 1
        byKind[kind] = (byKind[kind] || 0) + 1
      }
      hits.push({
        file: relPath,
        line: index + 1,
        column: match.index + 1,
        token,
        prefix,
        kind,
        exempt,
        reason: exempt ? exemption.reason : null,
      })
    }
  })
  if (fileHits > 0) {
    byFile.set(relPath, { count: fileHits, exempt: fileExempt, residual: fileHits - fileExempt })
  }
}

const residual = hits.length - exemptCount
const totals = { all: hits.length, exempt: exemptCount, residual }
const rankedFiles = [...byFile.entries()]
  .map(([file, stats]) => ({ file, ...stats }))
  .filter((item) => item.residual > 0)
  .sort((a, b) => b.residual - a.residual || a.file.localeCompare(b.file))

/* ---------- 基线 ---------- */

const baselineReport = {
  path: rel(BASELINE),
  present: existsSync(BASELINE),
  matches: false,
  recordedResidual: 0,
  newResidual: 0,
  regressions: [],
}
const readBaselineFile = () => {
  if (!existsSync(BASELINE)) return null
  const raw = readJson(BASELINE, 'baseline')
  if (raw === null) return null
  if (!isPlainObject(raw) || raw.version !== 1 || !isPlainObject(raw.files)) {
    failures.push('baseline 格式非法: 需要 {version:1, files:{...}}')
    return null
  }
  return raw
}

const currentSnapshot = {
  version: 1,
  tool: 'check-plan-ids',
  generatedAt: new Date().toISOString(),
  mode: enforce ? 'enforce' : 'warn',
  totals,
  byPrefix,
  byKind,
  files: Object.fromEntries([...byFile.entries()].sort(([a], [b]) => a.localeCompare(b))),
}

if (!configFailures) {
  const baseline = readBaselineFile()
  if (baseline !== null) {
    baselineReport.present = true
    baselineReport.recordedResidual = Number(baseline.totals && baseline.totals.residual) || 0
    baselineReport.matches =
      JSON.stringify(baseline.files) === JSON.stringify(currentSnapshot.files) &&
      baselineReport.recordedResidual === residual
    for (const item of rankedFiles) {
      const recorded = Number(baseline.files[item.file] && baseline.files[item.file].residual) || 0
      const extra = item.residual - recorded
      if (extra > 0) {
        baselineReport.regressions.push({ file: item.file, residual: item.residual, recorded })
        baselineReport.newResidual += extra
      }
    }
  } else {
    baselineReport.newResidual = residual
    for (const item of rankedFiles) {
      baselineReport.regressions.push({ file: item.file, residual: item.residual, recorded: 0 })
    }
  }
}

/* ---------- 输出 ---------- */

if (writeBaseline && !configFailures) {
  mkdirSync(path.dirname(BASELINE), { recursive: true })
  writeFileSync(BASELINE, JSON.stringify(currentSnapshot, null, 2) + '\n')
  baselineReport.path = rel(BASELINE)
  baselineReport.present = true
  baselineReport.recordedResidual = residual
  baselineReport.newResidual = 0
  baselineReport.regressions = []
  baselineReport.matches = true
}
const ok = failures.length === 0 && (!enforce || baselineReport.newResidual === 0)
const report = {
  ok,
  tool: 'check-plan-ids',
  mode: enforce ? 'enforce' : 'warn',
  root: rel(ROOT) || '.',
  config: rel(CONFIG),
  scannedFiles: files.length,
  totals,
  byPrefix,
  byKind,
  fileCount: byFile.size,
  topFiles: rankedFiles.slice(0, 15),
  residualHits: hits.filter((hit) => !hit.exempt),
  baseline: baselineReport,
  failures,
}

if (jsonOnly) {
  console.log(JSON.stringify(report, null, 2))
} else if (failures.length > 0) {
  console.error('check-plan-ids: FAIL (config)')
  for (const failure of failures) console.error('  x ' + failure)
} else if (!ok) {
  console.error('check-plan-ids: FAIL (enforce)')
  console.error(
    '  新增计划 ID 残留 ' +
      baselineReport.newResidual +
      ' 处（基线 ' +
      baselineReport.recordedResidual +
      ' 处）:',
  )
  for (const regression of baselineReport.regressions.slice(0, 20)) {
    console.error(
      '  x ' +
        regression.file +
        ': residual ' +
        regression.residual +
        ' > baseline ' +
        regression.recorded,
    )
  }
  if (baselineReport.regressions.length > 20) {
    console.error('  ... ' + (baselineReport.regressions.length - 20) + ' more files')
  }
} else {
  const verb = writeBaseline ? 'BASELINE WRITTEN' : enforce ? 'PASS (enforce)' : 'WARN'
  console.log(
    'check-plan-ids: ' +
      verb +
      ' — ' +
      totals.all +
      ' tokens in ' +
      byFile.size +
      ' files; exempt ' +
      totals.exempt +
      ', residual ' +
      totals.residual,
  )
  if (totals.residual > 0) {
    console.log(
      '  residual by prefix: ' +
        Object.entries(byPrefix)
          .sort()
          .map(([k, v]) => k + '=' + v)
          .join(' '),
    )
    console.log(
      '  baseline ' +
        rel(BASELINE) +
        ': ' +
        (baselineReport.present
          ? baselineReport.recordedResidual +
            ' residual recorded' +
            (baselineReport.matches ? ' (in sync)' : '')
          : 'missing (run --write-baseline after review)'),
    )
    for (const item of rankedFiles.slice(0, 10)) {
      console.log('  ' + String(item.residual).padStart(4) + '  ' + item.file)
    }
    if (rankedFiles.length > 10) console.log('  ... ' + (rankedFiles.length - 10) + ' more files')
  }
}

if (failures.length > 0) process.exit(1)
if (enforce && baselineReport.newResidual > 0) process.exit(1)
