// Regenerates the machine-readable gate artifacts in this directory.
// Read-only with respect to product code; run from the repo root:
//   node docs/refactor/evidence/gates/generate-gate-artifacts.mjs
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const ROOT = path.resolve(HERE, '../../../..')
const out = (name, value) => fs.writeFileSync(path.join(HERE, name), JSON.stringify(value, null, 2) + '\n')

const engine = await import(pathToFileURL(path.join(ROOT, 'lib/runtime/engine.js')).href)
const routesMod = await import(pathToFileURL(path.join(ROOT, 'lib/runtime/http/routes/index.js')).href)

const matrixText = fs.readFileSync(path.join(ROOT, 'tests/BEHAVIOR_MATRIX.md'), 'utf8')
const rows = []
for (const line of matrixText.split(/\r?\n/)) {
  if (!line.startsWith('| ') || line.startsWith('| id ') || line.startsWith('|---')) continue
  const cells = line.split('|').slice(1, -1).map((s) => s.trim())
  if (cells.length < 8) continue
  rows.push({
    id: cells[0],
    surface: cells[1],
    behavior: cells[2],
    contractSource: cells[3],
    unit: cells[4],
    integration: cells[5],
    e2e: cells[6],
    status: cells[7],
    exceptionOwner: cells[8] || ''
  })
}
const bySurface = {}
for (const r of rows) (bySurface[r.surface] ||= []).push(r)
out('matrix-report.json', { total: rows.length, bySurface: Object.fromEntries(Object.entries(bySurface).map(([k, v]) => [k, v.length])), rows })

const families = engine.PLAN_CATALOG.map((f) => ({ family: f.f, prefix: f.prefix, ops: f.ops.length, opNames: f.ops }))

// A2: regenerate the capability lock from the live catalog and diff it against
// the committed fixture. The lock is the frozen surface, not a second source.
const lockPath = path.join(ROOT, 'tests/fixtures/capability.lock.json')
const locked = JSON.parse(fs.readFileSync(lockPath, 'utf8'))
const generatedLock = {
  families_count: families.length,
  ops_count: families.reduce((n, f) => n + f.ops, 0),
  tools_count: engine.MODEL_FACING_TOOLS.length,
  tools: [...engine.MODEL_FACING_TOOLS].sort(),
  families: [...families].sort((a, b) => a.family.localeCompare(b.family)).map((f) => ({ f: f.family, ops: f.ops }))
}
const lockDiffs = []
for (const key of ['families_count', 'ops_count', 'tools_count']) {
  if (locked[key] !== generatedLock[key]) lockDiffs.push(key + ': lock=' + locked[key] + ' live=' + generatedLock[key])
}
if (JSON.stringify([...locked.tools].sort()) !== JSON.stringify(generatedLock.tools)) lockDiffs.push('tools list differs')
if (JSON.stringify([...locked.families].sort((a, b) => a.f.localeCompare(b.f))) !== JSON.stringify(generatedLock.families)) lockDiffs.push('families/ops table differs')
fs.writeFileSync(path.join(HERE, 'catalog-lock.diff'), lockDiffs.length ? lockDiffs.join('\n') + '\n' : '')
if (lockDiffs.length) process.exitCode = 1

out('catalog-report.json', {
  families: families.length,
  ops: families.reduce((n, f) => n + f.ops, 0),
  tools: generatedLock.tools_count,
  fingerprint: engine.catalogFingerprint(),
  planOpNames: engine.planOpNames().length,
  planReadOnlyOps: engine.PLAN_READ_ONLY_OPS.length,
  lockMatchesCommitted: lockDiffs.length === 0,
  familiesDetail: families
})

const commandRows = bySurface['addon command'] || []
out('command-matrix.json', {
  count: Object.keys(engine.COMMAND_CATALOG).length,
  covered: commandRows.filter((r) => r.status === 'covered').length,
  commands: Object.entries(engine.COMMAND_CATALOG).map(([name, c]) => ({
    name,
    description: c.d,
    gate: c.gate ?? null,
    matrixStatus: commandRows.find((r) => r.id === 'CMD-' + name)?.status ?? 'missing'
  }))
})

const routeRows = bySurface['http route'] || []
out('route-matrix.json', {
  count: routesMod.CONTRACT_ROUTES.length,
  routes: routesMod.CONTRACT_ROUTES.map((p) => ({
    path: p,
    matrixStatus: routeRows.find((r) => r.id === 'ROUTE-' + p.replace(/^\//, '').replace(/\//g, '-'))?.status ?? 'missing'
  }))
})

const toolRows = bySurface['model tool'] || []
out('tool-matrix.json', {
  count: toolRows.length,
  tools: toolRows.map((r) => ({ name: r.id.replace(/^TOOL-/, ''), status: r.status, unit: r.unit, integration: r.integration, e2e: r.e2e }))
})

const protocolPy = fs.readFileSync(path.join(ROOT, 'addon/dsh_blender_bridge/protocol.py'), 'utf8')
const addonInit = fs.readFileSync(path.join(ROOT, 'addon/dsh_blender_bridge/__init__.py'), 'utf8')
out('protocol-info.json', {
  addonProtocolVersion: Number(/ADDON_PROTOCOL_VERSION\s*=\s*(\d+)/.exec(protocolPy)[1]),
  addonBlInfoVersion: /"version"\s*:\s*\((\d+),\s*(\d+)\)/.exec(addonInit).slice(1).map(Number),
  expectedProtocol: 11,
  source: ['addon/dsh_blender_bridge/protocol.py', 'addon/dsh_blender_bridge/__init__.py']
})
console.log('wrote gate artifacts:', ['matrix-report.json','catalog-report.json','command-matrix.json','route-matrix.json','tool-matrix.json','protocol-info.json'].join(', '))
