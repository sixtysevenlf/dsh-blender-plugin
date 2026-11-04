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

    for (const [name, family] of Object.entries(CONTRACTS.families) as [string, any][]) {
      if (family.host) continue
      expect(typeof family.api, name).toBe('string')
      expect(family.api, name).toMatch(/^dsh_[a-z0-9_]+_api$/)
      expect(CONTRACTS.pythonTables[family.table], name + ' -> ' + family.table).toBeTruthy()
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
    const family = CONTRACTS.families[familyName] as any
    if (family.host) continue
    const table = tables.get(family.table)!
    const pythonKeys = new Set(table.keys!)
    const catalogExtras = Object.keys(family.catalogExtras ?? {})
    const aliases: Record<string, string> = family.aliases ?? {}
    const pythonExtras = new Set<string>(family.pythonExtras ?? [])

    it('maps every ' + familyName + ' catalog op to a declared python dispatch key', () => {
      for (const extra of catalogExtras) expect(ops, familyName + ' catalogExtras').toContain(extra)
      for (const [op, target] of Object.entries(aliases)) {
        expect(ops, familyName + ' alias source').toContain(op)
        expect(pythonKeys.has(target), familyName + ' alias ' + op + ' -> ' + target).toBe(true)
      }

      const required = ops
        .filter((op) => !catalogExtras.includes(op))
        .map((op) => aliases[op] ?? op)
      expect(required.length, familyName).toBe(ops.length - catalogExtras.length)

      const missing = required.filter((key) => !pythonKeys.has(key))
      expect(missing, familyName + ': catalog ops missing from ' + family.table).toEqual([])

      for (const extra of pythonExtras) {
        expect(pythonKeys.has(extra), familyName + ' pythonExtras ' + extra).toBe(true)
        expect(
          required,
          familyName + ' pythonExtra must not be a mapped catalog op: ' + extra,
        ).not.toContain(extra)
      }

      if (family.mode !== 'subset') {
        const unexpected = [...pythonKeys].filter(
          (key) => !required.includes(key) && !pythonExtras.has(key),
        )
        expect(
          unexpected,
          familyName + ': undeclared python dispatch keys in ' + family.table,
        ).toEqual([])
      } else {
        expect(
          Object.keys(aliases).length,
          familyName + ' subset mode needs explicit aliases',
        ).toBeGreaterThan(0)
      }
    })
  }

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
