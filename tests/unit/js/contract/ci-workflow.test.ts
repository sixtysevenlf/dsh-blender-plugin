import { describe, expect, it } from 'vitest'
import fs from 'node:fs'
import { repoPath } from '../../../helpers/docs'

const pr = fs.readFileSync(repoPath('.github/workflows/pr.yml'), 'utf8')
const release = fs.readFileSync(repoPath('.github/workflows/release.yml'), 'utf8')
const all = pr + '\n' + release
const pkg = JSON.parse(fs.readFileSync(repoPath('package.json'), 'utf8')) as {
  scripts: Record<string, string>
}
const checkBuildSource = fs.readFileSync(repoPath('scripts/check-build.mjs'), 'utf8')

const actionRefs = (text: string): string[] =>
  [...text.matchAll(/^\s*-?\s*uses:\s*(\S+)/gm)].map((m) => m[1])
const checkoutSteps = (text: string): { total: number; protected: number } => {
  const lines = text.split('\n')
  let total = 0
  let protectedCount = 0
  for (let i = 0; i < lines.length; i += 1) {
    if (!lines[i].includes('uses: actions/checkout@')) continue
    total += 1
    if (
      lines
        .slice(i, i + 6)
        .join('\n')
        .includes('persist-credentials: false')
    )
      protectedCount += 1
  }
  return { total, protected: protectedCount }
}

describe('CI workflow hygiene', () => {
  it('uses safe triggers and least-privilege tokens', () => {
    expect(pr).toMatch(/^on:\n {2}pull_request:/m)
    expect(pr).toMatch(/^ {4}branches: \[main\]$/m)
    expect(all).not.toMatch(/pull_request_target|workflow_run/)
    expect(pr).toMatch(/^permissions:\n {2}contents: read$/m)
    expect(release).toMatch(/^permissions:\n {2}contents: read$/m)
    expect(release).toMatch(/permissions:\n {6}contents: write/)
    expect(release).toMatch(/id-token: write/)
  })

  it('pins every action to a full commit SHA', () => {
    const refs = actionRefs(all)
    expect(refs.length).toBeGreaterThan(0)
    for (const ref of refs) expect(ref).toMatch(/@[0-9a-f]{40}$/)
  })

  it('does not persist checkout credentials', () => {
    for (const text of [pr, release]) {
      const { total, protected: protectedCount } = checkoutSteps(text)
      expect(total).toBeGreaterThan(0)
      expect(protectedCount).toBe(total)
    }
  })

  it('builds before running check:build', () => {
    for (const text of [pr, release]) {
      const build = text.indexOf('npm run build\n')
      const checkBuild = text.indexOf('npm run check:build')
      expect(build).toBeGreaterThan(-1)
      expect(checkBuild).toBeGreaterThan(-1)
      expect(build).toBeLessThan(checkBuild)
    }
  })

  it('keeps manual CD inputs typed and gated', () => {
    expect(release).toContain('if: ${{ !inputs.dry_run }}')
    expect(release).toContain('if: ${{ !inputs.dry_run && inputs.publish_npm }}')
    expect(release).not.toContain('github.event.inputs')
    expect(release).toContain('name: release')
    expect(release).toContain('--draft')
  })
})

describe('build hooks and VCS gate wiring', () => {
  it('keeps prepack build-only', () => {
    expect(pkg.scripts.prepack).toBe('npm run build')
  })

  it('keeps git out of check-build', () => {
    expect(checkBuildSource).not.toContain("spawnSync('git'")
  })

  it('runs git hygiene in both workflows', () => {
    for (const text of [pr, release]) expect(text).toContain('npm run check:git-hygiene')
  })
})
