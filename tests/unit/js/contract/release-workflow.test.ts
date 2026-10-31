import { describe, expect, it } from 'vitest'
import fs from 'node:fs'
import { repoPath } from '../../../helpers/docs'

const workflow = fs.readFileSync(repoPath('.github/workflows/release.yml'), 'utf8')

describe('manual CD workflow', () => {
  it('only runs on explicit workflow_dispatch', () => {
    expect(workflow).toMatch(/^on:\n {2}workflow_dispatch:/m)
    expect(workflow).not.toMatch(/^ {2}(push|pull_request|pull_request_target|schedule):/m)
  })

  it('defaults to a dry run and keeps npm publishing opt-in', () => {
    expect(workflow).toContain("default: '3.0.0'")
    expect(workflow).toMatch(/dry_run:[\s\S]*?default: true/)
    expect(workflow).toMatch(/publish_npm:[\s\S]*?default: false/)
    expect(workflow).toContain("github.event.inputs.dry_run == 'false'")
    expect(workflow).toContain("github.event.inputs.publish_npm == 'true'")
  })

  it('requires the release environment and only creates a draft release', () => {
    expect(workflow).toContain('name: release')
    expect(workflow).toContain('--draft')
    expect(workflow).toContain('--target')
    expect(workflow).toContain('gh release create')
  })

  it('publishes npm from the packed tarball with provenance', () => {
    expect(workflow).toContain('npm publish release/*.tgz --access public --provenance')
  })
})
