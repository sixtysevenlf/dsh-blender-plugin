import { afterAll, describe, expect, it } from 'vitest'
import { spawnSync } from 'node:child_process'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { pkgPath } from '../../../helpers/pkg'

const SCRIPT = pkgPath('scripts', 'check-exceptions.mjs')
const tmpDirs: string[] = []

type Report = {
  ok: boolean
  entries: number
  kinds: string[]
  failures: string[]
}

function tmpDir(): string {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'dsh-exceptions-'))
  tmpDirs.push(dir)
  return dir
}

function run(root: string): { status: number | null; report: Report | null } {
  const result = spawnSync(process.execPath, [SCRIPT, '--json', '--root', root], {
    cwd: pkgPath(),
    encoding: 'utf8',
  })
  let report: Report | null = null
  if (String(result.stdout || '').trim()) report = JSON.parse(String(result.stdout)) as Report
  return { status: result.status, report }
}

function writeRegistry(root: string, entries: unknown[]): void {
  const dir = path.join(root, 'config')
  fs.mkdirSync(dir, { recursive: true })
  fs.writeFileSync(
    path.join(dir, 'exceptions.json'),
    JSON.stringify({ version: 1, entries }, null, 2) + '\n',
    'utf8',
  )
}

function entry(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    id: 'EXC-TEST-ENTRY',
    kind: 'file-budget',
    path: 'tracked.txt',
    reason: 'Test fixture entry for the exceptions registry gate.',
    owner: 'tests',
    reviewBy: '2099-12-31',
    ...overrides,
  }
}

afterAll(() => {
  for (const dir of tmpDirs) fs.rmSync(dir, { recursive: true, force: true })
})

describe('exceptions registry', () => {
  it('passes on the checked-in registry', () => {
    const { status, report } = run(pkgPath())
    expect(status).toBe(0)
    expect(report).not.toBeNull()
    expect(report!.ok).toBe(true)
    expect(report!.failures).toEqual([])
    expect(report!.kinds).toContain('file-budget')
    expect(report!.entries).toBeGreaterThan(0)
  })

  it('passes for a valid fixture registry', () => {
    const root = tmpDir()
    fs.writeFileSync(path.join(root, 'tracked.txt'), 'x\n')
    writeRegistry(root, [entry()])
    const { status, report } = run(root)
    expect(status).toBe(0)
    expect(report!.ok).toBe(true)
    expect(report!.failures).toEqual([])
  })

  it('fails when a registered path does not exist', () => {
    const root = tmpDir()
    writeRegistry(root, [entry({ id: 'EXC-MISSING-PATH', path: 'runtime/catalog/missing.ts' })])
    const { status, report } = run(root)
    expect(status).toBe(1)
    expect(report!.ok).toBe(false)
    expect(report!.failures.join('\n')).toContain('path 不存在')
  })

  it('fails when reviewBy is expired or malformed', () => {
    const root = tmpDir()
    fs.writeFileSync(path.join(root, 'tracked.txt'), 'x\n')
    writeRegistry(root, [entry({ reviewBy: '2020-01-01' })])
    const expired = run(root)
    expect(expired.status).toBe(1)
    expect(expired.report!.failures.join('\n')).toContain('reviewBy 已过期')

    writeRegistry(root, [entry({ reviewBy: '2026-02-31' })])
    const malformed = run(root)
    expect(malformed.status).toBe(1)
    expect(malformed.report!.failures.join('\n')).toContain('reviewBy 不是合法 YYYY-MM-DD')
  })

  it('fails when the kind is outside the enum', () => {
    const root = tmpDir()
    fs.writeFileSync(path.join(root, 'tracked.txt'), 'x\n')
    writeRegistry(root, [entry({ kind: 'permanent-ignore' })])
    const { status, report } = run(root)
    expect(status).toBe(1)
    expect(report!.failures.join('\n')).toContain('kind 不在枚举内')
  })

  it('fails on duplicate ids and missing fields', () => {
    const root = tmpDir()
    fs.writeFileSync(path.join(root, 'tracked.txt'), 'x\n')
    writeRegistry(root, [entry(), entry()])
    const duplicate = run(root)
    expect(duplicate.status).toBe(1)
    expect(duplicate.report!.failures.join('\n')).toContain('id 重复')

    writeRegistry(root, [{ id: 'EXC-NO-OWNER', kind: 'file-budget', path: 'tracked.txt' }])
    const missing = run(root)
    expect(missing.status).toBe(1)
    expect(missing.report!.failures.join('\n')).toContain('缺少非空字符串字段 owner')
  })

  it('rejects a registry with an unsupported version or shape', () => {
    const root = tmpDir()
    fs.mkdirSync(path.join(root, 'config'), { recursive: true })
    fs.writeFileSync(
      path.join(root, 'config', 'exceptions.json'),
      JSON.stringify({ version: 2, entries: [] }),
    )
    const badVersion = run(root)
    expect(badVersion.status).toBe(1)
    expect(badVersion.report!.failures.join('\n')).toContain('version 必须为 1')
  })

  it('keeps the phantom runtime/catalog.ts out of the gates', () => {
    const structure = fs.readFileSync(pkgPath('scripts', 'check-structure.mjs'), 'utf8')
    expect(structure).toContain('config/exceptions.json')
    expect(structure).toContain("entry.kind === 'file-budget'")
    expect(structure).not.toContain("new Set(['runtime/catalog.ts'])")
    const baseline = JSON.parse(
      fs.readFileSync(pkgPath('config', 'coverage-baseline.json'), 'utf8'),
    ) as { criticalModules: Record<string, unknown> }
    expect(baseline.criticalModules['runtime/catalog.ts']).toBeUndefined()
  })
})
