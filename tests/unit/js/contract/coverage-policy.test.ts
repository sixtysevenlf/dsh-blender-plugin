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

  it('keeps JS floors inside the calibrated buffer below the measured baseline', () => {
    const floors = thresholds.javascript.globalFloor
    const measured = baseline.javascript
    for (const key of ['lines', 'branches', 'functions'] as const) {
      expect(floors[key], key).toBeLessThanOrEqual(measured[key])
      expect(measured[key] - floors[key], key).toBeGreaterThanOrEqual(1.4)
      expect(measured[key] - floors[key], key).toBeLessThanOrEqual(1.6)
    }
    const changedFloor = thresholds.javascript.changedLinesFloor.lines
    expect(changedFloor).toBeLessThanOrEqual(baseline.changedLines.lines)
    expect(baseline.changedLines.lines - changedFloor).toBeGreaterThanOrEqual(1.4)
    expect(baseline.changedLines.lines - changedFloor).toBeLessThanOrEqual(1.6)
    expect(thresholds.javascript.changedLinesFloor.against).toBe(baseline.changedLines.against)
  })

  it('keeps long-term quality floors under the measured values', () => {
    // an empty criticalModules list would make the loop below vacuous.
    // The baseline tracks exactly the five threshold modules; the former sixth key
    // (runtime/catalog.ts) was a stale phantom and is gone (3.2).
    expect(thresholds.javascript.criticalModules).toHaveLength(5)
    expect(Object.keys(baseline.criticalModules)).toHaveLength(5)
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

  it('applies the coverage-v8 5 re-baselined floors from the canonical CI run', () => {
    expect(thresholds.javascript.globalFloor).toEqual({
      lines: 70.4,
      branches: 54.4,
      functions: 68,
    })
    expect(thresholds.javascript.changedLinesFloor.lines).toBe(66.1)
    expect(thresholds.javascript.globalFloor.lines).toBeLessThan(baseline.javascript.lines)
    expect(thresholds.javascript.changedLinesFloor.lines).toBeLessThan(baseline.changedLines.lines)
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
