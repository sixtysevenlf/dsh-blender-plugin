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
const SPEC = readText('docs/refactor/spec.md')
const PR_TEMPLATE = readText('.github/pull_request_template.md')
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
  'docs/refactor/spec.md',
  '.github/pull_request_template.md',
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

  it('marks lint as planned instead of claiming it works today', () => {
    const lintLine = AGENTS.split('\n').find((line) => line.includes('npm run lint')) ?? ''
    expect(lintLine).toMatch(/planned/i)
    expect(scripts).not.toHaveProperty('lint')
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
    for (const deleted of ['CLAUDE.md', 'docs/engineering-principles.md', 'docs/refactor/metrics.md']) {
      expect(AGENTS).not.toContain(deleted)
      expect(fs.existsSync(repoPath(deleted))).toBe(false)
    }
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

describe('pull request template', () => {
  it('carries scope, matrix row, evidence, risk, rollback, disclosure, and checklist', () => {
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
      expect(PR_TEMPLATE).toContain(heading)
    }
  })

  it('links to AGENTS instead of restating the command table', () => {
    expect(PR_TEMPLATE).toContain('AGENTS.md')
    expect(PR_TEMPLATE).toContain('§6')
  })
})

describe('refactor specification', () => {
  it('contains the required sections', () => {
    for (const heading of [
      '## 1. Problem',
      '## 2. Goals',
      '## 3. Non-goals',
      '## 4. Constraints',
      '## 5. Normative clauses',
      '## 6. Acceptance scenarios',
      '## 7. Behavior matrix',
      '## 8. Rollback',
      '## 9. Metrics',
      '## 10. Traceability',
    ]) {
      expect(SPEC).toContain(heading)
    }
    expect(SPEC).toContain('SHALL')
    expect(SPEC).toContain('WHEN')
    expect(SPEC).toContain('THEN')
    expect(SPEC).toContain('BEHAVIOR_MATRIX.md')
  })

  it('has one clause for every release gate', () => {
    const gates = [
      ...Array.from({ length: 8 }, (_, index) => 'A' + (index + 1)),
      ...Array.from({ length: 7 }, (_, index) => 'B' + (index + 1)),
      ...Array.from({ length: 8 }, (_, index) => 'C' + (index + 1)),
      ...Array.from({ length: 6 }, (_, index) => 'D' + (index + 1)),
      ...Array.from({ length: 6 }, (_, index) => 'E' + (index + 1)),
      ...Array.from({ length: 4 }, (_, index) => 'F' + (index + 1)),
    ]
    for (const gate of gates) {
      expect(SPEC, gate + ' must be traceable to a clause').toContain('**' + gate + ' —')
    }
  })

  it('records the delivery and collaboration metrics baseline', () => {
    for (const metric of [
      'Release interval',
      'Change failure rate',
      'Rollback count',
      'PR size',
      'Review time',
      'Rework rate',
      'Flaky rate',
      'Agent first-pass rate',
    ]) {
      expect(SPEC).toContain(metric)
    }
    expect(SPEC).toContain('Not instrumented')
    expect(SPEC).toMatch(/SHALL NOT be recorded as output/i)
    expect(SPEC).toMatch(/Median 386/)
  })

  it('has no broken relative link', () => {
    expect(brokenRelativeLinks('docs/refactor/spec.md')).toEqual([])
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
