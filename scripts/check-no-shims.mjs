#!/usr/bin/env node
// 公共 facade 登记与中间层收口门禁：每个导出都要有真实消费者，退役中间层不得复活。
/**
 * 两条断言：
 *
 *  1. 公共 facade 只有两个（src/index.ts、runtime/engine.ts）。它们的导出集合必须与
 *     config/facades.json **完全一致**：每个导出都登记了至少一个仓库内消费者（文件存在
 *     且出现该名字），登记表里也不能留下已经不存在的导出。新增一个未登记导出即失败 ——
 *     想加导出，先把消费者写出来并登记；内部代码要用某能力，直接 import 归属模块，别穿 facade。
 *
 *  2. 其余"中间层"只能是单行再导出：纯再导出且只有一条语句的文件放行（例如
 *     src/tools/<tool>/index.ts 的 export { registerX } from './register.js'）；多条语句的
 *     再导出 barrel 必须在 config/exceptions.json 里登记 kind='single-line-reexport'
 *     （不再维护脚本内联白名单）。已退役文件登记在 config/facades.json 的 retired。
 *
 * 用法：node scripts/check-no-shims.mjs（npm run check:no-shims）
 */
import { existsSync, readFileSync, readdirSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const FACADE_REGISTRY = 'config/facades.json'
const EXCEPTIONS = 'config/exceptions.json'
const ALLOWED_FACADES = ['src/index.ts', 'runtime/engine.ts']
const failures = []

const rel = (p) => path.relative(ROOT, p).split(path.sep).join('/')
const full = (p) => path.join(ROOT, ...String(p).split('/'))
const readText = (p) => readFileSync(full(p), 'utf8')
const norm = (p) => String(p).replace(/\\/g, '/')
const escapeRe = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
const mentions = (text, name) =>
  new RegExp('(^|[^A-Za-z0-9_$])' + escapeRe(name) + '([^A-Za-z0-9_$]|$)').test(text)

function readJson(relPath) {
  try {
    return JSON.parse(readText(relPath))
  } catch (error) {
    failures.push(
      relPath + ': 无法读取或不是合法 JSON - ' + String((error && error.message) || error),
    )
    return null
  }
}

function stripComments(text) {
  return text.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/[^\n]*/g, '')
}

const EXPORT_FROM_RE = /export\s+(?:type\s+)?(?:\{[\s\S]*?\}|\*)\s+from\s+['"][^'"]+['"]\s*;?/g

/** 纯再导出判定：去掉注释后只剩 export ... from 语句。 */
function reexportShape(text) {
  const stripped = stripComments(text)
  const statements = (stripped.match(EXPORT_FROM_RE) || []).length
  const rest = stripped.replace(EXPORT_FROM_RE, '').trim()
  return { statements: statements, pure: statements > 0 && rest === '' }
}

/** 解析 facade 的具名导出（本仓 facade 只允许 export { … } from 形式）。 */
function facadeExports(relPath) {
  const stripped = stripComments(readText(relPath))
  const names = []
  const re = /export\s+(?:type\s+)?\{([\s\S]*?)\}\s+from\s+['"][^'"]+['"]/g
  let match
  while ((match = re.exec(stripped))) {
    for (const raw of match[1].split(',')) {
      const part = raw.trim()
      if (!part) continue
      const alias = /\bas\s+([A-Za-z_$][A-Za-z0-9_$]*)$/.exec(part)
      names.push(alias ? alias[1] : part.split(/\s+/)[0])
    }
  }
  return {
    names: names,
    wildcard: /export\s+\*\s+from/.test(stripped),
    localExport:
      /^\s*export\s+(?:default|const|let|var|function|class|interface|enum|namespace)\b/m.test(
        stripped,
      ),
  }
}

function walkSources(dir, out) {
  if (!existsSync(dir)) return out
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    if (entry.name === 'node_modules' || entry.name === 'lib' || entry.name === '.git') continue
    const p = path.join(dir, entry.name)
    if (entry.isDirectory()) walkSources(p, out)
    else if (
      /\.(ts|mts)$/.test(entry.name) &&
      !entry.name.endsWith('.d.ts') &&
      !/\.generated\.ts$/.test(entry.name)
    ) {
      out.push(p)
    }
  }
  return out
}

// ---------- 1. 扁平 runtime/*.py shim ----------
const runtimePy = readdirSync(path.join(ROOT, 'runtime')).filter((f) => f.endsWith('.py'))
if (runtimePy.length) failures.push('flat runtime/*.py files: ' + runtimePy.join(', '))

// ---------- 2. 退役文件不得复活 ----------
const registry = readJson(FACADE_REGISTRY)
const retired = registry && Array.isArray(registry.retired) ? registry.retired : []
if (registry && !Array.isArray(registry.retired)) {
  failures.push(FACADE_REGISTRY + ': retired 必须是数组')
}
for (const retiredPath of retired) {
  if (typeof retiredPath !== 'string' || !retiredPath) {
    failures.push(FACADE_REGISTRY + ': retired 条目必须是非空字符串')
    continue
  }
  if (existsSync(full(retiredPath))) failures.push('retired file still present: ' + retiredPath)
}

// ---------- 3. facade 登记：导出必须登记、登记必须有消费者 ----------
const facades =
  registry && registry.facades && typeof registry.facades === 'object' ? registry.facades : {}
if (registry && (!facades || Array.isArray(facades))) {
  failures.push(FACADE_REGISTRY + ': facades 必须是对象')
}
const listedFacades = Object.keys(facades).sort()
const allowedSorted = [...ALLOWED_FACADES].sort()
if (JSON.stringify(listedFacades) !== JSON.stringify(allowedSorted)) {
  failures.push(
    'facade 集合必须是 ' +
      allowedSorted.join(', ') +
      '（实际：' +
      (listedFacades.join(', ') || '空') +
      '）',
  )
}
for (const relPath of ALLOWED_FACADES) {
  const entry = facades[relPath]
  if (!entry || typeof entry !== 'object' || !Array.isArray(entry.exports)) {
    failures.push(relPath + ': 未在 ' + FACADE_REGISTRY + ' 登记（需要 exports 数组）')
    continue
  }
  if (!existsSync(full(relPath))) {
    failures.push(relPath + ': 登记为 facade，但文件不存在')
    continue
  }
  const actual = facadeExports(relPath)
  if (actual.wildcard || actual.localExport) {
    failures.push(
      relPath + ': facade 只允许 export { … } from 具名再导出（不允许 export * / 本地实现）',
    )
  }
  const registered = []
  for (const item of entry.exports) {
    if (!item || typeof item.name !== 'string' || !item.name) {
      failures.push(relPath + ': exports 条目缺少非空 name')
      continue
    }
    if (registered.includes(item.name)) {
      failures.push(relPath + ': 导出重复登记 - ' + item.name)
      continue
    }
    registered.push(item.name)
    if (!Array.isArray(item.consumers) || item.consumers.length === 0) {
      failures.push(relPath + ': 导出 ' + item.name + ' 未登记消费者（consumers 不能为空）')
      continue
    }
    for (const consumer of item.consumers) {
      if (typeof consumer !== 'string' || !consumer) {
        failures.push(relPath + ': 导出 ' + item.name + ' 的消费者路径必须是非空字符串')
        continue
      }
      if (!existsSync(full(consumer))) {
        failures.push(relPath + ': 导出 ' + item.name + ' 的消费者不存在 - ' + consumer)
        continue
      }
      if (!mentions(readText(consumer), item.name)) {
        failures.push(relPath + ': 导出 ' + item.name + ' 的消费者已不再引用该名字 - ' + consumer)
      }
    }
  }
  const missing = registered.filter((name) => !actual.names.includes(name))
  for (const name of missing) {
    failures.push(relPath + ': 登记表里的导出已不存在（请同步删除）- ' + name)
  }
  const unregistered = actual.names.filter((name) => !registered.includes(name))
  for (const name of unregistered) {
    failures.push(
      relPath +
        ': 未登记的 facade 导出 - ' +
        name +
        '（先在 config/facades.json 登记消费者，再提交）',
    )
  }
  const duplicates = actual.names.filter((name, i) => actual.names.indexOf(name) !== i)
  if (duplicates.length)
    failures.push(relPath + ': 导出源码内有重复名字 - ' + duplicates.join(', '))
}

// ---------- 4. 中间层只能是单行再导出；多语句 barrel 走例外登记 ----------
const exceptions = readJson(EXCEPTIONS)
const entries = exceptions && Array.isArray(exceptions.entries) ? exceptions.entries : []
const singleLineExceptions = new Set()
for (const item of entries) {
  if (!item || typeof item !== 'object') continue
  if (item.kind === 'single-line-reexport') {
    if (typeof item.path !== 'string' || !item.path) {
      failures.push(EXCEPTIONS + ': single-line-reexport 条目缺少 path')
      continue
    }
    singleLineExceptions.add(norm(item.path))
    const p = full(item.path)
    if (!existsSync(p)) {
      failures.push('single-line-reexport 例外指向不存在的文件 - ' + item.path)
      continue
    }
    if (ALLOWED_FACADES.includes(norm(item.path))) {
      failures.push('single-line-reexport 例外不能是公共 facade - ' + item.path)
      continue
    }
    const shape = reexportShape(readFileSync(p, 'utf8'))
    if (!shape.pure)
      failures.push('single-line-reexport 例外不再成立（文件已有实现代码）- ' + item.path)
  } else if (item.kind === 'facade-export') {
    if (typeof item.path !== 'string' || !ALLOWED_FACADES.includes(norm(item.path))) {
      failures.push('facade-export 例外必须指向已登记的公共 facade - ' + String(item.path))
    }
  }
}

const sources = [
  ...walkSources(path.join(ROOT, 'src'), []),
  ...walkSources(path.join(ROOT, 'runtime'), []),
]
for (const file of sources) {
  const relPath = rel(file)
  if (ALLOWED_FACADES.includes(relPath)) continue
  const shape = reexportShape(readFileSync(file, 'utf8'))
  if (!shape.pure) continue
  if (shape.statements <= 1) continue // 单行再导出：允许
  if (singleLineExceptions.has(relPath)) continue
  failures.push(
    '多语句再导出中间层未登记例外（config/exceptions.json kind=single-line-reexport）：' + relPath,
  )
}

// ---------- 5. 已迁移的 server 入口不得在运行代码/脚本/配置里复活 ----------
const forbiddenTopology = [
  ['runtime/server.ts', 'runtime/server'],
  ['runtime/server.js', 'lib/runtime/server.js'],
  ['SERVER_PATH', 'SERVER_PATH'],
  ['resolveServerPath', 'resolveServerPath'],
]
for (const [needle, label] of forbiddenTopology) {
  for (const file of sources) {
    if (file.endsWith('.d.ts') || /\.generated\.ts$/.test(file)) continue
    if (readFileSync(file, 'utf8').includes(needle)) {
      failures.push('已退役的 server 入口引用（' + label + '）：' + rel(file))
    }
  }
}
for (const extra of [
  'scripts/check-build.mjs',
  'scripts/ensure-build.mjs',
  'scripts/install-smoke.mjs',
  'package.json',
]) {
  if (!existsSync(full(extra))) continue
  if (readFileSync(full(extra), 'utf8').includes('runtime/server')) {
    failures.push('已退役的 server 入口引用：' + extra)
  }
}

if (failures.length) {
  console.error('check-no-shims: FAIL')
  for (const failure of failures) console.error('  x ' + failure)
  process.exit(1)
}
const registeredCount =
  (facades['src/index.ts'] ? facades['src/index.ts'].exports.length : 0) +
  (facades['runtime/engine.ts'] ? facades['runtime/engine.ts'].exports.length : 0)
console.log(
  'check-no-shims: PASS (' +
    ALLOWED_FACADES.length +
    ' facades / ' +
    String(registeredCount) +
    ' registered exports; ' +
    String(singleLineExceptions.size) +
    ' sanctioned re-export barrels; no flat shims, no retired files)',
)
