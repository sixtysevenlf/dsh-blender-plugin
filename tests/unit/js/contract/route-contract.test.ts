import { describe, expect, it } from 'vitest'
import fs from 'node:fs'
// @ts-expect-error - runtime is plain .mjs, no declaration file
import { PLAN_CATALOG } from '../../../../runtime/catalog.mjs'
// @ts-expect-error - runtime is plain .mjs, no declaration file
import { CONTRACT_ROUTES, createRoutes } from '../../../../runtime/http/routes/index.mjs'
import { pkgPath } from '../../../helpers/pkg'

const serverSource = fs.readFileSync(pkgPath('runtime', 'server.mjs'), 'utf8')
const healthSource = fs.readFileSync(pkgPath('runtime', 'http', 'routes', 'health.mjs'), 'utf8')
const dispatchSource =
  fs.readFileSync(pkgPath('runtime', 'core', 'dispatch.mjs'), 'utf8') +
  fs.readFileSync(pkgPath('runtime', 'core', 'plan-dispatch.mjs'), 'utf8')

const REQUIRED = [
  '/health', '/status', '/doctor', '/commands', '/plan', '/headless', '/job',
  '/txn', '/preset', '/worker', '/loop', '/perf', '/opt', '/cmd', '/act',
  '/host/scene-items', '/host/selection/latest', '/host/selection/consume',
  '/host/state', '/host/jobs', '/host/jobs/log',
]

describe('backend route contract', () => {
  it('keeps the stable HTTP route set (29 paths)', () => {
    expect(CONTRACT_ROUTES).toHaveLength(29)
    expect(new Set(CONTRACT_ROUTES).size).toBe(29)
    for (const required of REQUIRED) expect(CONTRACT_ROUTES, required).toContain(required)
    expect(CONTRACT_ROUTES).toContain('/frame')
    expect(CONTRACT_ROUTES).toContain('/frame.png')
    expect(CONTRACT_ROUTES).toContain('/launch')
    expect(CONTRACT_ROUTES).toContain('/who')
    expect(CONTRACT_ROUTES).toContain('/lease')
    expect(CONTRACT_ROUTES).toContain('/release')
    expect(CONTRACT_ROUTES).toContain('/view')
  })

  it('registers a handler for every contract route', () => {
    const routes = createRoutes({})
    const paths = new Set<string>()
    let hostPrefix = false
    for (const r of routes) {
      expect(typeof r.fn, 'handler').toBe('function')
      for (const p of r.paths || []) paths.add(p)
      if (r.prefix === '/host/') hostPrefix = true
    }
    expect(hostPrefix).toBe(true)
    for (const p of CONTRACT_ROUTES) {
      if (p.startsWith('/host/')) continue // 前缀路由，由同一个 handler 覆盖
      expect(paths.has(p), p).toBe(true)
    }
    // 人肉面板的 410 提示仍在（拆分后位于 health 路由模块）
    expect(healthSource).toContain('410')
    expect(serverSource).toContain('createRoutes')
    expect(serverSource).not.toContain("p === '/health'")
  })

  it('routes every catalog family prefix through engine dispatch', () => {
    for (const family of PLAN_CATALOG) {
      if (!family.prefix) continue
      expect(dispatchSource.includes(family.prefix), family.f + ' -> ' + family.prefix).toBe(true)
    }
  })

  it('keeps every family op name unique within its family', () => {
    for (const family of PLAN_CATALOG) {
      expect(new Set(family.ops).size, family.f).toBe(family.ops.length)
    }
    expect(PLAN_CATALOG).toHaveLength(34)
    expect(PLAN_CATALOG.reduce((n, f) => n + f.ops.length, 0)).toBe(228)
  })
})
