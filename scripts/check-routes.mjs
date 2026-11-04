#!/usr/bin/env node
// @ts-check
/**
 * scripts/check-routes.mjs — verify the generated route tables are fresh (D3).
 *
 * config/routing.json is the single routing source; scripts/gen-routes.mjs renders
 * the two generated tables. This gate fails when a generated file is missing,
 * stale after a routing edit, or was hand-edited (the header hash no longer matches).
 */
import fs from 'node:fs'
import path from 'node:path'
import { DEFAULT_ROOT, generateRoutes } from './gen-routes.mjs'

const args = process.argv.slice(2)
let root = DEFAULT_ROOT
let json = false
for (let index = 0; index < args.length; index += 1) {
  const arg = args[index]
  if (arg === '--json') {
    json = true
  } else if (arg === '--root') {
    const value = args[index + 1]
    if (!value) {
      console.error('check-routes: --root needs a directory')
      process.exit(2)
    }
    root = path.resolve(value)
    index += 1
  } else {
    console.error('check-routes: unknown argument ' + arg)
    process.exit(2)
  }
}

const failures = []
let generated = null
try {
  generated = await generateRoutes(root)
} catch (error) {
  failures.push(String(error && error.message ? error.message : error))
}
if (generated) {
  for (const file of generated.files) {
    const target = path.join(root, file.file)
    let actual
    try {
      actual = fs.readFileSync(target, 'utf8')
    } catch {
      failures.push(file.file + ' is missing: run node scripts/gen-routes.mjs')
      continue
    }
    if (actual !== file.content) {
      failures.push(file.file + ' is stale: run node scripts/gen-routes.mjs')
    }
  }
}

const payload = {
  ok: failures.length === 0,
  tool: 'check-routes',
  root,
  source: 'config/routing.json',
  hash: generated ? generated.hash : null,
  routes: generated ? generated.routes : 0,
  files: generated ? generated.files.map((file) => file.file) : [],
  failures,
}
if (json) {
  console.log(JSON.stringify(payload, null, 2))
} else if (payload.ok) {
  console.log(
    'check-routes: OK (' +
      payload.routes +
      ' routes · sha256 ' +
      String(payload.hash).slice(0, 12) +
      ')',
  )
} else {
  console.error('check-routes: FAIL')
  for (const failure of failures) console.error('- ' + failure)
}
process.exit(payload.ok ? 0 : 1)
