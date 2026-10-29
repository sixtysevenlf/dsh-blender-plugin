import { describe, expect, it } from 'vitest'
import fs from 'node:fs'
import vm from 'node:vm'
import { pkgPath } from '../../../helpers/pkg'

const source = fs.readFileSync(pkgPath('lib', 'client.js'), 'utf8')
const EXTERNAL_ID = '@dsh-external/dsh-blender-plugin'

function loadBundle() {
  let registration: any = null
  const sandbox = {
    window: { __ModuleLoader__: { load: (def: any) => { registration = def } } },
    console,
    fetch: async () => ({ ok: false, status: 0, json: async () => ({}) }),
    localStorage: { getItem: () => null, setItem: () => {} },
  }
  vm.runInNewContext(source, sandbox, { filename: 'lib/client.js' })
  return registration
}

describe('client bundle contract', () => {
  it('registers a factory under the package id', () => {
    const registration = loadBundle()
    expect(registration.id).toBe(EXTERNAL_ID)
    expect(registration.factory).toBeTypeOf('function')
  })

  it('materializes the documented apply / inject surface', () => {
    const registration = loadBundle()
    const react = { useEffect: () => {}, useRef: (v: unknown) => ({ current: v }) }
    const exported = registration.factory((id: string) => {
      if (id === 'react') return react
      throw new Error('unexpected require: ' + id)
    })
    expect(exported.apply).toBeTypeOf('function')
    expect(exported.inject).toContain('slots')
    expect(exported.__selftest.DEFAULT_BACKEND).toBe('http://127.0.0.1:9877')
    expect(exported.__selftest.splitRef('material:Oak')).toEqual({ kind: 'material', name: 'Oak' })
    expect(exported.__selftest.splitRef('Cube')).toEqual({ kind: 'object', name: 'Cube' })
    expect(exported.__selftest.onPickItem({ candidate: { value: 'object:Cube', name: 'Cube' } }))
      .toMatchObject({ insert: { ref: 'object:Cube', clipboardText: '@Cube' } })
  })

  it('has no ESM syntax or Node builtin imports', () => {
    expect(source).not.toMatch(/^\s*(import|export)\s/m)
    expect(source).not.toMatch(/require\(['"](?:node:)?(?:fs|path|net|http|https|child_process)['"]\)/)
  })
})
