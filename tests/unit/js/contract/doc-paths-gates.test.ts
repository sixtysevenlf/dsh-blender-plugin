import { afterAll, describe, expect, it } from 'vitest'
import { spawnSync } from 'node:child_process'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { pkgPath } from '../../../helpers/pkg'

const SCRIPT = pkgPath('scripts', 'check-doc-paths.mjs')
const tmpDirs: string[] = []

function tmpDir(): string {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'dsh-doc-paths-'))
  tmpDirs.push(dir)
  return dir
}

afterAll(() => {
  for (const dir of tmpDirs) fs.rmSync(dir, { recursive: true, force: true })
})

type MissingRef = { doc: string; line: number; target: string }
type Report = {
  ok: boolean
  mode: string
  legacy: { docs: string[]; failures: string[] }
  docs: { extendedCount: number; paths: string[] }
  findings: { inbound: Record<string, number>; orphanDocs: string[]; missingRefs: MissingRef[] }
  baseline: {
    path: string
    present: boolean
    review: null | { owner: string; reviewBy: string; reason: string }
    acceptedOrphans: number
    acceptedMissingRefs: number
    matches: boolean
    newOrphans: string[]
    newMissingRefs: MissingRef[]
  }
}

function write(root: string, relPath: string, content: string): void {
  const file = path.join(root, relPath)
  fs.mkdirSync(path.dirname(file), { recursive: true })
  fs.writeFileSync(file, content)
}

function writePackageJson(root: string): void {
  write(root, 'package.json', JSON.stringify({ name: 'doc-paths-fixture', scripts: {} }, null, 2))
}

function run(
  root: string,
  extra: string[] = [],
): { status: number | null; report: Report | null; stderr: string } {
  const result = spawnSync(process.execPath, [SCRIPT, '--json', '--root', root, ...extra], {
    cwd: pkgPath(),
    encoding: 'utf8',
  })
  const stdout = String(result.stdout || '').trim()
  return {
    status: result.status,
    report: stdout ? (JSON.parse(stdout) as Report) : null,
    stderr: String(result.stderr || ''),
  }
}

describe('check-doc-paths', () => {
  it('reports the checked-in doc graph in warn mode', () => {
    const { status, report } = run(pkgPath())
    expect(status).toBe(0)
    expect(report).not.toBeNull()
    expect(report!.mode).toBe('warn')
    expect(report!.legacy.failures).toEqual([])
    expect(report!.docs.extendedCount).toBe(22)
    expect(report!.findings.orphanDocs).toHaveLength(0)
    expect(report!.findings.missingRefs).toHaveLength(7)
    expect(report!.baseline.present).toBe(true)
    expect(report!.baseline.review).not.toBeNull()
    expect(report!.baseline.review!.owner).toBeTruthy()
    expect(report!.baseline.review!.reason).toBeTruthy()
    expect(report!.baseline.review!.reviewBy).toMatch(/^\d{4}-\d{2}-\d{2}$/)
    expect(report!.baseline.acceptedOrphans).toBe(0)
    expect(report!.baseline.acceptedMissingRefs).toBe(7)
    expect(report!.baseline.matches).toBe(true)
    expect(report!.findings.missingRefs.map((entry) => entry.doc + ' -> ' + entry.target)).toEqual([
      'docs/宿主集成-接口契约-v2.3增补.md -> tests/selection_store_selftest.mjs',
      'docs/宿主集成-接口契约-v2.3增补.md -> tests/host_routes_selftest.mjs',
      'docs/宿主集成-接口契约-v2.3增补.md -> tests/client_bundle_selftest.mjs',
      'docs/宿主集成-接口契约-v2.3增补.md -> tests/tui_host_selftest.mjs',
      'docs/宿主集成-接口契约-v2.3增补.md -> runtime/selectionstore.mjs',
      'MIGRATIONS.md -> docs/宿主集成-接口契约.md',
      'MIGRATIONS.md -> scripts/addons/dsh_blender_bridge/',
    ])
  })

  it('keeps the legacy hard checks: missing link target fails', () => {
    const root = tmpDir()
    writePackageJson(root)
    write(root, 'docs/guide.md', '[broken](missing.md)\n')
    const { status, report } = run(root)
    expect(status).toBe(1)
    expect(report!.legacy.failures.join('\n')).toContain('missing link target: missing.md')
  })

  it('keeps the legacy hard checks: unknown npm script fails', () => {
    const root = tmpDir()
    writePackageJson(root)
    write(root, 'docs/guide.md', 'Run npm run does-not-exist to start.\n')
    const { status, report } = run(root)
    expect(status).toBe(1)
    expect(report!.legacy.failures.join('\n')).toContain('unknown npm script: does-not-exist')
  })

  it('detects orphan docs and ratchets against the baseline', () => {
    const root = tmpDir()
    writePackageJson(root)
    write(root, 'docs/a.md', '# A\n')
    write(root, 'docs/b.md', '[A](a.md)\n')
    const first = run(root)
    expect(first.status).toBe(0)
    expect(first.report!.findings.orphanDocs).toEqual(['docs/b.md'])
    expect(first.report!.baseline.present).toBe(false)
    expect(run(root, ['--enforce']).status).toBe(1)
    expect(run(root, ['--write-baseline']).status).toBe(0)
    expect(run(root, ['--enforce']).status).toBe(0)
    write(root, 'docs/c.md', '# C\n')
    const drifted = run(root, ['--enforce'])
    expect(drifted.status).toBe(1)
    expect(drifted.report!.baseline.newOrphans).toEqual(['docs/c.md'])
    expect(run(root).status).toBe(0)
  })

  it('counts zh/en counterpart links as inbound links', () => {
    const root = tmpDir()
    writePackageJson(root)
    write(root, 'README.md', '[中文](docs/zh/README.md)\n')
    write(root, 'docs/zh/README.md', '[English](../../README.md)\n')
    const { status, report } = run(root, ['--enforce'])
    expect(status).toBe(0)
    expect(report!.findings.orphanDocs).toEqual([])
    expect(report!.findings.inbound['README.md']).toBe(1)
    expect(report!.findings.inbound['docs/zh/README.md']).toBe(1)
  })

  it('finds nonexistent path refs, including inline code, while skipping globs', () => {
    const root = tmpDir()
    writePackageJson(root)
    write(root, 'runtime/existing.ts', 'export const x = 1\n')
    write(
      root,
      'docs/refs.md',
      [
        '# refs',
        'See `runtime/missing.ts:run` and `runtime/existing.ts:run`.',
        'Glob src/plugin*.ts and dependency lib/ignored.js are skipped.',
      ].join('\n'),
    )
    write(root, 'docs/archive/link.md', '[g](../guide.md) and runtime/ghost.ts\n')
    write(root, 'docs/guide.md', '# G\n[refs](refs.md)\n')
    const { status, report } = run(root, ['--enforce'])
    expect(status).toBe(1)
    expect(report!.findings.missingRefs).toEqual([
      { doc: 'docs/refs.md', line: 2, target: 'runtime/missing.ts' },
    ])
    expect(report!.findings.orphanDocs).toEqual([])
  })

  it('treats a line shift as baseline drift, not a new finding', () => {
    const root = tmpDir()
    writePackageJson(root)
    write(root, 'docs/refs.md', '# refs\nSee `runtime/missing.ts`\n')
    expect(run(root, ['--write-baseline']).status).toBe(0)
    const baselinePath = path.join(root, 'config', 'doc-links-baseline.json')
    const baseline = JSON.parse(fs.readFileSync(baselinePath, 'utf8')) as Record<string, unknown>
    baseline.review = { owner: 'docs-owner', reviewBy: '2099-01-01', reason: 'frozen legacy ref' }
    fs.writeFileSync(baselinePath, JSON.stringify(baseline, null, 2) + '\n')
    write(root, 'docs/refs.md', '# refs\n\nSee `runtime/missing.ts`\n')
    const shifted = run(root, ['--enforce'])
    expect(shifted.status).toBe(0)
    expect(shifted.report!.baseline.matches).toBe(true)
  })

  it('requires a live owner/reason/reviewBy when the baseline accepts missing refs', () => {
    const root = tmpDir()
    writePackageJson(root)
    write(root, 'docs/refs.md', '# refs\nSee `runtime/missing.ts`\n')
    expect(run(root, ['--write-baseline']).status).toBe(0)
    const baselinePath = path.join(root, 'config', 'doc-links-baseline.json')
    const missingReview = run(root, ['--enforce'])
    expect(missingReview.status).toBe(1)
    expect(missingReview.report!.legacy.failures.join('\n')).toContain('必须提供 review')

    const baseline = JSON.parse(fs.readFileSync(baselinePath, 'utf8')) as Record<string, unknown>
    baseline.review = { owner: '', reviewBy: '2000-01-01', reason: '   ' }
    fs.writeFileSync(baselinePath, JSON.stringify(baseline, null, 2) + '\n')
    const invalid = run(root, ['--enforce'])
    expect(invalid.status).toBe(1)
    const invalidText = invalid.report!.legacy.failures.join('\n')
    expect(invalidText).toContain('review.owner 不能为空')
    expect(invalidText).toContain('review.reason 不能为空')
    expect(invalidText).toContain('review 已过期')

    baseline.review = { owner: 'docs-owner', reviewBy: '2099-01-01', reason: 'frozen legacy ref' }
    fs.writeFileSync(baselinePath, JSON.stringify(baseline, null, 2) + '\n')
    expect(run(root, ['--enforce']).status).toBe(0)
  })

  it('fails on a missing baseline review even without --enforce', () => {
    const root = tmpDir()
    writePackageJson(root)
    write(root, 'docs/refs.md', '# refs\nSee `runtime/missing.ts`\n')
    expect(run(root, ['--write-baseline']).status).toBe(0)
    const warn = run(root)
    expect(warn.status).toBe(1)
    expect(warn.report!.mode).toBe('warn')
    expect(warn.report!.legacy.failures.join('\n')).toContain('必须提供 review')
  })

  it('fails closed on a malformed baseline', () => {
    const root = tmpDir()
    writePackageJson(root)
    write(root, 'README.md', '# R\n')
    write(root, 'config/doc-links-baseline.json', '{not-json')
    const { status, report } = run(root)
    expect(status).toBe(1)
    expect(report!.legacy.failures.join('\n')).toContain('baseline 无法读取/解析')
  })
})
