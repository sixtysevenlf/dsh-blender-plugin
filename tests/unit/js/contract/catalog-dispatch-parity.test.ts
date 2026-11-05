import { afterAll, describe, expect, it } from 'vitest'
import { spawnSync } from 'node:child_process'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { catalogPayload } from '../../../../runtime/engine.js'
import { pkgPath } from '../../../helpers/pkg'

// parity gate: the human-readable catalog (runtime/catalog/data.ts) and the
// literal Python dispatch tables (runtime/py/**) must stay in lockstep. The link
// layer is config/contracts.json (family -> python table + declared extras/
// aliases); scripts/catalog_dispatch_keys.py collects the literal dict keys with
// the Python ast module (no import, no bpy). Host-side families and both-side
// extras must be declared explicitly so nothing is silently skipped.

const SCRIPT = pkgPath('scripts', 'catalog_dispatch_keys.py')
const CONTRACTS = JSON.parse(fs.readFileSync(pkgPath('config', 'contracts.json'), 'utf8'))
const ROUTING = JSON.parse(fs.readFileSync(pkgPath('config', 'routing.json'), 'utf8'))
const PAYLOAD = catalogPayload({ full: true }) as any

interface Extraction {
  tables: Record<
    string,
    { kind: string; file: string; keys: string[] | null; error: string | null }
  >
  errors: string[]
}

function pythonCandidates(): string[] {
  const candidates: string[] = []
  if (process.env.PYTHON) candidates.push(process.env.PYTHON)
  candidates.push(
    process.platform === 'win32'
      ? pkgPath('.venv', 'Scripts', 'python.exe')
      : pkgPath('.venv', 'bin', 'python'),
  )
  candidates.push(process.platform === 'win32' ? 'python' : 'python3', 'python')
  return candidates
}

function runExtractor(root: string): {
  status: number | null
  stdout: string
  stderr: string
  error?: string
} {
  let lastError = ''
  for (const candidate of pythonCandidates()) {
    if (candidate.includes(path.sep) && !fs.existsSync(candidate)) continue
    const run = spawnSync(candidate, [SCRIPT, root], {
      cwd: pkgPath(),
      encoding: 'utf8',
      timeout: 120_000,
      env: { ...process.env, PYTHONIOENCODING: 'utf-8', PYTHONUTF8: '1' },
    })
    if (run.error) {
      lastError = String(run.error)
      continue
    }
    return { status: run.status, stdout: run.stdout, stderr: run.stderr }
  }
  throw new Error(
    'cannot run python for the catalog parity gate (set PYTHON, or create .venv); last error: ' +
      lastError,
  )
}

const extracted: Extraction = (() => {
  const run = runExtractor(pkgPath())
  if (run.status !== 0) {
    throw new Error(
      'catalog_dispatch_keys.py failed (exit ' +
        run.status +
        '): ' +
        (run.stdout + run.stderr).slice(0, 2000),
    )
  }
  return JSON.parse(run.stdout) as Extraction
})()

const catalogFamilies: any[] = PAYLOAD.detail
const catalogOps = new Map<string, string[]>(
  catalogFamilies.map((row) => [row.family as string, row.ops as string[]]),
)
const tables = new Map(Object.entries(extracted.tables))

const tmpRoots: string[] = []

function fixtureContracts(tablesDefinition: Record<string, unknown>): string {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'dsh-parity-'))
  fs.mkdirSync(path.join(root, 'config'), { recursive: true })
  fs.writeFileSync(
    path.join(root, 'config', 'contracts.json'),
    JSON.stringify({ version: 1, pythonTables: tablesDefinition, families: {} }, null, 2),
  )
  tmpRoots.push(root)
  return root
}

afterAll(() => {
  for (const root of tmpRoots) fs.rmSync(root, { recursive: true, force: true })
})

interface FamilyContract {
  api?: string
  table?: string
  host?: string[]
  hostReason?: string
  hostOps?: Record<string, string>
  catalogExtras?: Record<string, string>
  aliases?: Record<string, string>
  pythonExtras?: string[]
  mode?: string
}

// One place decides whether a family's catalog ops really reach Python. Host-side
// ops are legitimate only through hostOps with a reason; the retired catalogExtras
// key is a hard failure so a missing dispatch key cannot hide as "known drift".
function validateFamily(
  familyName: string,
  ops: string[],
  family: FamilyContract,
  pythonKeys: Set<string>,
): string[] {
  const errors: string[] = []
  const table = family.table ?? '(no table)'
  if (family.catalogExtras !== undefined) {
    errors.push(
      familyName + ': catalogExtras is retired; declare host-side ops in hostOps with a reason',
    )
  }
  const hostOps = family.hostOps ?? {}
  const aliases = family.aliases ?? {}
  const pythonExtras = new Set<string>(family.pythonExtras ?? [])
  for (const [op, reason] of Object.entries(hostOps)) {
    if (!ops.includes(op)) errors.push(familyName + ': hostOps op is not in the catalog: ' + op)
    if (!reason || reason.trim() === '')
      errors.push(familyName + ': hostOps reason is empty: ' + op)
    if (pythonKeys.has(op))
      errors.push(familyName + ': hostOps op must not exist in ' + table + ': ' + op)
  }
  for (const [op, target] of Object.entries(aliases)) {
    if (!ops.includes(op)) errors.push(familyName + ': alias source is not a catalog op: ' + op)
    if (Object.hasOwn(hostOps, op))
      errors.push(familyName + ': host-side op must not carry an alias: ' + op)
    if (!pythonKeys.has(target))
      errors.push(
        familyName + ': alias target is missing from ' + table + ': ' + op + ' -> ' + target,
      )
  }
  const required = ops.filter((op) => !Object.hasOwn(hostOps, op)).map((op) => aliases[op] ?? op)
  for (const key of required) {
    if (!pythonKeys.has(key))
      errors.push(familyName + ': missing python dispatch key in ' + table + ': ' + key)
  }
  for (const extra of pythonExtras) {
    if (!pythonKeys.has(extra))
      errors.push(familyName + ': pythonExtras key is missing from ' + table + ': ' + extra)
    if (required.includes(extra))
      errors.push(familyName + ': pythonExtra must not be a mapped catalog op: ' + extra)
    if (Object.hasOwn(hostOps, extra))
      errors.push(familyName + ': pythonExtra must not also be a hostOp: ' + extra)
  }
  if (family.mode !== 'subset') {
    const unexpected = [...pythonKeys].filter(
      (key) => !required.includes(key) && !pythonExtras.has(key),
    )
    for (const key of unexpected) {
      errors.push(familyName + ': undeclared python dispatch key in ' + table + ': ' + key)
    }
  } else if (Object.keys(aliases).length === 0) {
    errors.push(familyName + ': subset mode needs explicit aliases')
  }
  return errors
}

describe('catalog <-> Python dispatch parity', () => {
  it('parses every declared Python dispatch table (ast, no import)', () => {
    expect(extracted.errors).toEqual([])
    expect(tables.size).toBe(Object.keys(CONTRACTS.pythonTables).length)
    expect(tables.size).toBeGreaterThan(0)
    for (const [name, table] of tables) {
      expect(table.error, name).toBeNull()
      expect(table.keys, name).not.toBeNull()
      expect(table.keys!.length, name).toBeGreaterThan(0)
    }
  })

  it('covers exactly the catalog families with a declared python table or a host-side reason', () => {
    const contractFamilies = Object.keys(CONTRACTS.families).sort()
    expect(contractFamilies).toEqual([...catalogOps.keys()].sort())

    const hostFamilies = Object.entries(CONTRACTS.families)
      .filter(([, family]) => (family as any).host)
      .map(([name]) => name)
      .sort()
    expect(RoutingHostFamilies()).toEqual(hostFamilies)
    for (const name of hostFamilies) {
      const family = CONTRACTS.families[name] as any
      expect(family.host.length, name).toBeGreaterThan(0)
      expect(family.hostReason, name).toBeTruthy()
      expect(family.table, name).toBeUndefined()
    }

    for (const [name, family] of Object.entries(CONTRACTS.families) as [string, FamilyContract][]) {
      expect(family.catalogExtras, name + ' catalogExtras').toBeUndefined()
      if (family.host) continue
      expect(typeof family.api, name).toBe('string')
      expect(family.api, name).toMatch(/^dsh_[a-z0-9_]+_api$/)
      expect(family.table, name + ' table').toBeTruthy()
      expect(CONTRACTS.pythonTables[family.table!], name + ' -> ' + family.table).toBeTruthy()
    }
  })

  it('keeps config/routing.json pointed at the same per-family api and family set', () => {
    const routingFamilies = new Set<string>()
    for (const route of ROUTING.routes as any[]) {
      routingFamilies.add(route.family)
      const family = CONTRACTS.families[route.family]
      expect(family, 'routing family ' + route.family).toBeTruthy()
      expect(family.host, 'host-side family must not have a route: ' + route.family).toBeUndefined()
      expect(family.api, route.id).toBe(route.api)
    }
    const declared = [...routingFamilies, ...RoutingHostFamilies()].sort()
    expect(declared).toEqual(Object.keys(CONTRACTS.families).sort())

    const hostSide = new Set(RoutingHostFamilies())
    for (const family of hostSide) expect(routingFamilies.has(family), family).toBe(false)
  })

  for (const [familyName, ops] of catalogOps) {
    const family = CONTRACTS.families[familyName] as FamilyContract
    if (family.host) continue
    const table = tables.get(family.table!)!
    const pythonKeys = new Set(table.keys!)

    it('maps every ' + familyName + ' catalog op to a declared python dispatch key', () => {
      expect(
        validateFamily(familyName, ops, family, pythonKeys),
        familyName + ': catalog <-> dispatch mismatches',
      ).toEqual([])
    })
  }

  it('rejects a catalog op without a python dispatch key', () => {
    const errors = validateFamily(
      'demo',
      ['compare_auto', 'align_iou'],
      { api: 'dsh_demo_api', table: 'demo' },
      new Set(['compare_auto']),
    )
    expect(errors.join('\n')).toContain('demo: missing python dispatch key in demo: align_iou')
  })

  it('accepts a host-side op declared in hostOps', () => {
    const errors = validateFamily(
      'shape',
      ['search', 'loft'],
      {
        api: 'dsh_shape_api',
        table: 'shape',
        hostOps: { search: 'handled before the dispatcher' },
      },
      new Set(['loft']),
    )
    expect(errors).toEqual([])
  })

  it('rejects the retired catalogExtras field', () => {
    const errors = validateFamily(
      'demo',
      ['compare'],
      { table: 'demo', catalogExtras: { compare: 'old drift note' } },
      new Set(['compare']),
    )
    expect(errors.join('\n')).toContain('catalogExtras is retired')
  })

  it('rejects a hostOps entry that also exists in the python table', () => {
    const errors = validateFamily(
      'demo',
      ['search', 'gen'],
      { table: 'demo', hostOps: { search: 'handled by host' } },
      new Set(['search', 'gen']),
    )
    expect(errors.join('\n')).toContain('hostOps op must not exist in demo: search')
  })

  it('rejects a contracts entry whose python file does not exist', () => {
    const root = fixtureContracts({
      ghost: {
        kind: 'local-dict',
        file: 'runtime/py/does_not_exist.py',
        function: 'ghost_dispatch',
        var: 'ops',
      },
    })
    const run = runExtractor(root)
    expect(run.status).toBe(1)
    const parsed = JSON.parse(run.stdout) as Extraction
    expect(parsed.errors[0]).toContain('file not found')
    expect(parsed.errors[0]).toContain('runtime/py/does_not_exist.py')
  })

  it('rejects a table that is not a literal dict', () => {
    const root = fixtureContracts({
      ghost: {
        kind: 'local-dict',
        file: 'runtime/py/ghost.py',
        function: 'ghost_dispatch',
        var: 'not_ops',
      },
    })
    fs.mkdirSync(path.join(root, 'runtime', 'py'), { recursive: true })
    fs.writeFileSync(
      path.join(root, 'runtime', 'py', 'ghost.py'),
      "def ghost_dispatch():\n    ops = {'a': 1}\n    return {}\n",
    )
    const run = runExtractor(root)
    expect(run.status).toBe(1)
    const parsed = JSON.parse(run.stdout) as Extraction
    expect(parsed.errors[0]).toContain('not_ops')
  })
})

function RoutingHostFamilies(): string[] {
  return [...(ROUTING.hostSideFamilies as string[])].sort()
}
