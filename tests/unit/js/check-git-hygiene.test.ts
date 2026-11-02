import { afterAll, describe, expect, it } from 'vitest'
import { spawnSync } from 'node:child_process'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { pkgPath } from '../../helpers/pkg'

const SCRIPT = pkgPath('scripts', 'check-git-hygiene.mjs')
const tmpDirs: string[] = []

type Report = {
  ok: boolean
  failures: string[]
  steps: { step: string; exit: number; output?: string }[]
}

function tmpDir(): string {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'dsh-gh-'))
  tmpDirs.push(dir)
  return dir
}

function git(cwd: string, args: string[]): void {
  const r = spawnSync('git', args, { cwd, encoding: 'utf8' })
  if (r.status !== 0) {
    throw new Error('git ' + args.join(' ') + ' failed: ' + String(r.stderr || r.stdout || r.error))
  }
}

function runJson(root: string): { status: number | null; report: Report | null } {
  const r = spawnSync(process.execPath, [SCRIPT, '--json', '--root', root], {
    cwd: pkgPath(),
    encoding: 'utf8',
  })
  let report: Report | null = null
  if (String(r.stdout || '').trim()) report = JSON.parse(String(r.stdout)) as Report
  return { status: r.status, report }
}

describe('check-git-hygiene', () => {
  it('fails closed when the directory is not a git checkout', () => {
    const { status, report } = runJson(tmpDir())
    expect(status).toBe(1)
    expect(report).not.toBeNull()
    expect(report!.ok).toBe(false)
    expect(report!.failures.join('\n')).toContain('不是 git 检出')
  })

  it('passes in a clean checkout with lib/ ignored', () => {
    const dir = tmpDir()
    git(dir, ['init'])
    fs.writeFileSync(path.join(dir, '.gitignore'), '/lib/\n')
    const { status, report } = runJson(dir)
    expect(status).toBe(0)
    expect(report!.ok).toBe(true)
    expect(report!.failures).toEqual([])
    expect(report!.steps.find((s) => s.step === 'lib-not-tracked')?.exit).toBe(0)
  })

  it('fails when lib/ is tracked', () => {
    const dir = tmpDir()
    git(dir, ['init'])
    fs.writeFileSync(path.join(dir, '.gitignore'), '/lib/\n')
    fs.mkdirSync(path.join(dir, 'lib'))
    fs.writeFileSync(path.join(dir, 'lib', 'x.js'), 'export {}\n')
    git(dir, ['add', '-f', 'lib/x.js'])
    const { status, report } = runJson(dir)
    expect(status).toBe(1)
    expect(report!.ok).toBe(false)
    expect(report!.failures.join('\n')).toContain('仍被 git 跟踪')
    expect(report!.steps.find((s) => s.step === 'lib-not-tracked')?.exit).toBe(1)
  })

  it('fails when .gitignore no longer ignores lib/', () => {
    const dir = tmpDir()
    git(dir, ['init'])
    const { status, report } = runJson(dir)
    expect(status).toBe(1)
    expect(report!.failures.join('\n')).toContain('.gitignore 缺少 /lib 忽略规则')
  })

  it('fails when --root is a subdirectory of the checkout', () => {
    const dir = tmpDir()
    git(dir, ['init'])
    const sub = path.join(dir, 'sub')
    fs.mkdirSync(sub)
    const { status, report } = runJson(sub)
    expect(status).toBe(1)
    expect(report!.failures.join('\n')).toContain('检出根目录不是当前目录')
  })
})

afterAll(() => {
  for (const dir of tmpDirs) fs.rmSync(dir, { recursive: true, force: true })
})
