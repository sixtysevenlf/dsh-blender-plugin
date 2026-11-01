import { describe, expect, it } from 'vitest'
import fs from 'node:fs'
import { repoPath } from '../../../helpers/docs'

const readJson = (relative: string): any =>
  JSON.parse(fs.readFileSync(repoPath(relative), 'utf8')) as any

const thresholds = readJson('config/coverage-thresholds.json')
const baseline = readJson('config/coverage-baseline.json')
const moveManifest = readJson('config/coverage-move-manifest.json')

describe('coverage policy', () => {
  it('keeps the baseline and thresholds on the same commit', () => {
    expect(thresholds.version).toBe(2)
    expect(baseline.version).toBe(2)
    expect(thresholds.baselineCommit).toBe(baseline.commit)
    expect(thresholds.baselineDate).toBe(baseline.date)
  })

  it('never sets a JS floor below the recorded baseline', () => {
    const floors = thresholds.javascript.globalFloor
    expect(floors.lines).toBeGreaterThanOrEqual(baseline.javascript.lines)
    expect(floors.branches).toBeGreaterThanOrEqual(baseline.javascript.branches)
    expect(floors.functions).toBeGreaterThanOrEqual(baseline.javascript.functions)
    expect(thresholds.javascript.changedLinesFloor.lines).toBeGreaterThanOrEqual(
      baseline.changedLines.lines,
    )
    expect(thresholds.javascript.changedLinesFloor.against).toBe(baseline.changedLines.against)
  })

  it('keeps long-term quality floors under the measured values', () => {
    // F4.1: an empty criticalModules list would make the loop below vacuous.
    expect(thresholds.javascript.criticalModules).toHaveLength(5)
    expect(Object.keys(baseline.criticalModules)).toHaveLength(6)
    for (const mod of thresholds.javascript.criticalModules) {
      const measured = baseline.criticalModules[mod.module]
      expect(measured, mod.module).toBeDefined()
      expect(measured.lines).toBeGreaterThanOrEqual(mod.lines)
      expect(measured.branches).toBeGreaterThanOrEqual(mod.branches)
    }
    expect(baseline.python.scopeLines).toBeGreaterThanOrEqual(thresholds.python.floor.lines)
    expect(baseline.python.scopeBranches).toBeGreaterThanOrEqual(thresholds.python.floor.branches)
    expect(baseline.python.coveredLines).toBeLessThanOrEqual(baseline.python.scopeLinesTotal)
    expect(baseline.python.coveredBranches).toBeLessThanOrEqual(baseline.python.scopeBranchesTotal)
  })

  it('tracks execution-engine modules with measured ratchet floors', () => {
    expect(Array.isArray(thresholds.javascript.executionModules)).toBe(true)
    expect(thresholds.javascript.executionModules).toHaveLength(9)
    expect(Object.keys(baseline.executionModules)).toHaveLength(13)
    for (const mod of thresholds.javascript.executionModules) {
      const measured = baseline.executionModules[mod.module]
      expect(measured, mod.module).toBeDefined()
      expect(measured.lines).toBeGreaterThanOrEqual(mod.lines)
      expect(measured.branches).toBeGreaterThanOrEqual(mod.branches)
    }
  })

  it('applies the T16c calibrated floors from the canonical CI run', () => {
    expect(thresholds.javascript.globalFloor).toEqual({
      lines: 73.2,
      branches: 68.7,
      functions: 70.4,
    })
    expect(thresholds.javascript.changedLinesFloor.lines).toBe(73.4)
    expect(thresholds.javascript.globalFloor.lines).toBeGreaterThan(baseline.javascript.lines)
    expect(thresholds.javascript.changedLinesFloor.lines).toBeGreaterThan(
      baseline.changedLines.lines,
    )
  })

  it('records a 100% E2E baseline and an explicit move manifest', () => {
    const e2e = baseline.e2e
    expect(e2e.planned).toBe(0)
    expect((e2e.covered / (e2e.behaviorMatrixRows - e2e.exempt)) * 100).toBeGreaterThanOrEqual(
      thresholds.e2e.scenarioCoverage,
    )
    expect(moveManifest.generatedFrom).toMatch(new RegExp(baseline.commit.slice(0, 7) + '$'))
    expect(Array.isArray(moveManifest.entries)).toBe(true)
  })
})
