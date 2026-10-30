import { execFileSync } from 'node:child_process'
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { describe, expect, it } from 'vitest'
import { PKG_ROOT, pkgPath } from '../../../helpers/pkg'

// The matrix is the release gate: a new tool/command/op/route without a row must
// fail CI, and a "covered" row without a real test reference must fail too.
describe('behavior matrix release gate', () => {
  it('passes against the live catalogs and source files', () => {
    const output = execFileSync(
      process.execPath,
      [pkgPath('scripts/check-behavior-matrix.mjs'), '--release'],
      { cwd: PKG_ROOT, encoding: 'utf8', timeout: 20000 },
    ).trim()
    expect(output).toContain(
      'OK behavior matrix: 347 rows (345 covered, 0 planned, 2 exempt)',
    )
  })

  it('exits nonzero when a planned row survives the release gate', () => {
    const dir = mkdtempSync(path.join(os.tmpdir(), 'dsh-behavior-matrix-'))
    const matrix = path.join(dir, 'BEHAVIOR_MATRIX.md')
    try {
      const lines = readFileSync(
        pkgPath('tests/BEHAVIOR_MATRIX.md'),
        'utf8',
      ).replace(/\r\n/g, '\n').split('\n')
      const index = lines.findIndex((line) => line.startsWith('| CMD-ping |'))
      expect(index).toBeGreaterThan(0)
      const cells = lines[index].split('|')
      cells[8] = ' planned '
      lines[index] = cells.join('|')
      writeFileSync(matrix, lines.join('\n'))
      let failure: { status?: number; stderr?: string } | null = null
      try {
        execFileSync(
          process.execPath,
          [pkgPath('scripts/check-behavior-matrix.mjs'), '--release', '--matrix', matrix],
          { cwd: PKG_ROOT, encoding: 'utf8', timeout: 20000, stdio: ['ignore', 'pipe', 'pipe'] },
        )
      } catch (error) {
        failure = error as { status?: number; stderr?: string }
      }
      expect(failure).not.toBeNull()
      expect(failure?.status).toBe(1)
      expect(String(failure?.stderr)).toContain('release gate: 1 planned row(s) remain')
    } finally {
      rmSync(dir, { recursive: true, force: true })
    }
  })
})
