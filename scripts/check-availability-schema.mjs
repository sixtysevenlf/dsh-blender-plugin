#!/usr/bin/env node
// @ts-check
/**
 * scripts/check-availability-schema.mjs — verify the generated availability
 * constants match config/availability.json.
 *
 * config/availability.json is the single source for stages, envelope fields,
 * fallback kinds and diagnostic budgets. scripts/gen-availability.mjs renders
 * the TypeScript and Python constants; this gate fails when a generated file is
 * missing, stale, or hand-edited.
 */
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { DEFAULT_ROOT, generateAvailabilitySchema, SCHEMA_FILE } from './gen-availability.mjs'

export function checkAvailabilitySchema(root = DEFAULT_ROOT) {
  const failures = []
  let generated = null
  try {
    generated = generateAvailabilitySchema(root)
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
        failures.push(file.file + ' is missing: run node scripts/gen-availability.mjs')
        continue
      }
      if (actual !== file.content)
        failures.push(file.file + ' is stale: run node scripts/gen-availability.mjs')
    }
  }
  return {
    ok: failures.length === 0,
    tool: 'check-availability-schema',
    root,
    source: SCHEMA_FILE,
    hash: generated ? generated.hash : null,
    stages: generated ? generated.config.stages : null,
    failures,
  }
}

const MAIN = process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)
if (MAIN) {
  const payload = checkAvailabilitySchema()
  if (payload.ok) {
    console.log(
      'check-availability-schema: OK (' +
        String(payload.stages ? payload.stages.length : 0) +
        ' stages · sha256 ' +
        String(payload.hash).slice(0, 12) +
        ')',
    )
  } else {
    console.error('check-availability-schema: FAIL')
    for (const failure of payload.failures) console.error('- ' + failure)
  }
  process.exit(payload.ok ? 0 : 1)
}
