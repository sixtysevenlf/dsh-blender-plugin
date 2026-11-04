#!/usr/bin/env node
// @ts-check
/**
 * scripts/check-exceptions.mjs —— 例外登记表门禁。
 *
 * config/exceptions.json 是仓库里唯一允许的 gate 例外数据源：各 gate 脚本读取对应 kind 的
 * 条目，而不是各自维护白名单（幽灵条目 runtime/catalog.ts 就是分散维护的直接后果，
 * 见 docs/architecture.md §8）。
 *
 * 条目字段：{id, kind, path, reason, owner, reviewBy}
 *   id       唯一标识，EXC- 前缀
 *   kind     KINDS 之一（见下）
 *   path     仓库根相对路径，必须存在
 *   reason   为什么需要例外（>= 16 字符）
 *   owner    负责人 / 角色
 *   reviewBy YYYY-MM-DD；早于今天即过期失败
 *
 * 用法：
 *   node scripts/check-exceptions.mjs                  # 人读摘要
 *   node scripts/check-exceptions.mjs --json           # JSON 报告
 *   node scripts/check-exceptions.mjs --root <dir>     # 在别的仓库根上校验（测试用）
 *   node scripts/check-exceptions.mjs --config <path>  # 指定登记表（测试用，相对 --root）
 */
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const DEFAULT_ROOT = path.resolve(HERE, '..')

/** 例外种类枚举。新增 kind 必须同时让对应 gate 消费它，否则登记是死数据。 */
const KINDS = [
  'file-budget',
  'function-budget',
  'python-static',
  'single-line-reexport',
  'facade-export',
  'doc-path',
  'matrix-row',
]
const REQUIRED_FIELDS = ['id', 'kind', 'path', 'reason', 'owner', 'reviewBy']
const ID_PATTERN = /^EXC-[A-Z0-9][A-Z0-9-]*$/
const DATE_PATTERN = /^\d{4}-\d{2}-\d{2}$/
const MIN_REASON_LENGTH = 16

const argv = process.argv.slice(2)
const jsonOnly = argv.includes('--json')

function usage(message) {
  console.error('check-exceptions: ' + message)
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
const ROOT = rootArg ? path.resolve(rootArg) : DEFAULT_ROOT
const CONFIG = configArg
  ? path.resolve(ROOT, configArg)
  : path.join(ROOT, 'config', 'exceptions.json')

const failures = []
const today = new Date().toISOString().slice(0, 10)

function rel(p) {
  return path.relative(ROOT, p).split(path.sep).join('/')
}

function isPlainObject(value) {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function validDate(value) {
  if (!DATE_PATTERN.test(value)) return false
  const parts = value.split('-').map(Number)
  const date = new Date(Date.UTC(parts[0], parts[1] - 1, parts[2]))
  return (
    date.getUTCFullYear() === parts[0] &&
    date.getUTCMonth() === parts[1] - 1 &&
    date.getUTCDate() === parts[2]
  )
}

function checkPath(id, value) {
  const normalized = value.replace(/\\/g, '/')
  if (path.isAbsolute(value) || normalized.split('/').includes('..')) {
    failures.push(id + ': path 必须是仓库内相对路径 - ' + value)
    return
  }
  const resolved = path.resolve(ROOT, normalized)
  if (resolved !== ROOT && !resolved.startsWith(ROOT + path.sep)) {
    failures.push(id + ': path 越出仓库根 - ' + value)
    return
  }
  if (!fs.existsSync(resolved)) failures.push(id + ': path 不存在 - ' + normalized)
}

let rawEntries = []
try {
  const registry = JSON.parse(fs.readFileSync(CONFIG, 'utf8'))
  if (!isPlainObject(registry)) throw new Error('顶层必须是对象')
  if (registry.version !== 1) throw new Error('version 必须为 1')
  if (!Array.isArray(registry.entries)) throw new Error('entries 必须是数组')
  rawEntries = registry.entries
} catch (error) {
  const reason = error instanceof Error ? error.message : String(error)
  failures.push('登记表不可读 ' + rel(CONFIG) + ': ' + reason)
}

const seenIds = new Set()
rawEntries.forEach((entry, index) => {
  const label = 'entries[' + index + ']'
  if (!isPlainObject(entry)) {
    failures.push(label + ': 必须是对象')
    return
  }
  for (const field of REQUIRED_FIELDS) {
    if (typeof entry[field] !== 'string' || entry[field].trim() === '') {
      failures.push(label + ': 缺少非空字符串字段 ' + field)
    }
  }
  const id = typeof entry.id === 'string' ? entry.id.trim() : ''
  if (id && !ID_PATTERN.test(id)) failures.push(label + ': id 需匹配 ' + ID_PATTERN + ' - ' + id)
  if (id && seenIds.has(id)) failures.push(label + ': id 重复 - ' + id)
  if (id) seenIds.add(id)
  if (typeof entry.kind === 'string' && !KINDS.includes(entry.kind)) {
    failures.push(label + (id ? ' (' + id + ')' : '') + ': kind 不在枚举内 - ' + entry.kind)
  }
  if (typeof entry.path === 'string' && entry.path.trim() !== '')
    checkPath(id || label, entry.path.trim())
  if (typeof entry.reason === 'string' && entry.reason.trim().length < MIN_REASON_LENGTH) {
    failures.push(
      label + (id ? ' (' + id + ')' : '') + ': reason 少于 ' + MIN_REASON_LENGTH + ' 字符',
    )
  }
  if (typeof entry.reviewBy === 'string' && entry.reviewBy.trim() !== '') {
    const reviewBy = entry.reviewBy.trim()
    if (!validDate(reviewBy)) {
      failures.push(
        label + (id ? ' (' + id + ')' : '') + ': reviewBy 不是合法 YYYY-MM-DD - ' + reviewBy,
      )
    } else if (reviewBy < today) {
      failures.push(
        label + (id ? ' (' + id + ')' : '') + ': reviewBy 已过期 - ' + reviewBy + ' < ' + today,
      )
    }
  }
})

const report = {
  ok: failures.length === 0,
  tool: 'check-exceptions',
  generatedAt: new Date().toISOString(),
  registry: rel(CONFIG),
  entries: rawEntries.length,
  kinds: KINDS,
  failures,
}

if (jsonOnly) {
  console.log(JSON.stringify(report, null, 2))
} else if (failures.length > 0) {
  console.error('check-exceptions: FAIL')
  for (const failure of failures) console.error('  x ' + failure)
} else {
  console.log(
    'check-exceptions: OK (' + rawEntries.length + ' entries; kinds: ' + KINDS.join(', ') + ')',
  )
}

if (failures.length > 0) process.exit(1)
