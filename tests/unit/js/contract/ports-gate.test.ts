import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { spawnSync } from 'node:child_process'
import { afterEach, describe, expect, it } from 'vitest'

const SCRIPT = resolve('scripts/check-ports.mjs')
const roots: string[] = []

function makeRoot(): string {
  const root = mkdtempSync(join(tmpdir(), 'dsh-ports-'))
  roots.push(root)
  mkdirSync(join(root, 'docs'), { recursive: true })
  return root
}

function run(root: string) {
  return spawnSync(process.execPath, [SCRIPT, '--root', root], { encoding: 'utf8' })
}

afterEach(() => {
  for (const root of roots.splice(0)) rmSync(root, { recursive: true, force: true })
})

describe('check-ports gate', () => {
  it('accepts the 3.1 Gateway default', () => {
    const root = makeRoot()
    writeFileSync(join(root, 'docs', 'install.md'), 'Gateway: http://127.0.0.1:39877/health\n')
    const result = run(root)
    expect(result.status).toBe(0)
    expect(result.stdout).toContain('PASS')
  })

  it('rejects the legacy 9877 default in a current doc', () => {
    const root = makeRoot()
    writeFileSync(join(root, 'docs', 'install.md'), 'Gateway: http://127.0.0.1:9877/health\n')
    const result = run(root)
    expect(result.status).toBe(1)
    expect(result.stderr).toContain('docs/install.md:1')
    expect(result.stderr).toContain('39877')
  })

  it('does not scan tests, archives, or the changelog', () => {
    const root = makeRoot()
    mkdirSync(join(root, 'tests'), { recursive: true })
    mkdirSync(join(root, 'docs', 'archive'), { recursive: true })
    writeFileSync(join(root, 'tests', 'old.test.ts'), 'const port = 9877\n')
    writeFileSync(join(root, 'docs', 'archive', 'old.md'), 'Gateway: 9877\n')
    writeFileSync(join(root, 'CHANGELOG.md'), '3.0.x used 9877\n')
    expect(run(root).status).toBe(0)
  })
})
