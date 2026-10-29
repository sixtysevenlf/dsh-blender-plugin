import { describe, expect, it } from 'vitest'
import fs from 'node:fs'
// @ts-expect-error - runtime is plain .mjs, no declaration file
import { PLAN_CATALOG } from '../../../../runtime/catalog.mjs'
import { pkgPath } from '../../../helpers/pkg'

const serverSource = fs.readFileSync(pkgPath('runtime', 'server.mjs'), 'utf8')
const engineSource = fs.readFileSync(pkgPath('runtime', 'engine.mjs'), 'utf8')

function literalRoutes(source: string) {
  return new Set([...source.matchAll(/p === '([^']+)'/g)].map((m) => m[1]))
}

describe('backend route contract', () => {
  it('keeps the stable HTTP route set', () => {
    const routes = literalRoutes(serverSource)
    for (const required of [
      '/health', '/status', '/doctor', '/commands', '/plan', '/headless', '/job',
      '/txn', '/preset', '/worker', '/loop', '/perf', '/opt', '/cmd', '/act',
      '/host/scene-items', '/host/selection/latest', '/host/selection/consume',
      '/host/state', '/host/jobs', '/host/jobs/log',
    ]) expect(routes.has(required), required).toBe(true)
    expect(routes.has('/frame')).toBe(true)
    expect(serverSource).toContain('410')
  })

  it('routes every catalog family prefix through engine dispatch', () => {
    for (const family of PLAN_CATALOG) {
      if (!family.prefix) continue
      expect(engineSource.includes(family.prefix), family.f + ' -> ' + family.prefix).toBe(true)
    }
  })

  it('keeps every family op name unique within its family', () => {
    for (const family of PLAN_CATALOG) {
      expect(new Set(family.ops).size, family.f).toBe(family.ops.length)
    }
    expect(PLAN_CATALOG).toHaveLength(34)
  })
})
