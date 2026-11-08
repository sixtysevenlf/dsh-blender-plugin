#!/usr/bin/env node
// @ts-check
/**
 * scripts/check-gateway-schema.mjs — verify the generated Gateway constants are fresh.
 *
 * config/gateway.json is the single handshake source; scripts/gen-gateway-schema.mjs
 * renders the TypeScript and Python constants. This gate fails when a generated
 * file is missing, stale after a schema edit, or hand-edited.
 */
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { DEFAULT_ROOT, generateGatewaySchema, SCHEMA_FILE } from './gen-gateway-schema.mjs'

export function checkGatewaySchema(root = DEFAULT_ROOT) {
  const failures = []
  let generated = null
  try {
    generated = generateGatewaySchema(root)
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
        failures.push(file.file + ' is missing: run node scripts/gen-gateway-schema.mjs')
        continue
      }
      if (actual !== file.content) {
        failures.push(file.file + ' is stale: run node scripts/gen-gateway-schema.mjs')
      }
    }
  }
  return {
    ok: failures.length === 0,
    tool: 'check-gateway-schema',
    root,
    source: SCHEMA_FILE,
    hash: generated ? generated.hash : null,
    protocol: generated ? generated.config.addon.protocol : null,
    files: generated ? generated.files.map((file) => file.file) : [],
    failures,
  }
}

const MAIN = process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)
if (MAIN) {
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
        console.error('check-gateway-schema: --root needs a directory')
        process.exit(2)
      }
      root = path.resolve(value)
      index += 1
    } else {
      console.error('check-gateway-schema: unknown argument ' + arg)
      process.exit(2)
    }
  }
  const payload = checkGatewaySchema(root)
  if (json) {
    console.log(JSON.stringify(payload, null, 2))
  } else if (payload.ok) {
    console.log(
      'check-gateway-schema: OK (' +
        'protocol ' +
        payload.protocol +
        ' · sha256 ' +
        String(payload.hash).slice(0, 12) +
        ')',
    )
  } else {
    console.error('check-gateway-schema: FAIL')
    for (const failure of payload.failures) console.error('- ' + failure)
  }
  process.exit(payload.ok ? 0 : 1)
}
