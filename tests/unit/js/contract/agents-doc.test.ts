import { describe, expect, it } from 'vitest'
import fs from 'node:fs'
import {
  brokenRelativeLinks,
  hasCjk,
  lineCount,
  proseOnly,
  readText,
  repoPath,
} from '../../../helpers/docs'

const AGENTS = readText('AGENTS.md')
const PR_TEMPLATE_DIR = '.github/PULL_REQUEST_TEMPLATE'
const PR_TEMPLATES = ['bug_fix.md', 'feature.md', 'docs_update.md', 'refactor.md']
  .map((name) => ({ name, path: PR_TEMPLATE_DIR + '/' + name, text: readText(PR_TEMPLATE_DIR + '/' + name) }))
const TESTS_README = readText('tests/README.md')
const ADR_DIR = repoPath('docs/adr')
const pkg = JSON.parse(readText('package.json'))
const scripts: Record<string, string> = pkg.scripts ?? {}

const AI_DOCS = [
  'AGENTS.md',
  'docs/adr/README.md',
  'docs/adr/0001-runtime-ts.md',
  'docs/adr/0002-test-stack.md',
  'docs/adr/0003-addon-package-release.md',
  'docs/adr/0004-lib-build-release.md',
  '.github/PULL_REQUEST_TEMPLATE/bug_fix.md',
  '.github/PULL_REQUEST_TEMPLATE/feature.md',
  '.github/PULL_REQUEST_TEMPLATE/docs_update.md',
  '.github/PULL_REQUEST_TEMPLATE/refactor.md',
  'tests/README.md',
]

describe('AGENTS.md contract', () => {
  it('stays within the 120-line budget', () => {
    expect(lineCount(AGENTS)).toBeLessThanOrEqual(120)
  })

  it('keeps the required sections', () => {
    for (const heading of [
      '## 1. Commands',
      '## 2. Architecture map',
      '## 3. Hard rules',
      '## 4. Task routing',
      '## 5. Definition of done',
      '## 6. Disclosure',
      '## 7. Facts',
    ]) {
      expect(AGENTS).toContain(heading)
    }
    expect(AGENTS).toContain('single source')
  })

  it('documents build, typecheck, test, lint, and pack', () => {
    for (const command of ['npm run build', 'npm run typecheck', 'npm run test', 'npm run lint', 'npm pack']) {
      expect(AGENTS).toContain(command)
    }
  })

  it('only names scripts that exist, except rows marked planned', () => {
    const referenced = new Set([...AGENTS.matchAll(/npm run ([a-z0-9:_-]+)/gi)].map((match) => match[1]))
    expect(referenced.size).toBeGreaterThan(4)
    for (const name of referenced) {
      const line = AGENTS.split('\n').find((candidate) => candidate.includes('npm run ' + name)) ?? ''
      if (/planned/i.test(line)) continue
      expect(scripts, 'npm run ' + name + ' must exist in package.json').toHaveProperty([name])
    }
  })

  it('claims lint only now that T5 delivered the script', () => {
    const lintLine = AGENTS.split('\n').find((line) => line.includes('npm run lint')) ?? ''
    expect(lintLine).not.toMatch(/planned/i)
    expect(scripts).toHaveProperty('lint')
  })

  it('names only files and scripts that exist on disk', () => {
    const nodeRefs = [...AGENTS.matchAll(/node ([^\s`]+\.(?:mjs|js|ts))/g)].map((match) => match[1])
    expect(nodeRefs.length).toBeGreaterThan(0)
    for (const relative of nodeRefs) {
      expect(fs.existsSync(repoPath(relative)), relative + ' must exist').toBe(true)
    }
  })

  it('describes current and target architecture with task markers', () => {
    expect(AGENTS).toContain('| Layer | Target | Current')
    for (const marker of ['T4', 'T6', 'T7', 'T8', 'T9', 'T15']) expect(AGENTS).toContain(marker)
    expect(AGENTS).toMatch(/is the truth in this worktree/i)
  })

  it('has no broken relative link', () => {
    expect(brokenRelativeLinks('AGENTS.md')).toEqual([])
  })

  it('does not reference deleted files or retired tools', () => {
    for (const deleted of ['CLAUDE.md', 'docs/engineering-principles.md']) {
      expect(AGENTS).not.toContain(deleted)
      expect(fs.existsSync(repoPath(deleted))).toBe(false)
    }
    expect(AGENTS).not.toContain('docs/refactor')
    expect(fs.existsSync(repoPath('docs/refactor'))).toBe(false)
    expect(AGENTS).not.toContain('blender_rt_pick')
  })

  it('discloses agent work in two places', () => {
    expect(AGENTS).toContain('AI-Assisted:')
    expect(AGENTS).toMatch(/\*\*Commit\*\*/)
    expect(AGENTS).toMatch(/\*\*Pull request\*\*/)
    expect(AGENTS).toMatch(/disclos/i)
  })
})

describe('ADR set', () => {
  const adrs = [
    '0001-runtime-ts.md',
    '0002-test-stack.md',
    '0003-addon-package-release.md',
    '0004-lib-build-release.md',
  ]

  it('has the index, the four decisions, and no separate template file', () => {
    expect(fs.existsSync(repoPath('docs/adr/README.md'))).toBe(true)
    for (const adr of adrs) expect(fs.existsSync(repoPath('docs/adr', adr))).toBe(true)
    const templateFiles = fs.readdirSync(ADR_DIR).filter((name) => /template/i.test(name))
    expect(templateFiles).toEqual([])
  })

  it('indexes every ADR and keeps an inline template', () => {
    const index = readText('docs/adr/README.md')
    for (const adr of adrs) expect(index).toContain('(' + adr + ')')
    expect(index).toContain('## Template')
    expect(index).toContain('## Decision')
    expect(index).toMatch(/When to write one/i)
  })

  it('gives every ADR the same reviewable structure', () => {
    for (const adr of adrs) {
      const text = readText('docs/adr/' + adr)
      for (const heading of ['## Context', '## Decision', '## Consequences', '## Alternatives considered']) {
        expect(text, adr + ' must contain ' + heading).toContain(heading)
      }
      expect(brokenRelativeLinks('docs/adr/' + adr), adr + ' has broken links').toEqual([])
    }
  })
})

describe('pull request templates', () => {
  it('replaces the single template with a typed chooser set', () => {
    expect(fs.existsSync(repoPath('.github/pull_request_template.md'))).toBe(false)
    expect(fs.existsSync(repoPath(PR_TEMPLATE_DIR))).toBe(true)
    expect(PR_TEMPLATES.map((template) => template.name).sort()).toEqual([
      'bug_fix.md',
      'docs_update.md',
      'feature.md',
      'refactor.md',
    ])
  })

  it.each(PR_TEMPLATES)('$name carries the common review fields', ({ text }) => {
    for (const heading of [
      '## Summary',
      '## Task',
      '## Scope',
      '## Behavior matrix row',
      '## Test evidence',
      '## Build and packaging evidence',
      '## Risk',
      '## Rollback',
      '## Disclosure',
      '## Checklist',
    ]) {
      expect(text).toContain(heading)
    }
  })

  it.each(PR_TEMPLATES)('$name links to the shared rules instead of restating them', ({ text }) => {
    expect(text).toContain('AGENTS.md')
    expect(text).toContain('§1')
    expect(text).toContain('§6')
  })

  it('keeps the type-specific sections', () => {
    const byName = Object.fromEntries(PR_TEMPLATES.map(({ name, text }) => [name, text]))
    expect(byName['bug_fix.md']).toContain('## Reproduction')
    expect(byName['feature.md']).toContain('## Motivation')
    expect(byName['docs_update.md']).toContain('## Docs changed')
    expect(byName['refactor.md']).toContain('## No behavior change')
  })
})

describe('tests README', () => {
  it('stays within the 80-line budget and describes three layers', () => {
    expect(lineCount(TESTS_README)).toBeLessThanOrEqual(80)
    for (const layer of ['Unit', 'Integration', 'End-to-end']) expect(TESTS_README).toContain(layer)
    expect(TESTS_README).toContain('not** a fourth layer')
  })

  it('has no broken relative link', () => {
    expect(brokenRelativeLinks('tests/README.md')).toEqual([])
  })
})

describe('AI documentation language', () => {
  it('is English-only outside code and links', () => {
    for (const doc of AI_DOCS) {
      const prose = proseOnly(readText(doc))
      expect(hasCjk(prose), doc + ' must be English-only').toBe(false)
    }
  })
})
