/**
 * 3.1 tool-surface contract: exactly six model tools, legacy names rejected with
 * a migration hint, and no internal concept (worker/viewport/job) exposed as a tool.
 */
import { afterAll, beforeAll, describe, expect, it } from 'vitest'
import * as plugin from '../../../src/index'
import { LEGACY_TOOL_MAP, legacyToolHint } from '../../../src/tools/migration'
import { CARD_SPECS } from '../../../src/ui/cards'
import { createFakeContext, type CapturedTool } from '../../helpers/fake-context'

const SIX = [
  'blender_inspect',
  'blender_op',
  'blender_py',
  'blender_run',
  'blender_see',
  'blender_session',
]
const OLD = [
  'blender_rt_see',
  'blender_rt_do',
  'blender_rt_watch',
  'blender_rt_plan',
  'blender_rt_loop',
  'blender_rt_opt',
  'blender_rt_preset',
  'blender_rt_headless',
  'blender_rt_job',
  'blender_rt_perf',
  'blender_rt_cmd',
  'blender_rt_commands',
  'blender_rt_txn',
  'blender_rt_worker',
  'blender_viewport',
]

let tools: CapturedTool[] = []
let fake: ReturnType<typeof createFakeContext>

beforeAll(() => {
  fake = createFakeContext()
  plugin.apply(fake.ctx, { autoStart: false, tui: false, port: 9897 })
  tools = fake.tools
})

afterAll(() => fake.dispose())

describe('3.1 six-tool surface', () => {
  it('registers exactly the six public names', () => {
    expect(tools.map((t) => t.name).sort()).toEqual(SIX)
  })

  it('does not expose any legacy or internal concept as a tool', () => {
    const names = new Set(tools.map((t) => t.name))
    for (const name of OLD) expect(names.has(name), name).toBe(false)
    for (const name of [
      'worker',
      'viewport',
      'job',
      'headless',
      'plan',
      'do',
      'watch',
      'perf',
      'cmd',
    ])
      expect(names.has(name), name).toBe(false)
  })

  it('keeps card specs exactly in sync with the six tools', () => {
    expect(Object.keys(CARD_SPECS).sort()).toEqual(SIX)
  })

  it('maps every legacy tool name to one of the six tools', () => {
    for (const name of OLD) {
      const target = LEGACY_TOOL_MAP[name]
      expect(target, name).toBeDefined()
      expect(SIX.includes(target), name + ' -> ' + String(target)).toBe(true)
    }
    for (const name of [...OLD, 'session', 'op', 'py', 'see', 'run', 'inspect']) {
      expect(legacyToolHint(name), name).toContain('six tools')
    }
    expect(legacyToolHint('blender_see')).toBeUndefined()
    expect(legacyToolHint('run_code')).toBeUndefined()
  })

  it('registers a pre-execute guard that turns a legacy call into a hint', () => {
    expect(fake.guards).toHaveLength(1)
    const guard = fake.guards[0]
    expect(guard({ name: 'blender_rt_see' })).toContain('blender_see')
    expect(guard({ name: 'blender_rt_headless' })).toContain('blender_run')
    expect(guard({ name: 'blender_rt_txn' })).toContain('blender_session')
    expect(guard({ name: 'blender_viewport' })).toContain('blender_session')
    expect(guard({ name: 'session' })).toContain('blender_session')
    expect(guard({ name: 'inspect' })).toContain('blender_inspect')
    expect(guard({ name: 'blender_session' })).toBeUndefined()
    expect(guard({})).toBeUndefined()
  })

  it('states the six names in the catalog payload and tool descriptions', async () => {
    const catalog = tools.find((t) => t.name === 'blender_op')
    expect(catalog).toBeDefined()
    const text = String(catalog?.description || '')
    expect(text).toContain('catalog')
  })
})
