import { describe, it, expect, beforeEach, afterEach } from 'vitest'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { generateGatewaySchema } from '../../../../scripts/gen-gateway-schema.mjs'
import { checkGatewaySchema } from '../../../../scripts/check-gateway-schema.mjs'

const ROOT = path.resolve(__dirname, '../../../..')
const SOURCE = path.join(ROOT, 'config', 'gateway.json')

function seed(): string {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'gateway-schema-'))
  fs.mkdirSync(path.join(dir, 'config'), { recursive: true })
  fs.copyFileSync(SOURCE, path.join(dir, 'config', 'gateway.json'))
  const generated = generateGatewaySchema(dir)
  for (const file of generated.files) {
    const target = path.join(dir, file.file)
    fs.mkdirSync(path.dirname(target), { recursive: true })
    fs.writeFileSync(target, file.content, 'utf8')
  }
  return dir
}

describe('gateway handshake schema', () => {
  let root = ''
  beforeEach(() => {
    root = seed()
  })
  afterEach(() => {
    fs.rmSync(root, { recursive: true, force: true })
  })

  it('accepts generated TypeScript and Python constants for the current source', () => {
    const result = checkGatewaySchema(root)
    expect(result.failures).toEqual([])
    expect(result.ok).toBe(true)
    expect(result.protocol).toBe(12)
    expect(result.files).toEqual([
      'runtime/transport/gateway-schema.generated.ts',
      'addon/dsh_blender_bridge/gateway_schema.py',
    ])
  })

  it('fails when the schema changes without regenerating', () => {
    const file = path.join(root, 'config', 'gateway.json')
    const config = JSON.parse(fs.readFileSync(file, 'utf8'))
    config.addon.version = '2.0.1'
    fs.writeFileSync(file, JSON.stringify(config, null, 2) + '\n', 'utf8')
    const result = checkGatewaySchema(root)
    expect(result.ok).toBe(false)
    expect(result.failures.join('\n')).toContain('is stale')
  })

  it('fails when a generated file is missing or hand-edited', () => {
    fs.rmSync(path.join(root, 'addon/dsh_blender_bridge/gateway_schema.py'))
    expect(checkGatewaySchema(root).failures.join('\n')).toContain('is missing')

    const ts = path.join(root, 'runtime/transport/gateway-schema.generated.ts')
    fs.writeFileSync(ts, fs.readFileSync(ts, 'utf8') + '\n// hand edit\n', 'utf8')
    expect(checkGatewaySchema(root).failures.join('\n')).toContain(
      'runtime/transport/gateway-schema.generated.ts is stale',
    )
  })

  it('rejects a protocol bump that skips the breaking-change contract', () => {
    const file = path.join(root, 'config', 'gateway.json')
    const config = JSON.parse(fs.readFileSync(file, 'utf8'))
    config.addon.protocol = 11
    fs.writeFileSync(file, JSON.stringify(config, null, 2) + '\n', 'utf8')
    const result = checkGatewaySchema(root)
    expect(result.ok).toBe(false)
    expect(result.failures.join('\n')).toContain('addon.protocol must be 12')
  })
})
