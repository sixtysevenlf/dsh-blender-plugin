import { describe, it, expect, afterEach } from 'vitest'
import { spawnSync } from 'node:child_process'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

const ROOT = path.resolve(__dirname, '../../../..')
const CHECKER = path.join(ROOT, 'scripts', 'check-tsconfig-layout.mjs')

const EMPTY_PROJECTS: Record<string, string> = {
  'tsconfig.json': JSON.stringify({ files: [] }),
  'runtime/tsconfig.json': JSON.stringify({ files: [] }),
  'tests/tsconfig.json': JSON.stringify({ files: [] }),
  'scripts/tsconfig.json': JSON.stringify({ files: [] }),
}

const dirs: string[] = []

function fixture(files: Record<string, string>): string {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'tsconfig-layout-'))
  dirs.push(dir)
  for (const [name, content] of Object.entries(files)) {
    const target = path.join(dir, name)
    fs.mkdirSync(path.dirname(target), { recursive: true })
    fs.writeFileSync(target, content, 'utf8')
  }
  return dir
}

function run(root: string) {
  return spawnSync(process.execPath, [CHECKER, '--root', root], { encoding: 'utf8' })
}

afterEach(() => {
  while (dirs.length) fs.rmSync(dirs.pop() as string, { recursive: true, force: true })
})

describe('tsconfig layout checker', () => {
  it('accepts the current repository', () => {
    const result = run(ROOT)
    const output = String(result.stdout || '') + String(result.stderr || '')
    expect(output).not.toContain('R1')
    expect(result.status).toBe(0)
  })

  it('rejects a TypeScript file that belongs to no project', () => {
    const root = fixture({ ...EMPTY_PROJECTS, 'stray.ts': 'export {}\n' })
    const result = run(root)
    expect(result.status).not.toBe(0)
    expect(String(result.stderr)).toContain('stray.ts')
    expect(String(result.stderr)).toContain('R1')
  })

  it('rejects a file claimed by two projects', () => {
    const root = fixture({
      ...EMPTY_PROJECTS,
      'tsconfig.json': JSON.stringify({ files: ['tests/dup.ts'] }),
      'tests/tsconfig.json': JSON.stringify({ files: ['dup.ts'] }),
      'tests/dup.ts': 'export {}\n',
    })
    const result = run(root)
    expect(result.status).not.toBe(0)
    expect(String(result.stderr)).toContain('tests/dup.ts')
  })

  it('rejects a root-level tsconfig.*.json', () => {
    const root = fixture({ ...EMPTY_PROJECTS, 'tsconfig.extra.json': '{}\n' })
    const result = run(root)
    expect(result.status).not.toBe(0)
    expect(String(result.stderr)).toContain('tsconfig.extra.json')
    expect(String(result.stderr)).toContain('R2')
  })

  it('rejects a build reference that points at a missing file', () => {
    const root = fixture({
      ...EMPTY_PROJECTS,
      'scripts/build.mjs': "const TARGETS = { runtime: { config: 'missing/tsconfig.json' } }\n",
    })
    const result = run(root)
    expect(result.status).not.toBe(0)
    expect(String(result.stderr)).toContain('missing/tsconfig.json')
    expect(String(result.stderr)).toContain('R3')
  })

  it('rejects a live file that still mentions a retired config name', () => {
    const root = fixture({ ...EMPTY_PROJECTS, 'README.md': 'see tsconfig.runtime.json\n' })
    const result = run(root)
    expect(result.status).not.toBe(0)
    expect(String(result.stderr)).toContain('tsconfig.runtime.json')
    expect(String(result.stderr)).toContain('R4')
  })
})
