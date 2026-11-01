#!/usr/bin/env node
/**
 * Behavior coverage matrix checker (F4.2).
 *
 * Two matrices, one source of truth:
 *   tests/BEHAVIOR_MATRIX.md   human-readable behavior rows (346 rows: model tools,
 *                              model-facing addon commands, plan ops, HTTP routes and
 *                              fixed protocol/lifecycle/config/install/security/env rows).
 *   tests/BEHAVIOR_MATRIX.json machine-readable aspect matrices generated from the
 *                              executable gates: A1 tool matrix (15 tools x 7 aspects),
 *                              A5 route matrix (29 routes x 5 aspects) and the addon
 *                              command matrix (38 registered commands + ping = 39 wire).
 *
 * Live source of truth compared against both matrices:
 *   - model tools: every vTool registration under src (the schema-diagnostic duplicate
 *     is not a model-facing tool and is skipped)
 *   - addon commands: addon/dsh_blender_bridge/command_registry.py (38 registered + ping
 *     = 39 wire) and lib/runtime/catalog/index.js COMMAND_CATALOG (38 model-facing entries)
 *   - plan ops: lib/runtime/catalog/index.js PLAN_CATALOG (one row per op entry)
 *   - HTTP routes: CONTRACT_ROUTES from lib/runtime/http/routes/index.js
 *
 * Runtime sources are TypeScript, so this script imports the built lib/runtime JS
 * artifact and calls scripts/ensure-build.mjs first.
 *
 * Usage:
 *   node scripts/check-behavior-matrix.mjs                 # validate (same as --release)
 *   node scripts/check-behavior-matrix.mjs --release       # require planned=0
 *   node scripts/check-behavior-matrix.mjs --generate      # run A1/A5/command matrices,
 *                                                          # rewrite tests/BEHAVIOR_MATRIX.json,
 *                                                          # then validate everything
 *   node scripts/check-behavior-matrix.mjs --report <path> # write a deterministic JSON report
 *   node scripts/check-behavior-matrix.mjs --matrix <path> # validate another .md copy
 *   --python <exe>                                        # interpreter for the command matrix
 *
 * Exit 1 on any gap. --release also requires planned=0.
 */
import { spawnSync } from 'node:child_process'
import {
  existsSync,
  mkdirSync,
  mkdtempSync,
  readdirSync,
  readFileSync,
  renameSync,
  rmSync,
  writeFileSync,
} from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { ensureBuild } from './ensure-build.mjs'

const here = path.dirname(fileURLToPath(import.meta.url))
const ROOT = path.resolve(here, '..')
const MATRIX = path.join(ROOT, 'tests', 'BEHAVIOR_MATRIX.md')
const MATRIX_JSON = path.join(ROOT, 'tests', 'BEHAVIOR_MATRIX.json')
const MATRIX_JSON_SCHEMA = 'dsh-behavior-matrix/1'
const COLUMNS = [
  'id',
  'surface',
  'behavior',
  'contract source',
  'unit',
  'integration',
  'e2e',
  'status',
  'exception/owner',
]
const STATUSES = new Set(['covered', 'planned', 'exempt'])
const PLACEHOLDER = new Set(['', '—', '-', 'n/a', 'N/A'])
const TOOL_ASPECT_COUNT = 7
const ROUTE_ASPECT_COUNT = 5

const FIXED = {
  protocol: [
    'PROTO-HANDSHAKE',
    'PROTO-VERSION-MISMATCH',
    'PROTO-FRAME-BOUNDARY',
    'PROTO-PARTIAL-FRAME',
    'PROTO-DISCONNECT',
    'PROTO-TIMEOUT',
    'PROTO-ERROR-ENVELOPE',
    'PROTO-PAUSE',
  ],
  lifecycle: [
    'LIFECYCLE-START',
    'LIFECYCLE-STOP',
    'LIFECYCLE-RESTART',
    'LIFECYCLE-WATCHDOG',
    'LIFECYCLE-PORT-CONFLICT',
    'LIFECYCLE-CRASH',
    'LIFECYCLE-WORKER-TTL',
    'LIFECYCLE-UNINSTALL',
  ],
  config: [
    'CONFIG-DEFAULTS',
    'CONFIG-ENV',
    'CONFIG-FILE',
    'CONFIG-INVALID',
    'CONFIG-MISSING-DSH-SERVICES',
  ],
  install: [
    'INSTALL-NPM-PACK',
    'INSTALL-TARBALL',
    'INSTALL-ADDON-ZIP',
    'INSTALL-OLD-ADDON-REMOVAL',
    'INSTALL-RESTART-DSH',
    'INSTALL-DOCTOR',
  ],
  security: ['SEC-FILE-GUARD', 'SEC-ZIP-SLIP', 'SEC-SYMLINK', 'SEC-INJECTION', 'SEC-LEASE-BYPASS'],
  environment: ['ENV-BLENDER-42', 'ENV-BLENDER-5X', 'ENV-DSH-CURRENT', 'ENV-DSH-PREVIOUS'],
}

const failures = []
const fail = (message) => failures.push(message)
const rel = (file) => path.relative(ROOT, file).replaceAll(path.sep, '/')

function readTree(relative, extension) {
  const root = path.join(ROOT, relative)
  const out = []
  const walk = (dir) => {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, entry.name)
      if (entry.isDirectory()) walk(full)
      else if (entry.name.endsWith(extension)) out.push(full)
    }
  }
  if (existsSync(root)) walk(root)
  out.sort()
  return out
}

function splitRow(line) {
  let text = line.trim()
  if (text.startsWith('|')) text = text.slice(1)
  if (text.endsWith('|')) text = text.slice(0, -1)
  return text.split('|').map((cell) => cell.trim())
}

function loadMatrix(matrixPath = MATRIX) {
  if (!existsSync(matrixPath)) {
    fail('missing matrix: ' + rel(matrixPath))
    return []
  }
  const lines = readFileSync(matrixPath, 'utf8').replace(/\r\n/g, '\n').split('\n')
  const headerIndex = lines.findIndex((line) => line.trim().startsWith('| id |'))
  if (headerIndex < 0) {
    fail('matrix has no "| id |" table header')
    return []
  }
  const header = splitRow(lines[headerIndex])
  if (header.length !== COLUMNS.length || header.some((cell, index) => cell !== COLUMNS[index])) {
    fail('matrix header must be exactly: ' + COLUMNS.join(' | '))
    return []
  }
  const rows = []
  for (let index = headerIndex + 1; index < lines.length; index += 1) {
    const line = lines[index]
    if (line.trim() === '') {
      if (rows.length > 0) break
      continue
    }
    if (!line.trim().startsWith('|')) {
      if (rows.length > 0) break
      continue
    }
    if (/^\|[\s:|-]+\|$/.test(line.trim())) continue
    const cells = splitRow(line)
    if (cells.length !== COLUMNS.length) {
      fail('row ' + (index + 1) + ' has ' + cells.length + ' cells, expected ' + COLUMNS.length)
      continue
    }
    const row = Object.fromEntries(
      COLUMNS.map((column, columnIndex) => [column, cells[columnIndex]]),
    )
    row.line = index + 1
    rows.push(row)
  }
  return rows
}

async function loadCatalogs() {
  const catalog = await import(
    pathToFileURL(path.join(ROOT, 'lib', 'runtime', 'catalog', 'index.js')).href
  )
  const toolNames = []
  const walkTools = (dir) => {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, entry.name)
      if (entry.isDirectory()) walkTools(full)
      else if (entry.name.endsWith('.ts')) {
        const chunks = readFileSync(full, 'utf8').split('vTool({').slice(1)
        for (const chunk of chunks) {
          const match = chunk.match(/name:\s*'([^']+)'/)
          if (!match) continue
          if (chunk.slice(0, 3000).includes('仅诊断')) continue
          toolNames.push(match[1])
        }
      }
    }
  }
  walkTools(path.join(ROOT, 'src'))
  toolNames.sort()

  const commandCatalog = catalog.COMMAND_CATALOG || {}
  const planCatalog = catalog.PLAN_CATALOG || []
  const ops = []
  const families = new Set()
  for (const family of planCatalog) {
    families.add(family.f)
    for (const op of family.ops || []) ops.push({ family: family.f, op })
  }

  const routesModule = await import(
    pathToFileURL(path.join(ROOT, 'lib', 'runtime', 'http', 'routes', 'index.js')).href
  )
  const routes = Array.isArray(routesModule.CONTRACT_ROUTES) ? routesModule.CONTRACT_ROUTES : []
  if (routes.length === 0) fail('cannot find CONTRACT_ROUTES in lib/runtime/http/routes/index.js')

  const addonSource = readTree('addon/dsh_blender_bridge', '.py')
    .map((full) => readFileSync(full, 'utf8'))
    .join('\n')
  const protocolMatch = addonSource.match(/ADDON_PROTOCOL_VERSION\s*=\s*(\d+)/)
  const protocol = protocolMatch ? Number(protocolMatch[1]) : null
  const handshake = addonSource.slice(
    addonSource.indexOf('def get_addon_info'),
    addonSource.indexOf('def get_scene_info'),
  )
  const capabilityMatch = handshake.match(/"capabilities":\s*sorted\(\[([\s\S]*?)\]\)/)
  const capabilityCount = capabilityMatch ? (capabilityMatch[1].match(/"[^"]+"/g) || []).length : 0

  // F3.1: one command registry owns the wire table. Counts are explicit constants;
  // the matrix must agree with the registry, not with a hand-maintained list.
  const registeredMatch = addonSource.match(/REGISTERED_COMMAND_COUNT\s*=\s*(\d+)/)
  const wireMatch = addonSource.match(/WIRE_COMMAND_COUNT\s*=\s*(\d+)/)
  const rawCommandNames = [...addonSource.matchAll(/_command\(\s*"([^"]+)"/g)].map(
    (match) => match[1],
  )
  const wireCommands = [...new Set(rawCommandNames)].sort()
  const registeredCommands = wireCommands.filter((name) => name !== 'ping')

  return {
    toolNames,
    commandCatalog,
    planCatalog,
    ops,
    families,
    routes,
    addonSource,
    protocol,
    capabilityCount,
    addonRegistry: {
      registeredCount: registeredMatch ? Number(registeredMatch[1]) : null,
      wireCount: wireMatch ? Number(wireMatch[1]) : null,
      rawCommandNames,
      wireCommands,
      registeredCommands,
    },
  }
}

function expectedIds(catalogs) {
  const ids = []
  for (const name of catalogs.toolNames) ids.push('TOOL-' + name)
  for (const name of Object.keys(catalogs.commandCatalog)) ids.push('CMD-' + name)
  for (const entry of catalogs.ops) ids.push('OP-' + entry.family + '-' + entry.op)
  for (const route of catalogs.routes)
    ids.push('ROUTE-' + route.replace(/^\//, '').replaceAll('/', '-'))
  for (const list of Object.values(FIXED)) ids.push(...list)
  return ids
}

function validDate(value) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return false
  const parsed = new Date(value + 'T00:00:00Z')
  return !Number.isNaN(parsed.getTime()) && parsed.toISOString().slice(0, 10) === value
}

function checkTestRef(reference, row) {
  if (PLACEHOLDER.has(reference)) return false
  const separator = reference.indexOf('::')
  const file = separator >= 0 ? reference.slice(0, separator) : reference
  const title = separator >= 0 ? reference.slice(separator + 2) : ''
  if (!/\.(?:ts|mjs)$/.test(file)) {
    fail(row.id + ': test reference is not a .ts/.mjs file: ' + reference)
    return false
  }
  const absolute = path.resolve(ROOT, file)
  if (!absolute.startsWith(ROOT + path.sep)) {
    fail(row.id + ': test reference escapes the package: ' + reference)
    return false
  }
  if (!existsSync(absolute)) {
    fail(row.id + ': missing test file ' + file)
    return false
  }
  if (title) {
    const text = readFileSync(absolute, 'utf8').replace(/\r\n/g, '\n')
    // A title must be a real quoted test title, not just a substring in a comment.
    const quoted =
      text.includes("'" + title + "'") ||
      text.includes('"' + title + '"') ||
      text.includes('`' + title + '`')
    if (!quoted) {
      fail(
        row.id +
          ': ' +
          file +
          ' has no quoted test titled ' +
          JSON.stringify(title) +
          ' (comment-only matches are not evidence)',
      )
      return false
    }
  }
  return true
}

function checkExemption(row) {
  const value = row['exception/owner']
  const prefix = 'EXEMPT-' + row.id + ';'
  if (!value.startsWith(prefix)) {
    fail(row.id + ': exempt row must start with ' + JSON.stringify(prefix))
    return
  }
  const rest = value.slice(prefix.length)
  const fields = {}
  for (const part of rest.split(';')) {
    const separator = part.indexOf('=')
    if (separator < 0) continue
    fields[part.slice(0, separator).trim()] = part.slice(separator + 1).trim()
  }
  for (const field of ['issue', 'owner', 'reason']) {
    if (!fields[field]) fail(row.id + ': exempt row is missing ' + field + '=')
  }
  if (!fields.review || !validDate(fields.review)) {
    fail(row.id + ': exempt row needs review=YYYY-MM-DD')
  }
}

function checkAddonCommands(catalogs) {
  // F3: the single command_registry.py owns the command table; match its
  // _command("name" entries instead of the old server-side dict literals.
  for (const name of Object.keys(catalogs.commandCatalog)) {
    if (!catalogs.addonSource.includes('_command("' + name + '"')) {
      fail('COMMAND_CATALOG command ' + name + ' has no handler in addon/dsh_blender_bridge/')
    }
  }
  if (!catalogs.addonSource.includes('_command("ping"')) {
    fail('addon ping command is missing from command_registry.py')
  }
  if (catalogs.protocol !== 11) {
    fail('addon protocol version is ' + String(catalogs.protocol) + ', expected 11')
  }
  if (catalogs.capabilityCount !== 16) {
    fail('addon handshake advertises ' + catalogs.capabilityCount + ' capabilities, expected 16')
  }

  const registry = catalogs.addonRegistry
  if (registry.registeredCount === null) {
    fail('addon command_registry.py has no REGISTERED_COMMAND_COUNT')
  } else if (registry.registeredCommands.length !== registry.registeredCount) {
    fail(
      'addon registry declares ' +
        registry.registeredCount +
        ' registered commands but defines ' +
        registry.registeredCommands.length,
    )
  }
  if (registry.wireCount === null) {
    fail('addon command_registry.py has no WIRE_COMMAND_COUNT')
  } else if (registry.wireCommands.length !== registry.wireCount) {
    fail(
      'addon registry declares ' +
        registry.wireCount +
        ' wire commands but defines ' +
        registry.wireCommands.length,
    )
  }
  if (registry.rawCommandNames.length !== registry.wireCommands.length) {
    fail(
      'addon command_registry.py registers duplicate command names: ' +
        registry.rawCommandNames.length +
        ' definitions for ' +
        registry.wireCommands.length +
        ' unique names',
    )
  }
  if (!registry.wireCommands.includes('ping')) {
    fail('addon wire registry must contain the transport-level ping command')
  }
  const wireSet = new Set(registry.wireCommands)
  for (const name of Object.keys(catalogs.commandCatalog)) {
    if (!wireSet.has(name)) {
      fail('COMMAND_CATALOG command ' + name + ' is absent from the addon wire registry')
    }
  }
}

function parseArgs(argv) {
  const options = { release: false, report: null, matrix: MATRIX, generate: false, python: null }
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index]
    if (arg === '--release') options.release = true
    else if (arg === '--generate') options.generate = true
    else if (arg === '--report' || arg === '--matrix' || arg === '--python') {
      const value = argv[index + 1]
      if (!value || value.startsWith('--')) {
        console.error('check-behavior-matrix: ' + arg + ' needs a value')
        process.exit(2)
      }
      if (arg === '--report') options.report = value
      else if (arg === '--matrix') options.matrix = path.resolve(value)
      else options.python = value
      index += 1
    } else {
      console.error('check-behavior-matrix: unknown argument ' + arg)
      process.exit(2)
    }
  }
  return options
}

function runGate(command, args, env, label) {
  const result = spawnSync(command, args, {
    cwd: ROOT,
    env: { ...process.env, ...env },
    stdio: 'inherit',
  })
  if (result.error)
    fail(label + ' failed to start: ' + String(result.error.message || result.error))
  else if (result.status !== 0) fail(label + ' failed with exit code ' + String(result.status))
  return result.status === 0
}

function matrixCounts(rows) {
  const counts = { covered: 0, planned: 0, exempt: 0 }
  for (const row of rows) if (STATUSES.has(row.status)) counts[row.status] += 1
  return counts
}

function checkDeclaredCounts(matrixText, catalogs) {
  const line = matrixText
    .replace(/\r\n/g, '\n')
    .split('\n')
    .find((item) => item.startsWith('Counts (machine-checked'))
  if (!line) {
    fail('matrix has no "Counts (machine-checked ...)" header line')
    return
  }
  const expectedCounts = [
    catalogs.toolNames.length,
    catalogs.addonRegistry.registeredCommands.length,
    catalogs.addonRegistry.wireCommands.length,
    Object.keys(catalogs.commandCatalog).length,
    catalogs.families.size,
    catalogs.ops.length,
    catalogs.routes.length,
    catalogs.protocol,
  ]
  const countLabels = [
    'model tools',
    'registered addon commands',
    'wire',
    'model-facing catalog entries',
    'plan families',
    'op entries',
    'HTTP routes',
    'protocol',
  ]
  const numbers = (line.match(/[0-9]+/g) || []).map(Number)
  if (numbers.length !== expectedCounts.length) {
    fail('matrix header declares ' + numbers.length + ' counts, expected ' + expectedCounts.length)
  } else {
    for (let index = 0; index < expectedCounts.length; index += 1) {
      if (numbers[index] !== expectedCounts[index]) {
        fail(
          'matrix header declares ' +
            countLabels[index] +
            '=' +
            numbers[index] +
            ' but source says ' +
            expectedCounts[index],
        )
      }
    }
  }
  for (const label of countLabels) {
    if (!line.includes(label)) fail('matrix header does not name ' + label)
  }
}

const BS = String.fromCharCode(92)
const SOURCE_PATH = new RegExp(
  '(?:[A-Za-z0-9_.@*/-]+' +
    BS +
    '/)+[A-Za-z0-9_.@*/-]+(?::' +
    BS +
    'd+(?:-' +
    BS +
    'd+)?)?' +
    '|[A-Za-z0-9_.-]+' +
    BS +
    '.(?:ts|mjs|py|md|json|toml|yml|yaml)(?::' +
    BS +
    'd+(?:-' +
    BS +
    'd+)?)?',
  'g',
)
const ROOT_PATH =
  /^(src|runtime|addon|tests|scripts|docs|config|package.json|AGENTS.md|MIGRATIONS.md|CHANGELOG.md|README.md|pyproject.toml)/

function checkSourceText(label, text) {
  const seen = new Set()
  const tokens = text.match(SOURCE_PATH) || []
  for (let token of tokens) {
    token = token.replace(/[),.;]+$/, '')
    if (seen.has(token) || token.includes('*') || token.includes('$')) continue
    seen.add(token)
    const spec = token.match(new RegExp('^(.*?)(?::(' + BS + 'd+)(?:-(' + BS + 'd+))?)?$'))
    const file = spec[1]
    const line = spec[2] ? Number(spec[2]) : null
    if (!ROOT_PATH.test(file)) continue
    const absolute = path.resolve(ROOT, file)
    if (!absolute.startsWith(ROOT + path.sep) && absolute !== ROOT) {
      fail(label + ': contract source escapes the package: ' + file)
      continue
    }
    if (!existsSync(absolute)) {
      fail(label + ': contract source path does not exist: ' + file)
      continue
    }
    if (line !== null && fsIsFile(absolute)) {
      const lineCount = readFileSync(absolute, 'utf8').split('\n').length
      if (line < 1 || line > lineCount) {
        fail(
          label +
            ': contract source line is stale: ' +
            token +
            ' (file has ' +
            lineCount +
            ' lines)',
        )
      }
    }
  }
}

function fsIsFile(target) {
  try {
    return readFileSync(target, 'utf8') !== null
  } catch {
    return false
  }
}

function readMatrixHeaderText(matrixPath) {
  if (!existsSync(matrixPath)) return ''
  const lines = readFileSync(matrixPath, 'utf8').replace(/\r\n/g, '\n').split('\n')
  return lines.slice(0, 12).join('\n')
}

function loadMatrixJson() {
  if (!existsSync(MATRIX_JSON)) {
    fail(
      'missing machine-readable matrix tests/BEHAVIOR_MATRIX.json (regenerate with: node scripts/check-behavior-matrix.mjs --generate)',
    )
    return null
  }
  try {
    return JSON.parse(readFileSync(MATRIX_JSON, 'utf8'))
  } catch (error) {
    fail(
      'cannot parse tests/BEHAVIOR_MATRIX.json: ' +
        String(error && error.message ? error.message : error),
    )
    return null
  }
}

function uniqueSorted(values) {
  return [...new Set(values)].sort()
}

function checkAspectRows(kind, section, expectedKeys) {
  const label = 'BEHAVIOR_MATRIX.json ' + kind
  if (!section || typeof section !== 'object') {
    fail(label + ' is missing')
    return null
  }
  const keys = expectedKeys.map((value) => String(value))
  const keySet = new Set(keys)
  const count = section.count
  if (count !== keys.length)
    fail(label + '.count is ' + String(count) + ', source has ' + keys.length)
  const aspects = Array.isArray(section.aspects) ? section.aspects : []
  const aspectIds = aspects.map((aspect) => aspect && aspect.id)
  if (aspectIds.length !== TOOL_ASPECT_COUNT && kind === 'tools') {
    fail(label + ' has ' + aspectIds.length + ' aspects, expected ' + TOOL_ASPECT_COUNT)
  }
  if (aspectIds.length !== ROUTE_ASPECT_COUNT && kind === 'routes') {
    fail(label + ' has ' + aspectIds.length + ' aspects, expected ' + ROUTE_ASPECT_COUNT)
  }
  if (uniqueSorted(aspectIds).length !== aspectIds.length || aspectIds.some((id) => !id)) {
    fail(label + ' aspect ids must be unique non-empty strings')
  }
  for (const aspect of aspects) {
    if (!aspect || typeof aspect.title !== 'string' || typeof aspect.description !== 'string') {
      fail(label + ' aspect entries need id/title/description')
      break
    }
  }
  const rows = Array.isArray(section.rows) ? section.rows : []
  const cells = keys.length * aspectIds.length
  if (section.cells !== cells)
    fail(label + '.cells is ' + String(section.cells) + ', expected ' + cells)
  if (rows.length !== cells) fail(label + '.rows has ' + rows.length + ' rows, expected ' + cells)
  const seen = new Set()
  const summary = { pass: 0, notApplicable: 0 }
  for (const row of rows) {
    if (!row || typeof row !== 'object') {
      fail(label + ' has a non-object row')
      continue
    }
    const rowKey = row[kind === 'tools' ? 'tool' : 'route']
    if (!keySet.has(String(rowKey)))
      fail(label + ' row key is not a source item: ' + String(rowKey))
    if (!aspectIds.includes(row.aspect))
      fail(label + ' row has unknown aspect: ' + String(row.aspect))
    const pair = String(rowKey) + '|' + String(row.aspect)
    if (seen.has(pair))
      fail(label + ' has duplicate cell ' + String(rowKey) + '/' + String(row.aspect))
    seen.add(pair)
    if (row.status === 'PASS') {
      summary.pass += 1
      if (typeof row.evidence !== 'string' || row.evidence.trim() === '') {
        fail(label + ' PASS row has empty evidence: ' + String(rowKey) + '/' + String(row.aspect))
      }
    } else if (row.status === 'N/A') {
      summary.notApplicable += 1
      if (typeof row.reason !== 'string' || row.reason.trim() === '') {
        fail(label + ' N/A row has no reason: ' + String(rowKey) + '/' + String(row.aspect))
      }
    } else {
      fail(label + ' row has invalid status ' + String(row.status))
    }
  }
  if (seen.size !== cells) fail(label + ' does not cover every cell exactly once')
  const declared = section.summary || {}
  if (declared.total !== cells)
    fail(label + '.summary.total is ' + String(declared.total) + ', expected ' + cells)
  if (declared.pass !== summary.pass) {
    fail(label + '.summary.pass is ' + String(declared.pass) + ', rows say ' + summary.pass)
  }
  if (declared.notApplicable !== summary.notApplicable) {
    fail(
      label +
        '.summary.notApplicable is ' +
        String(declared.notApplicable) +
        ', rows say ' +
        summary.notApplicable,
    )
  }
  return {
    count: keys.length,
    aspects: aspectIds.length,
    cells,
    pass: summary.pass,
    notApplicable: summary.notApplicable,
  }
}

function checkCommandMatrix(section, catalogs) {
  const label = 'BEHAVIOR_MATRIX.json commands'
  const registry = catalogs.addonRegistry
  if (!section || typeof section !== 'object') {
    fail(label + ' is missing')
    return null
  }
  if (section.registered !== registry.registeredCommands.length) {
    fail(
      label +
        '.registered is ' +
        String(section.registered) +
        ', addon registry has ' +
        registry.registeredCommands.length,
    )
  }
  if (section.wire !== registry.wireCommands.length) {
    fail(
      label +
        '.wire is ' +
        String(section.wire) +
        ', addon registry has ' +
        registry.wireCommands.length,
    )
  }
  const catalogNames = Object.keys(catalogs.commandCatalog).sort()
  if (section.catalog !== catalogNames.length) {
    fail(
      label +
        '.catalog is ' +
        String(section.catalog) +
        ', COMMAND_CATALOG has ' +
        catalogNames.length,
    )
  }
  const wireSet = new Set(registry.wireCommands)
  const declaredWire = Array.isArray(section.wireCommands)
    ? section.wireCommands.slice().sort()
    : []
  if (
    declaredWire.length !== registry.wireCommands.length ||
    uniqueSorted(declaredWire).length !== declaredWire.length
  ) {
    fail(label + '.wireCommands must list every wire command exactly once')
  } else if (declaredWire.some((name, index) => name !== registry.wireCommands[index])) {
    fail(label + '.wireCommands does not match the addon wire registry')
  }
  for (const name of catalogNames)
    if (!wireSet.has(name)) fail(label + ' catalog command absent from wire registry: ' + name)
  const registeredSet = new Set(registry.registeredCommands)
  const catalogSet = new Set(catalogNames)
  const catalogOnly = uniqueSorted(catalogNames.filter((name) => !registeredSet.has(name)))
  const registryOnly = uniqueSorted(
    registry.registeredCommands.filter((name) => !catalogSet.has(name)),
  )
  if (JSON.stringify(section.catalogOnly || []) !== JSON.stringify(catalogOnly)) {
    fail(label + '.catalogOnly does not match source (expected ' + catalogOnly.join(',') + ')')
  }
  if (JSON.stringify(section.registryOnly || []) !== JSON.stringify(registryOnly)) {
    fail(label + '.registryOnly does not match source (expected ' + registryOnly.join(',') + ')')
  }
  const rows = Array.isArray(section.rows) ? section.rows : []
  if (rows.length !== registry.registeredCommands.length) {
    fail(
      label + '.rows has ' + rows.length + ' rows, expected ' + registry.registeredCommands.length,
    )
  }
  const seen = new Set()
  for (const row of rows) {
    if (!row || typeof row !== 'object') {
      fail(label + ' has a non-object row')
      continue
    }
    if (!registeredSet.has(row.command))
      fail(label + ' row command is not registered: ' + String(row.command))
    if (seen.has(row.command)) fail(label + ' duplicate command row: ' + String(row.command))
    seen.add(row.command)
    if (!row.params || typeof row.params !== 'object') {
      fail(label + ' row needs a params object: ' + String(row.command))
    }
    if (typeof row.envelope !== 'string' || row.envelope.trim() === '') {
      fail(label + ' row needs an envelope description: ' + String(row.command))
    }
    const expectedCatalog = catalogSet.has(row.command) ? 'catalog' : 'off-catalog'
    if (row.catalog !== expectedCatalog) {
      fail(
        label +
          ' row ' +
          String(row.command) +
          ' catalog marker is ' +
          String(row.catalog) +
          ', expected ' +
          expectedCatalog,
      )
    }
  }
  if (seen.size !== registry.registeredCommands.length) {
    fail(label + ' does not list every registered command exactly once')
  }
  return {
    registered: registry.registeredCommands.length,
    wire: registry.wireCommands.length,
    catalog: catalogNames.length,
    rows: rows.length,
  }
}

function validateMatrixJson(json, catalogs, rows, counts, matrixPath) {
  if (!json) return null
  if (json.schema !== MATRIX_JSON_SCHEMA) {
    fail(
      'BEHAVIOR_MATRIX.json schema is ' +
        JSON.stringify(json.schema) +
        ', expected ' +
        MATRIX_JSON_SCHEMA,
    )
  }
  if (typeof json.generatedAt !== 'string' || Number.isNaN(new Date(json.generatedAt).getTime())) {
    fail('BEHAVIOR_MATRIX.json generatedAt is not an ISO timestamp')
  }
  if (typeof json.node !== 'string' || !json.node.startsWith('v') || !json.node.includes('.')) {
    fail('BEHAVIOR_MATRIX.json node is not a node version')
  }
  const tools = checkAspectRows('tools', json.tools, catalogs.toolNames)
  const routes = checkAspectRows('routes', json.routes, catalogs.routes)
  if (json.routes && Array.isArray(json.routes.contractRoutes)) {
    const declared = uniqueSorted(json.routes.contractRoutes)
    const expected = uniqueSorted(catalogs.routes)
    if (
      declared.length !== expected.length ||
      declared.some((route, index) => route !== expected[index])
    ) {
      fail('BEHAVIOR_MATRIX.json routes.contractRoutes does not match CONTRACT_ROUTES')
    }
  } else if (json.routes) {
    fail('BEHAVIOR_MATRIX.json routes.contractRoutes is missing')
  }
  const routeSummary = (json.routes && json.routes.summary) || {}
  if (routeSummary.invalidRequests !== catalogs.routes.length) {
    fail(
      'BEHAVIOR_MATRIX.json routes.summary.invalidRequests is ' +
        String(routeSummary.invalidRequests) +
        ', expected ' +
        catalogs.routes.length,
    )
  }
  if (routeSummary.invalidRequestsProducing500 !== 0) {
    fail('BEHAVIOR_MATRIX.json routes.summary.invalidRequestsProducing500 must be 0')
  }
  const commands = checkCommandMatrix(json.commands, catalogs)
  const behavior = json.behaviorMatrix || {}
  if (matrixPath === MATRIX) {
    if (behavior.total !== rows.length) {
      fail(
        'BEHAVIOR_MATRIX.json behaviorMatrix.total is ' +
          String(behavior.total) +
          ', matrix has ' +
          rows.length,
      )
    }
    for (const status of ['covered', 'planned', 'exempt']) {
      if (behavior[status] !== counts[status]) {
        fail(
          'BEHAVIOR_MATRIX.json behaviorMatrix.' +
            status +
            ' is ' +
            String(behavior[status]) +
            ', matrix has ' +
            counts[status],
        )
      }
    }
    if (behavior.planned !== 0) fail('BEHAVIOR_MATRIX.json behaviorMatrix.planned must be 0')
  }
  return { tools, routes, commands }
}

async function generateMatrix(catalogs, rows, counts, python) {
  const directory = mkdtempSync(path.join(os.tmpdir(), 'dsh-behavior-matrix-'))
  const a1 = path.join(directory, 'a1.json')
  const a5 = path.join(directory, 'a5.json')
  const commandMarkdown = path.join(directory, 'commands.md')
  const vitest = path.join(ROOT, 'node_modules', 'vitest', 'vitest.mjs')
  if (!existsSync(vitest)) {
    fail('cannot generate matrix: ' + rel(vitest) + ' is missing (run npm ci first)')
    return null
  }
  try {
    if (
      !runGate(
        process.execPath,
        [vitest, 'run', '--configLoader', 'runner', 'tests/unit/js/contract/tool-matrix.test.ts'],
        { A1_MATRIX_OUT: a1 },
        'A1 tool matrix',
      )
    )
      return null
    if (
      !runGate(
        process.execPath,
        [vitest, 'run', '--configLoader', 'runner', 'tests/integration/js/route-matrix.test.ts'],
        { A5_MATRIX_OUT: a5 },
        'A5 route matrix',
      )
    )
      return null
    if (
      !runGate(
        python,
        ['-m', 'pytest', 'tests/integration/python/test_addon_command_matrix.py', '-q'],
        { DSHBB_COMMAND_MATRIX_OUT: commandMarkdown },
        'addon command matrix',
      )
    )
      return null
    const toolMatrix = JSON.parse(readFileSync(a1, 'utf8'))
    const routeMatrix = JSON.parse(readFileSync(a5, 'utf8'))
    const commandJson = path.join(directory, 'commands.json')
    if (!existsSync(commandJson)) {
      fail('addon command matrix did not write ' + rel(commandJson))
      return null
    }
    const commandMatrix = JSON.parse(readFileSync(commandJson, 'utf8'))
    const registry = catalogs.addonRegistry
    const catalogNames = Object.keys(catalogs.commandCatalog).sort()
    const registeredSet = new Set(registry.registeredCommands)
    const catalogSet = new Set(catalogNames)
    const report = {
      schema: MATRIX_JSON_SCHEMA,
      generatedAt: new Date().toISOString(),
      node: process.version,
      pluginVersion: toolMatrix.pluginVersion ?? null,
      generatedBy: 'node scripts/check-behavior-matrix.mjs --generate',
      generators: {
        tools: 'tests/unit/js/contract/tool-matrix.test.ts (A1_MATRIX_OUT)',
        routes: 'tests/integration/js/route-matrix.test.ts (A5_MATRIX_OUT)',
        commands:
          'tests/integration/python/test_addon_command_matrix.py (DSHBB_COMMAND_MATRIX_OUT)',
      },
      tools: {
        count: toolMatrix.tools,
        aspects: toolMatrix.aspects,
        cells: toolMatrix.summary.total,
        summary: toolMatrix.summary,
        rows: toolMatrix.rows,
      },
      routes: {
        count: routeMatrix.routes,
        contractRoutes: routeMatrix.contractRoutes,
        aspects: routeMatrix.aspects,
        cells: routeMatrix.summary.total,
        summary: routeMatrix.summary,
        rows: routeMatrix.rows,
      },
      commands: {
        registered: registry.registeredCommands.length,
        wire: registry.wireCommands.length,
        catalog: catalogNames.length,
        wireCommands: registry.wireCommands,
        catalogOnly: uniqueSorted(catalogNames.filter((name) => !registeredSet.has(name))),
        registryOnly: uniqueSorted(
          registry.registeredCommands.filter((name) => !catalogSet.has(name)),
        ),
        rows: commandMatrix.commands,
      },
      behaviorMatrix: {
        total: rows.length,
        covered: counts.covered,
        planned: counts.planned,
        exempt: counts.exempt,
      },
    }
    const temporary = path.join(ROOT, 'tests', 'BEHAVIOR_MATRIX.generated.json')
    writeFileSync(temporary, JSON.stringify(report, null, 2) + '\n')
    const prettier = path.join(ROOT, 'node_modules', 'prettier', 'bin', 'prettier.cjs')
    if (existsSync(prettier)) {
      const formatted = spawnSync(process.execPath, [prettier, '--write', temporary], {
        cwd: ROOT,
        stdio: 'ignore',
      })
      if (formatted.status !== 0)
        console.warn('check-behavior-matrix: prettier could not format ' + rel(MATRIX_JSON))
    }
    rmSync(MATRIX_JSON + '.tmp', { force: true })
    renameSync(temporary, MATRIX_JSON)
    console.log('wrote ' + rel(MATRIX_JSON) + ' (' + rows.length + ' behavior rows)')
    return report
  } finally {
    rmSync(directory, { recursive: true, force: true })
    rmSync(path.join(ROOT, 'tests', 'BEHAVIOR_MATRIX.generated.json'), { force: true })
  }
}

async function main() {
  const options = parseArgs(process.argv.slice(2))
  if (options.generate && options.matrix !== MATRIX) {
    console.error('check-behavior-matrix: --generate always validates tests/BEHAVIOR_MATRIX.md')
    process.exit(2)
  }
  const python =
    options.python || process.env.PYTHON || (process.platform === 'win32' ? 'python' : 'python3')
  ensureBuild()
  const catalogs = await loadCatalogs()
  const rows = loadMatrix(options.matrix)
  const counts = matrixCounts(rows)
  checkDeclaredCounts(readFileSync(options.matrix, 'utf8'), catalogs)
  checkSourceText('matrix header', readMatrixHeaderText(options.matrix))
  for (const row of rows) checkSourceText(row.id, row['contract source'])
  if (options.generate) {
    await generateMatrix(catalogs, rows, counts, python)
  }
  const json = loadMatrixJson()
  const jsonSummary = validateMatrixJson(json, catalogs, rows, counts, options.matrix)

  const expected = expectedIds(catalogs)
  const expectedSet = new Set(expected)
  const seen = new Set()

  if (expected.length !== expectedSet.size) {
    const duplicates = expected.filter((id, index) => expected.indexOf(id) !== index)
    fail('source of truth produces duplicate ids: ' + [...new Set(duplicates)].join(', '))
  }

  for (const row of rows) {
    if (seen.has(row.id)) fail(row.id + ': duplicate matrix id')
    seen.add(row.id)
    if (!expectedSet.has(row.id)) fail(row.id + ': row has no source-of-truth counterpart')
    if (!STATUSES.has(row.status)) {
      fail(row.id + ': invalid status ' + JSON.stringify(row.status))
      continue
    }
    if (row.status === 'covered') {
      const e2eRef = /[.](?:ts|mjs)(?:::.*)?$/.test(row.e2e.trim()) ? [row.e2e] : []
      const refs = [row.unit, row.integration, ...e2eRef]
        .flatMap((cell) => cell.split(';'))
        .map((value) => value.trim())
      const real = refs.filter((value) => !PLACEHOLDER.has(value))
      if (real.length === 0) {
        fail(row.id + ': covered row has no unit/integration/e2e test reference')
      }
      let valid = false
      for (const reference of real) {
        if (checkTestRef(reference, row)) valid = true
      }
      if (real.length > 0 && !valid) fail(row.id + ': covered row has no valid test reference')
      if (!PLACEHOLDER.has(row['exception/owner'])) {
        fail(row.id + ': covered row must not carry an exemption')
      }
    } else if (row.status === 'exempt') {
      checkExemption(row)
    } else if (row['exception/owner'] !== '' && row['exception/owner'] !== '—') {
      fail(row.id + ': planned row should use — in exception/owner')
    }
  }

  for (const id of expectedSet) {
    if (!seen.has(id)) fail(id + ': source-of-truth item has no matrix row')
  }

  if (options.release && counts.planned > 0) {
    fail('release gate: ' + counts.planned + ' planned row(s) remain')
  }
  checkAddonCommands(catalogs)

  const report = {
    ok: failures.length === 0,
    release: options.release,
    generated: options.generate,
    matrix: rel(options.matrix),
    matrixJson: rel(MATRIX_JSON),
    schema: MATRIX_JSON_SCHEMA,
    sources: {
      tools: 'src/**/*.ts vTool registrations',
      commands: 'addon/dsh_blender_bridge/command_registry.py + runtime/catalog/commands.ts',
      ops: 'runtime/catalog/data.ts PLAN_CATALOG',
      routes: 'runtime/http/routes/index.ts CONTRACT_ROUTES',
    },
    counts: {
      tools: catalogs.toolNames.length,
      commands: Object.keys(catalogs.commandCatalog).length,
      commandsRegistered: catalogs.addonRegistry.registeredCommands.length,
      commandsWire: catalogs.addonRegistry.wireCommands.length,
      commandsCatalogOnly: uniqueSorted(
        Object.keys(catalogs.commandCatalog).filter(
          (name) => !catalogs.addonRegistry.registeredCommands.includes(name),
        ),
      ),
      commandsRegistryOnly: uniqueSorted(
        catalogs.addonRegistry.registeredCommands.filter(
          (name) => !Object.keys(catalogs.commandCatalog).includes(name),
        ),
      ),
      families: catalogs.families.size,
      ops: catalogs.ops.length,
      routes: catalogs.routes.length,
      protocol: catalogs.protocol,
      protocolRows: FIXED.protocol.length,
      lifecycle: FIXED.lifecycle.length,
      config: FIXED.config.length,
      install: FIXED.install.length,
      security: FIXED.security.length,
      environment: FIXED.environment.length,
      total: rows.length,
      expectedTotal: expectedSet.size,
      covered: counts.covered,
      planned: counts.planned,
      exempt: counts.exempt,
    },
    toolMatrix: jsonSummary && jsonSummary.tools ? jsonSummary.tools : null,
    routeMatrix: jsonSummary && jsonSummary.routes ? jsonSummary.routes : null,
    commandMatrix: jsonSummary && jsonSummary.commands ? jsonSummary.commands : null,
    addon: {
      protocol: catalogs.protocol,
      capabilities: catalogs.capabilityCount,
      registeredCommands: catalogs.addonRegistry.registeredCount,
      wireCommands: catalogs.addonRegistry.wireCount,
    },
    failures,
  }

  if (options.report) {
    const target = path.resolve(ROOT, options.report)
    mkdirSync(path.dirname(target), { recursive: true })
    writeFileSync(target, JSON.stringify(report, null, 2) + '\n')
  }

  const summary =
    'behavior matrix: ' +
    rows.length +
    ' rows (' +
    counts.covered +
    ' covered, ' +
    counts.planned +
    ' planned, ' +
    counts.exempt +
    ' exempt) · source ' +
    catalogs.toolNames.length +
    ' tools / ' +
    Object.keys(catalogs.commandCatalog).length +
    ' commands (' +
    catalogs.addonRegistry.registeredCommands.length +
    ' registered + ping = ' +
    catalogs.addonRegistry.wireCommands.length +
    ' wire) / ' +
    catalogs.families.size +
    ' families / ' +
    catalogs.ops.length +
    ' ops / ' +
    catalogs.routes.length +
    ' routes'
  if (failures.length === 0) {
    console.log('OK ' + summary)
    process.exit(0)
  }
  console.error('FAIL ' + summary)
  for (const message of failures) console.error('- ' + message)
  process.exit(1)
}

main().catch((error) => {
  console.error('check-behavior-matrix: ' + (error && error.stack ? error.stack : String(error)))
  process.exit(1)
})
