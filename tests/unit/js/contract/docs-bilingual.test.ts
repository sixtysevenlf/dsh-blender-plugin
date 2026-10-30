import { describe, expect, it } from 'vitest'
import fs from 'node:fs'
import {
  brokenRelativeLinks,
  hasCjk,
  lineCount,
  proseOnly,
  readText,
  repositoryTextFiles,
  repoPath,
  sectionHeadings,
} from '../../../helpers/docs'

// The retired filename is assembled so this test file itself is clean of the residue it forbids.
const RETIRED_README = ['README', 'zh-CN.md'].join('.')
const README = readText('README.md')
const ZH_README = readText('zh-README.md')
const CONTRIBUTING = readText('CONTRIBUTING.md')
const ZH_CONTRIBUTING = readText('zh-CONTRIBUTING.md')

function top(text: string, lines = 8) {
  return text.split('\n').slice(0, lines).join('\n')
}

describe('human entry cross-links', () => {
  it('README points to the Chinese entry at the top', () => {
    expect(top(README)).toContain('[简体中文](zh-README.md)')
  })

  it('zh-README points back to English at the top', () => {
    expect(top(ZH_README)).toContain('[English](README.md)')
  })

  it('CONTRIBUTING points to the Chinese entry at the top', () => {
    expect(top(CONTRIBUTING)).toContain('[简体中文](zh-CONTRIBUTING.md)')
  })

  it('zh-CONTRIBUTING points back to English and to zh-README at the top', () => {
    expect(top(ZH_CONTRIBUTING)).toContain('[English](CONTRIBUTING.md)')
    expect(top(ZH_CONTRIBUTING, 12)).toContain('zh-README.md')
  })

  it('keeps the development sections synchronized', () => {
    for (const text of [README, ZH_README]) {
      expect(text).toContain('Node.js **22**')
      expect(text).toContain('PYTHON')
      expect(text).toContain('pytest')
      expect(text).toMatch(/virtualenv|venv/i)
      expect(text).toContain('AGENTS.md#1-commands-single-source')
    }
  })

  it('keeps both language versions structurally paired', () => {
    expect(sectionHeadings(ZH_README).length).toBe(sectionHeadings(README).length)
    expect(sectionHeadings(ZH_CONTRIBUTING).length).toBe(sectionHeadings(CONTRIBUTING).length)
    expect(lineCount(ZH_CONTRIBUTING)).toBeGreaterThanOrEqual(Math.floor(lineCount(CONTRIBUTING) * 0.7))
    expect(lineCount(ZH_CONTRIBUTING)).toBeGreaterThan(60)
    expect(lineCount(ZH_README)).toBeGreaterThan(150)
  })

  it('has no broken relative link in any human entry', () => {
    for (const path of ['README.md', 'zh-README.md', 'CONTRIBUTING.md', 'zh-CONTRIBUTING.md']) {
      expect(brokenRelativeLinks(path), path + ' has broken links').toEqual([])
    }
  })
})

describe('no duplicate or stale Chinese documentation', () => {
  it('does not keep the retired Chinese README file', () => {
    expect(fs.existsSync(repoPath(RETIRED_README))).toBe(false)
    const extra = fs.readdirSync(repoPath())
      .filter((name) => /^readme.*(zh|中文).*\.md$/i.test(name) && name !== 'zh-README.md')
    expect(extra).toEqual([])
  })

  it('does not reference the retired filename anywhere in the repository', () => {
    const offenders: string[] = []
    for (const relative of repositoryTextFiles()) {
      if (readText(relative).includes(RETIRED_README)) offenders.push(relative)
    }
    expect(offenders).toEqual([])
  })

  it('keeps AI documentation single-language', () => {
    expect(fs.existsSync(repoPath('zh-AGENTS.md'))).toBe(false)
    expect(fs.existsSync(repoPath('tests', RETIRED_README))).toBe(false)
    expect(fs.existsSync(repoPath('tests/zh-README.md'))).toBe(false)
    expect(proseOnly(CONTRIBUTING)).not.toContain('中文摘要')
  })

  it('does not mention tools that were removed', () => {
    for (const path of ['README.md', 'zh-README.md', 'CONTRIBUTING.md', 'zh-CONTRIBUTING.md', 'AGENTS.md', 'tests/README.md']) {
      expect(readText(path), path + ' must not cite a deleted tool').not.toContain('blender_rt_pick')
    }
  })

  it('keeps the deleted rule and metrics files deleted', () => {
    for (const path of ['CLAUDE.md', 'docs/engineering-principles.md', 'docs/refactor/metrics.md']) {
      expect(fs.existsSync(repoPath(path)), path + ' must stay deleted').toBe(false)
    }
  })
})

describe('user-facing Chinese documentation is preserved', () => {
  it('keeps the operation guide in Chinese', () => {
    const guide = readText('docs/操作教程.md')
    expect(hasCjk(guide)).toBe(true)
    expect(lineCount(guide)).toBeGreaterThan(100)
  })

  it('keeps the configuration reference in Chinese', () => {
    expect(hasCjk(readText('docs/配置参考.md'))).toBe(true)
  })
})
