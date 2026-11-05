import { afterAll, describe, expect, it } from 'vitest'
import { spawnSync } from 'node:child_process'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { pkgPath } from '../../../helpers/pkg'

const SCRIPT = pkgPath('scripts', 'check-plan-ids.mjs')
const tmpDirs: string[] = []

function tmpDir(): string {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'dsh-plan-ids-'))
  tmpDirs.push(dir)
  return dir
}

afterAll(() => {
  for (const dir of tmpDirs) fs.rmSync(dir, { recursive: true, force: true })
})

type PrefixRule = { prefix: string; kind: string }
type Exemption = { path: string; reason: string; prefixes?: string[] }
type Config = {
  version: number
  scan: { extensions: string[]; skipDirs: string[]; skipFiles: string[] }
  prefixes: PrefixRule[]
  exemptions: Exemption[]
}
type Hit = {
  file: string
  line: number
  column: number
  token: string
  prefix: string
  kind: string
  exempt: boolean
}
type Report = {
  ok: boolean
  mode: string
  scannedFiles: number
  totals: { all: number; exempt: number; residual: number }
  byPrefix: Record<string, number>
  byKind: Record<string, number>
  fileCount: number
  topFiles: { file: string; count: number; exempt: number; residual: number }[]
  residualHits: Hit[]
  baseline: {
    path: string
    present: boolean
    matches: boolean
    recordedResidual: number
    newResidual: number
    regressions: { file: string; residual: number; recorded: number }[]
  }
  failures: string[]
}

function baseConfig(overrides: Partial<Config> = {}): Config {
  return {
    version: 1,
    scan: { extensions: ['.ts', '.py', '.md'], skipDirs: ['node_modules'], skipFiles: [] },
    prefixes: [
      { prefix: 'D', kind: 'decision' },
      { prefix: 'F', kind: 'feature' },
      { prefix: 'P', kind: 'priority' },
      { prefix: 'S', kind: 'slice' },
      { prefix: 'T', kind: 'task' },
    ],
    exemptions: [],
    ...overrides,
  }
}

function write(root: string, relPath: string, content: string): void {
  const file = path.join(root, relPath)
  fs.mkdirSync(path.dirname(file), { recursive: true })
  fs.writeFileSync(file, content)
}

function writeConfig(root: string, config: Config): void {
  write(root, 'config/plan-ids.json', JSON.stringify(config, null, 2))
}

function fixture(files: Record<string, string>, config: Config = baseConfig()): string {
  const root = tmpDir()
  writeConfig(root, config)
  for (const [relPath, content] of Object.entries(files)) write(root, relPath, content)
  return root
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

describe('check-plan-ids', () => {
  it('inventories ID-shaped tokens in warn mode and exits 0', () => {
    const root = fixture({
      'src/a.ts': [
        '// D3 legacy flag',
        '// F401 is a lint code, not a plan id',
        '// -A3 is a grep flag, not a plan id',
        "const label = 'F4.2'",
        '// P01 and MP4 are not plan ids',
      ].join('\n'),
    })
    const { status, report } = run(root)
    expect(status).toBe(0)
    expect(report).not.toBeNull()
    expect(report!.mode).toBe('warn')
    expect(report!.ok).toBe(true)
    expect(report!.totals).toEqual({ all: 2, exempt: 0, residual: 2 })
    expect(report!.byPrefix).toEqual({ D: 1, F: 1 })
    expect(report!.residualHits.map((hit) => hit.token).sort()).toEqual(['D3', 'F4.2'])
    expect(report!.residualHits.find((hit) => hit.token === 'D3')?.line).toBe(1)
    expect(report!.baseline.present).toBe(false)
  })

  it('enforce fails on residual when the baseline is missing', () => {
    const root = fixture({ 'src/a.ts': '// T14\n' })
    const { status, report } = run(root, ['--enforce'])
    expect(status).toBe(1)
    expect(report!.ok).toBe(false)
    expect(report!.baseline.newResidual).toBe(1)
    expect(report!.baseline.regressions[0].file).toBe('src/a.ts')
  })

  it('write-baseline then enforce stays green until a new token appears', () => {
    const root = fixture({ 'src/a.ts': '// T14\n' })
    const written = run(root, ['--write-baseline'])
    expect(written.status).toBe(0)
    expect(written.report!.baseline.present).toBe(true)
    expect(run(root, ['--enforce']).status).toBe(0)
    const warned = run(root)
    expect(warned.status).toBe(0)
    expect(warned.report!.baseline.matches).toBe(true)
    write(root, 'src/b.ts', '// D7\n')
    const enforced = run(root, ['--enforce'])
    expect(enforced.status).toBe(1)
    expect(enforced.report!.baseline.regressions.map((item) => item.file)).toEqual(['src/b.ts'])
    expect(run(root).status).toBe(0)
  })

  it('exemptions remove declared domain tokens from the residual', () => {
    const root = fixture(
      { 'CHANGELOG.md': 'T1 historical entry\n', 'skills/x.md': 'S0 stage and S8 stage\n' },
      baseConfig({
        exemptions: [
          { path: 'CHANGELOG.md', reason: 'historical changelog entries' },
          { path: 'skills/', reason: 'domain vocabulary in skill docs' },
        ],
      }),
    )
    const { status, report } = run(root, ['--enforce'])
    expect(status).toBe(0)
    expect(report!.totals).toEqual({ all: 3, exempt: 3, residual: 0 })
    expect(report!.byPrefix).toEqual({})
  })

  it('exemption prefixes narrow what is exempt', () => {
    const root = fixture(
      { 'docs/guide.md': 'D3 decision and P2 priority\n' },
      baseConfig({
        exemptions: [
          {
            path: 'docs/guide.md',
            prefixes: ['D'],
            reason: 'decision ids are documented vocabulary',
          },
        ],
      }),
    )
    const { status, report } = run(root, ['--enforce'])
    expect(status).toBe(1)
    expect(report!.residualHits.map((hit) => hit.token)).toEqual(['P2'])
  })

  it('rejects invalid config entries', () => {
    const cases: { config: Config; message: string }[] = [
      {
        config: baseConfig({ prefixes: [{ prefix: '9', kind: 'bad' }] }),
        message: 'prefix 必须是单个大写字母',
      },
      {
        config: baseConfig({ exemptions: [{ path: 'CHANGELOG.md', reason: 'short' }] }),
        message: 'reason 至少',
      },
      {
        config: baseConfig({
          exemptions: [{ path: 'docs/ghost.md', reason: 'nonexistent exemption path' }],
        }),
        message: 'path 不存在',
      },
    ]
    for (const item of cases) {
      const root = tmpDir()
      writeConfig(root, item.config)
      const { status, report } = run(root)
      expect(status).toBe(1)
      expect(report!.failures.join('\n')).toContain(item.message)
    }
  })

  it('checks the checked-in config and baseline', () => {
    const { status, report } = run(pkgPath())
    expect(status).toBe(0)
    expect(report).not.toBeNull()
    expect(report!.ok).toBe(true)
    // 终态：仓库零残留；新增的 plan-ID 形状 token 必须先清理或按需豁免。
    expect(report!.totals.residual).toBe(0)
    expect(report!.baseline.present).toBe(true)
    expect(report!.baseline.matches).toBe(true)
    expect(report!.baseline.recordedResidual).toBe(0)
  })

  it('matches the recorded baseline regardless of JSON file key order', () => {
    // 复现 Linux CI 的失败：同一份扫描结果，仅因为 JSON 里 files 键的顺序不同
    // （localeCompare 受平台 locale 影响）就不应判定为基线漂移。
    const root = fixture({ 'src/a.ts': '// T14\n', 'src/b.ts': '// D7\n' })
    const written = run(root, ['--write-baseline'])
    expect(written.status).toBe(0)
    const baselineFile = path.join(root, 'config/plan-id-baseline.json')
    const baseline = JSON.parse(fs.readFileSync(baselineFile, 'utf8')) as {
      files: Record<string, unknown>
    }
    baseline.files = Object.fromEntries(Object.entries(baseline.files).reverse())
    fs.writeFileSync(baselineFile, JSON.stringify(baseline, null, 2) + '\n')
    const { status, report } = run(root)
    expect(status).toBe(0)
    expect(report!.baseline.matches).toBe(true)
    expect(report!.baseline.recordedResidual).toBe(2)
  })
})
