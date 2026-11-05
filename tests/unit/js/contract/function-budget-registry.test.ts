import { afterAll, describe, expect, it } from 'vitest'
import { spawnSync } from 'node:child_process'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { pkgPath } from '../../../helpers/pkg'

const SCRIPT = pkgPath('scripts', 'check-function-budget.mjs')
const REGISTRY = 'config/function-budget-allowlist.json'
const DESCRIPTION =
  'Function-level budget registry: every 80-120 line function is registered with a real classification, reason, owner and reviewBy; functions over 120 lines always fail. Path-level exceptions live in config/exceptions.json.'
const tmpDirs: string[] = []

type Entry = {
  file: string
  name: string
  lines?: number
  classification: string
  reason: string
  owner: string
  reviewBy: string
}

type Violation = { kind: string; file: string; name?: string; lines?: number; why: string }

type Report = {
  ok: boolean
  registry: string
  counts: { band: number; hard: number; stale: number; registered: number }
  bandFunctions: { file: string; name: string; lines: number }[]
  hardFunctions: { file: string; name: string; lines: number }[]
  staleFunctions: { file: string; name: string; lines?: number }[]
  registryFailures: string[]
  violations: Violation[]
}

function tmpRoot(): string {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'dsh-fn-budget-'))
  tmpDirs.push(dir)
  return dir
}

function writeFunction(
  root: string,
  file: string,
  name: string,
  lines: number,
  append = false,
): void {
  const body = Array.from({ length: lines - 2 }, (_, index) => '  const v' + index + ' = ' + index)
  const target = path.join(root, file)
  fs.mkdirSync(path.dirname(target), { recursive: true })
  const text = ['export function ' + name + '() {', ...body, '  return 0', '}'].join('\n') + '\n'
  if (append) fs.appendFileSync(target, text, 'utf8')
  else fs.writeFileSync(target, text, 'utf8')
}

function writeRegistry(root: string, entries: Entry[]): void {
  const dir = path.join(root, 'config')
  fs.mkdirSync(dir, { recursive: true })
  fs.writeFileSync(
    path.join(dir, 'function-budget-allowlist.json'),
    JSON.stringify(
      {
        version: 1,
        description: DESCRIPTION,
        generatedAt: '2026-11-05T00:00:00.000Z',
        functions: entries,
      },
      null,
      2,
    ) + '\n',
    'utf8',
  )
}

function entry(overrides: Partial<Entry> = {}): Entry {
  return {
    file: 'src/band.ts',
    name: 'bandFn',
    classification: 'linear-pipeline',
    reason: 'Sequential fixture pipeline step that exercises the soft-band registry.',
    owner: 'runtime',
    reviewBy: '2099-12-31',
    ...overrides,
  }
}

function run(
  root: string,
  extra: string[] = [],
  jsonOnly = true,
): { status: number | null; report: Report | null } {
  const result = spawnSync(
    process.execPath,
    [SCRIPT, '--root', root, ...(jsonOnly ? ['--json'] : []), ...extra],
    { cwd: pkgPath(), encoding: 'utf8' },
  )
  const stdout = String(result.stdout || '').trim()
  const report = jsonOnly && stdout ? (JSON.parse(stdout) as Report) : null
  return { status: result.status, report }
}

afterAll(() => {
  for (const dir of tmpDirs) fs.rmSync(dir, { recursive: true, force: true })
})

describe('function budget soft-band registry', () => {
  it('passes on the checked-in registry and emits the whole band', () => {
    const { status, report } = run(pkgPath())
    expect(status).toBe(0)
    expect(report).not.toBeNull()
    expect(report!.ok).toBe(true)
    expect(report!.violations).toEqual([])
    expect(report!.registryFailures).toEqual([])
    expect(report!.staleFunctions).toEqual([])
    expect(report!.registry.replace(/\\/g, '/')).toBe(REGISTRY)
    expect(report!.counts.band).toBeGreaterThan(20)
    expect(report!.bandFunctions.length).toBe(report!.counts.band)
    const unique = new Set(report!.bandFunctions.map((row) => row.file + '::' + row.name))
    expect(unique.size).toBe(report!.counts.band)
  })

  it('fails when a band function is not registered', () => {
    const root = tmpRoot()
    writeFunction(root, 'src/band.ts', 'bandFn', 90)
    writeRegistry(root, [])
    const { status, report } = run(root)
    expect(status).toBe(1)
    expect(report!.ok).toBe(false)
    expect(report!.counts.band).toBe(1)
    expect(report!.violations.map((row) => row.why).join('\n')).toContain('not registered')
  })

  it('fails when a registered function reviewBy is expired', () => {
    const root = tmpRoot()
    writeFunction(root, 'src/band.ts', 'bandFn', 90)
    writeRegistry(root, [entry({ reviewBy: '2020-01-01' })])
    const { status, report } = run(root)
    expect(status).toBe(1)
    expect(report!.ok).toBe(false)
    expect(report!.registryFailures.join('\n')).toContain('expired')
  })

  it('fails when a registry entry uses an unregistered classification', () => {
    const root = tmpRoot()
    writeFunction(root, 'src/band.ts', 'bandFn', 90)
    writeRegistry(root, [entry({ classification: 'permanent-warning' })])
    const { status, report } = run(root)
    expect(status).toBe(1)
    expect(report!.ok).toBe(false)
    expect(report!.registryFailures.join('\n')).toContain('classification')
  })

  it('fails and reports a stale entry for a function outside the band', () => {
    const root = tmpRoot()
    writeFunction(root, 'src/band.ts', 'bandFn', 90)
    writeFunction(root, 'src/small.ts', 'smallFn', 40)
    writeRegistry(root, [entry(), entry({ file: 'src/small.ts', name: 'smallFn' })])
    const { status, report } = run(root)
    expect(status).toBe(1)
    expect(report!.ok).toBe(false)
    expect(report!.counts.stale).toBe(1)
    expect(report!.staleFunctions).toEqual([{ file: 'src/small.ts', name: 'smallFn' }])
    expect(report!.violations.map((row) => row.kind)).toContain('stale-function')
  })

  it('fails for a function over 120 lines even when registered', () => {
    const root = tmpRoot()
    writeFunction(root, 'src/hard.ts', 'hardFn', 121)
    writeRegistry(root, [entry({ file: 'src/hard.ts', name: 'hardFn' })])
    const { status, report } = run(root)
    expect(status).toBe(1)
    expect(report!.ok).toBe(false)
    expect(report!.counts.hard).toBe(1)
    expect(report!.violations.map((row) => row.why).join('\n')).toContain('120-line hard limit')
  })

  it('emits the full band even beyond the old 20-item hidden slice', () => {
    const root = tmpRoot()
    const entries: Entry[] = []
    for (let index = 0; index < 21; index += 1) {
      const name = 'bandFn' + index
      writeFunction(root, 'src/band.ts', name, 90, index > 0)
      entries.push(entry({ name }))
    }
    writeRegistry(root, entries)
    const { status, report } = run(root)
    expect(status).toBe(0)
    expect(report!.ok).toBe(true)
    expect(report!.counts.band).toBe(21)
    expect(report!.bandFunctions.length).toBe(21)
  })

  it('skips AUTO-GENERATED sources and keeps them out of the ledger', () => {
    const root = tmpRoot()
    const target = path.join(root, 'src', 'generated.ts')
    fs.mkdirSync(path.dirname(target), { recursive: true })
    const body = Array.from({ length: 88 }, (_, index) => '  const v' + index + ' = ' + index)
    fs.writeFileSync(
      target,
      [
        '// AUTO-GENERATED FILE - do not edit',
        'export function generatedFn() {',
        ...body,
        '  return 0',
        '}',
      ].join('\n') + '\n',
      'utf8',
    )
    writeRegistry(root, [])
    const { status, report } = run(root)
    expect(status).toBe(0)
    expect(report!.ok).toBe(true)
    expect(report!.counts.band).toBe(0)
  })

  it('rewrites the ledger with --refresh and requires classification afterwards', () => {
    const root = tmpRoot()
    writeFunction(root, 'src/band.ts', 'bandFn', 90)
    writeRegistry(root, [])
    const refreshed = run(root, ['--refresh'], false)
    expect(refreshed.status).toBe(0)
    const raw = JSON.parse(fs.readFileSync(path.join(root, REGISTRY), 'utf8')) as {
      version: number
      functions: Entry[]
    }
    expect(raw.version).toBe(1)
    expect(raw.functions).toHaveLength(1)
    expect(raw.functions[0].file).toBe('src/band.ts')
    expect(raw.functions[0].name).toBe('bandFn')
    expect(raw.functions[0].classification).toBe('unclassified')
    const gate = run(root)
    expect(gate.status).toBe(1)
    expect(gate.report!.registryFailures.join('\n')).toContain('classification')
  })
})
