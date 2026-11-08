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

  it('validates the addon package with a pinned Blender 4.2 LTS validator', () => {
    expect(pr).toContain('blender-4.2.10-linux-x64.tar.xz')
    expect(pr).toContain('key: blender-4.2.10-linux-x64')
    expect(pr).toContain('--command extension validate addon/dsh_blender_bridge.zip')
    expect(pr).toContain('Reject an invalid extension package')
  })

  it('copies exactly one extension artifact into the release assets', () => {
    const copied = [...release.matchAll(/\bcp\s+(\S+\.zip)\s+release-assets\//g)].map((m) => m[1])
    expect(copied).toEqual(['addon/dsh_blender_bridge.zip'])
    expect(release).not.toMatch(/cp\s+\S*legacy\S*\.zip/)
  })

  it('keeps manual CD inputs typed and gated', () => {
    expect(release).toContain('if: ${{ !inputs.dry_run }}')
    expect(release).toContain('if: ${{ !inputs.dry_run && inputs.publish_npm }}')
    expect(release).not.toContain('github.event.inputs')
    expect(release).toContain('name: release')
    expect(release).toContain('--draft')
  })

  it('gates the extension upload behind an explicit opt-in and a real token', () => {
    expect(release).toContain('publish_extension:')
    expect(release).toContain("description: 'Upload the extension zip to extensions.blender.org")
    expect(release).toMatch(
      /publish_extension:\n\s+description:.*\n\s+required: true\n\s+default: false/,
    )
    expect(release).toContain('if: ${{ !inputs.dry_run && inputs.publish_extension }}')
    expect(release).toContain('BLENDER_EXTENSIONS_TOKEN is not configured')
    expect(release).toContain('npm run publish:extension --')
    expect(release).toContain('--notes-file release/extension-release-notes.md')
    expect(release).toContain('submitted manually at https://extensions.blender.org/')
  })

  it('validates the addon manifest and protocol against one gateway schema', () => {
    expect(release).toContain('const protocol = protocolSource.match')
    expect(release).toContain('const addonVersion = manifest.match')
    expect(release).toContain('addon protocol.py and config/gateway.json disagree')
    expect(release).toContain('blender_manifest.toml and config/gateway.json disagree')
    expect(release).not.toContain('addon protocol.py must keep protocol 11')
    expect(release).not.toContain('addon protocol.py must keep addon version 1.11')
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
