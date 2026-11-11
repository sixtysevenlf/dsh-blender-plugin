#!/usr/bin/env node
/**
 * scripts/check-tsconfig-layout.mjs — machine-check the TypeScript project layout.
 *
 * Rules:
 *   R1 coverage   every tracked .ts/.mts/.cts file (except .d.ts) belongs to exactly one project.
 *                  Whitelist: root vitest.config.ts, a fixed-location tool entry that imports
 *                  vitest/config and deliberately belongs to no runtime or test project.
 *   R2 existence  the four projects exist and parse; the repository root has no tsconfig.*.json.
 *   R3 references build.mjs TARGETS.*.config, package.json typecheck -p targets, ensure-build
 *                  WATCH_FILES and check-doc-paths ROOT_FILES point at files that exist.
 *   R4 old names  live files do not mention tsconfig.runtime.json / tsconfig.test.json /
 *                  tsconfig.scripts.json. CHANGELOG.md, MIGRATIONS.md, openspec/** and this
 *                  checker itself (the rule table lives here) are exempt as history.
 *
 * Usage: node scripts/check-tsconfig-layout.mjs [--root <dir>]
 */
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import ts from 'typescript'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const DEFAULT_ROOT = path.resolve(HERE, '..')

const argv = process.argv.slice(2)
const rootIndex = argv.indexOf('--root')
if (rootIndex >= 0 && (!argv[rootIndex + 1] || argv[rootIndex + 1].startsWith('--'))) {
  console.error('check-tsconfig-layout: --root needs a directory')
  process.exit(2)
}
const ROOT = rootIndex >= 0 ? path.resolve(argv[rootIndex + 1]) : DEFAULT_ROOT

const PROJECTS = [
  { name: 'host', config: 'tsconfig.json' },
  { name: 'runtime', config: 'runtime/tsconfig.json' },
  { name: 'tests', config: 'tests/tsconfig.json' },
  { name: 'scripts', config: 'scripts/tsconfig.json' },
]

const SOURCE_FILE = /\.(?:ts|mts|cts)$/
const DECLARATION_FILE = /\.d\.ts$/
const TEXT_FILE = /\.(?:ts|mts|cts|mjs|cjs|js|json|yml|yaml|md)$/
const SKIP_DIRS = new Set([
  'node_modules',
  'lib',
  '.git',
  '.venv',
  'local-workspace',
  '__pycache__',
  'dist',
  'coverage',
  '.dsh',
])
const UNCOVERED_WHITELIST = new Set(['vitest.config.ts'])
// The rule table and its negative-fixture test must name the retired files to test them.
const R4_FILE_WHITELIST = new Set([
  'CHANGELOG.md',
  'MIGRATIONS.md',
  'scripts/check-tsconfig-layout.mjs',
  'tests/unit/js/contract/tsconfig-layout.test.ts',
])
const OLD_NAMES = ['tsconfig.runtime.json', 'tsconfig.test.json', 'tsconfig.scripts.json']

const failures = []
function fail(rule, message) {
  failures.push(rule + ': ' + message)
}

function rel(abs) {
  return path.relative(ROOT, abs).split(path.sep).join('/')
}

function walk(dir, files, accept) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name)
    if (entry.isDirectory()) {
      if (SKIP_DIRS.has(entry.name)) continue
      walk(full, files, accept)
    } else if (accept(entry.name)) {
      files.push(rel(full))
    }
  }
}

function readText(relative) {
  const full = path.join(ROOT, relative)
  return fs.existsSync(full) ? fs.readFileSync(full, 'utf8') : null
}

function loadProject(project) {
  const configPath = path.join(ROOT, project.config)
  if (!fs.existsSync(configPath)) {
    fail('R2', project.config + ' is missing')
    return { name: project.name, files: [] }
  }
  const read = ts.readConfigFile(configPath, ts.sys.readFile)
  if (read.error) {
    fail('R2', project.config + ': ' + ts.flattenDiagnosticMessageText(read.error.messageText, ' '))
    return { name: project.name, files: [] }
  }
  const parsed = ts.parseJsonConfigFileContent(
    read.config,
    ts.sys,
    path.dirname(configPath),
    undefined,
    configPath,
  )
  for (const diagnostic of parsed.errors) {
    fail('R2', project.config + ': ' + ts.flattenDiagnosticMessageText(diagnostic.messageText, ' '))
  }
  return { name: project.name, files: parsed.fileNames.map(rel) }
}

// R2 — projects exist and the root is clean.
const projects = PROJECTS.map(loadProject)
const strayRootConfigs = fs.readdirSync(ROOT).filter(function (name) {
  return /^tsconfig\..+\.json$/.test(name)
})
for (const name of strayRootConfigs) fail('R2', 'root-level project config is not allowed: ' + name)

// R1 — every source file has exactly one project.
const sourceFiles = []
walk(ROOT, sourceFiles, function (name) {
  return SOURCE_FILE.test(name) && !DECLARATION_FILE.test(name)
})
const owners = new Map()
for (const project of projects) {
  for (const file of project.files) {
    if (!SOURCE_FILE.test(file) || DECLARATION_FILE.test(file)) continue
    if (!owners.has(file)) owners.set(file, [])
    owners.get(file).push(project.name)
  }
}
for (const file of sourceFiles) {
  const found = owners.get(file) || []
  if (found.length === 0) {
    if (!UNCOVERED_WHITELIST.has(file)) fail('R1', 'no project covers ' + file)
  } else if (found.length > 1) {
    fail('R1', file + ' belongs to ' + found.length + ' projects: ' + found.join(', '))
  }
}

// R3 — every referenced config target exists.
const references = []
function addReferences(text, owner, pattern) {
  if (!text) return
  for (const match of text.matchAll(pattern)) references.push({ file: match[1], owner: owner })
}
const buildScript = readText('scripts/build.mjs')
addReferences(buildScript, 'scripts/build.mjs', /config:\s*'([^']+\.json)'/g)
const packageText = readText('package.json')
if (packageText) {
  let pkg = null
  try {
    pkg = JSON.parse(packageText)
  } catch (error) {
    fail('R3', 'package.json is not valid JSON: ' + String(error.message || error))
  }
  for (const [name, command] of Object.entries((pkg && pkg.scripts) || {})) {
    if (typeof command === 'string') {
      addReferences(command, 'package.json#' + name, /(?:^|\s)-p\s+([^\s"']+\.json)/g)
    }
  }
}
const watchText = readText('scripts/ensure-build.mjs')
if (watchText) {
  const block = watchText.match(/const WATCH_FILES = \[([\s\S]*?)\]/)
  if (block) addReferences(block[1], 'scripts/ensure-build.mjs', /'([^']+\.json)'/g)
}
const docPathsText = readText('scripts/check-doc-paths.mjs')
if (docPathsText) {
  const block = docPathsText.match(/const ROOT_FILES = \[([\s\S]*?)\]/)
  if (block) addReferences(block[1], 'scripts/check-doc-paths.mjs', /'([^']+\.json)'/g)
}
for (const reference of references) {
  if (!fs.existsSync(path.join(ROOT, reference.file))) {
    fail('R3', reference.file + ' referenced by ' + reference.owner + ' does not exist')
  }
}

// R4 — retired config names are gone from live files.
const textFiles = []
walk(ROOT, textFiles, function (name) {
  return TEXT_FILE.test(name)
})
for (const file of textFiles) {
  if (R4_FILE_WHITELIST.has(file)) continue
  if (file.startsWith('openspec/') || file.startsWith('tests/e2e/blender/native/')) continue
  const content = readText(file)
  if (!content) continue
  for (const oldName of OLD_NAMES) {
    if (content.includes(oldName)) fail('R4', file + ' still mentions ' + oldName)
  }
}

if (failures.length) {
  console.error('check-tsconfig-layout: ' + failures.length + ' problem(s)')
  for (const failure of failures) console.error('  - ' + failure)
  process.exit(1)
}

const tsFiles = sourceFiles.filter(function (file) {
  return !UNCOVERED_WHITELIST.has(file)
})
console.log(
  'check-tsconfig-layout: PASS (' +
    projects.length +
    ' projects, ' +
    tsFiles.length +
    ' TypeScript files, ' +
    references.length +
    ' references, ' +
    textFiles.length +
    ' live files scanned)',
)
