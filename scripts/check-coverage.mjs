#!/usr/bin/env node
// C5 coverage gate: single source of thresholds for local gate and CI.
// Inputs: coverage/coverage-summary.json (vitest json-summary),
//         coverage/diff-cover.json (diff-cover --json-report),
//         coverage/python-coverage.json (pytest --cov-report=json).
// E2E: scripts/check-behavior-matrix.mjs --release.
import fs from 'node:fs'
import path from 'node:path'
import { spawnSync } from 'node:child_process'

const root = path.resolve(import.meta.dirname, '..')
const readJson = (rel) => JSON.parse(fs.readFileSync(path.join(root, rel), 'utf8'))
const norm = (s) => s.replace(/\\/g, '/')
const fails = []
const rows = []

function check(name, actual, minimum, detail = '') {
  const ok = Number.isFinite(actual) && actual + 1e-9 >= minimum
  rows.push({ name, actual: Number.isFinite(actual) ? Number(actual.toFixed(2)) : null, minimum, ok, detail })
  if (!ok) fails.push(name + ': ' + actual + ' < ' + minimum + (detail ? ' (' + detail + ')' : ''))
}

const thresholds = readJson('config/coverage-thresholds.json')
const baseline = readJson('config/coverage-baseline.json')

// 1) JavaScript global floor
const js = readJson('coverage/coverage-summary.json')
check('js.global.lines', js.total?.lines?.pct, thresholds.javascript.globalFloor.lines)
check('js.global.branches', js.total?.branches?.pct, thresholds.javascript.globalFloor.branches)
check('js.global.functions', js.total?.functions?.pct, baseline.javascript.functions, 'baseline no-decrease')

// 2) JavaScript changed logical lines, vs baseline
const diff = readJson('coverage/diff-cover.json')
let changed = 0,
  missing = 0
for (const stat of Object.values(diff.src_stats || {})) {
  changed += (stat.covered_lines?.length || 0) + (stat.violation_lines?.length || 0)
  missing += stat.violation_lines?.length || 0
}
if (!changed) fails.push('diff-cover: no changed lines found')
const changedPct = changed ? ((changed - missing) / changed) * 100 : 0
check(
  'js.changedLines',
  changedPct,
  thresholds.javascript.changedLinesFloor.lines,
  'target ' +
    thresholds.javascript.changedLinesFloor.targetLines +
    ', ratchet +' +
    thresholds.javascript.changedLinesFloor.ratchetPerPr +
    '/PR'
)

// 3) Critical contract modules
for (const mod of thresholds.javascript.criticalModules) {
  const key = Object.keys(js).find((k) => k !== 'total' && norm(k).endsWith(mod.module))
  if (!key) {
    fails.push('critical module missing from coverage: ' + mod.module)
    continue
  }
  check('critical ' + mod.module + '.lines', js[key].lines?.pct, mod.lines)
  check('critical ' + mod.module + '.branches', js[key].branches?.pct, mod.branches)
}

// 4) Python pure-logic scope (UI/bpy/registration/network transport omitted, see config)
const py = readJson('coverage/python-coverage.json')
let pyLines = 0,
  pyCovered = 0,
  pyBranches = 0,
  pyBranchCovered = 0
for (const mod of thresholds.python.scope) {
  const key = Object.keys(py.files).find((k) => norm(k).endsWith(mod))
  if (!key) {
    fails.push('python scope module missing from coverage: ' + mod)
    continue
  }
  const s = py.files[key].summary
  pyLines += s.num_statements || 0
  pyCovered += s.covered_lines || 0
  pyBranches += s.num_branches || 0
  pyBranchCovered += s.covered_branches || 0
}
const pyLinePct = pyLines ? (pyCovered / pyLines) * 100 : 0
const pyBranchPct = pyBranches ? (pyBranchCovered / pyBranches) * 100 : 0
check('python.pureLogic.lines', pyLinePct, thresholds.python.floor.lines, pyCovered + '/' + pyLines)
check('python.pureLogic.branches', pyBranchPct, thresholds.python.floor.branches, pyBranchCovered + '/' + pyBranches)

// 5) E2E behavioral matrix, reported separately
if (thresholds.e2e.behaviorMatrixRelease) {
  const run = spawnSync(process.execPath, [path.join(root, 'scripts/check-behavior-matrix.mjs'), '--release'], {
    cwd: root,
    encoding: 'utf8',
    env: { ...process.env, NODE_OPTIONS: '' }
  })
  const out = (run.stdout || '').trim()
  const m = out.match(/(\d+) rows \((\d+) covered, (\d+) planned, (\d+) exempt\)/)
  if (run.status !== 0 || !m) {
    fails.push('e2e behavior matrix release check failed: ' + (out || run.stderr || 'no output'))
  } else {
    const [, total, covered, planned, exempt] = m.map(Number)
    check(
      'e2e.scenarioCoverage',
      (covered / (total - exempt)) * 100,
      thresholds.e2e.scenarioCoverage,
      covered + '/' + (total - exempt) + ' covered, ' + planned + ' planned, ' + exempt + ' exempt'
    )
    if (thresholds.e2e.plannedMustBeZero && planned !== 0) fails.push('e2e.planned: ' + planned + ' > 0')
  }
}

const result = {
  at: new Date().toISOString(),
  commit: (() => {
    try {
      return fs.readFileSync(path.join(root, '.git/HEAD'), 'utf8').trim()
    } catch {
      return null
    }
  })(),
  thresholdsVersion: thresholds.version,
  rows,
  fails,
  pass: fails.length === 0
}
try {
  fs.mkdirSync(path.join(root, 'coverage'), { recursive: true })
  fs.writeFileSync(path.join(root, 'coverage/check-coverage-result.json'), JSON.stringify(result, null, 2) + '\n')
} catch {}

for (const r of rows) {
  console.log(
    (r.ok ? 'PASS' : 'FAIL') +
      '  ' +
      r.name.padEnd(38) +
      ' ' +
      String(r.actual).padStart(7) +
      '  >= ' +
      String(r.minimum).padStart(6) +
      (r.detail ? '  ' + r.detail : '')
  )
}
if (fails.length) {
  console.error('\nCOVERAGE FAIL')
  for (const f of fails) console.error(' - ' + f)
  process.exit(1)
}
console.log(
  '\nCOVERAGE PASS (' + changedPct.toFixed(1) + '% changed lines, ' + ((pyCovered / pyLines) * 100).toFixed(1) + '% python pure logic)'
)
