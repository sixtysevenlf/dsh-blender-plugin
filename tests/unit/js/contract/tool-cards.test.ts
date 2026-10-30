import { afterAll, beforeAll, describe, expect, it } from 'vitest'
import * as plugin from '../../../../lib/index.js'
import { createFakeContext, type CapturedTool } from '../../../helpers/fake-context'

// v2.6.1: presentCall/presentResult decide how the host draws cards. Without
// them the header is raw JSON args and the body is the raw receipt. This suite
// pins titles, bodies, envelope stripping, image passthrough and losslessness.

const NL = String.fromCharCode(10)
let fake: ReturnType<typeof createFakeContext>
let tools: Map<string, CapturedTool>

beforeAll(() => {
  fake = createFakeContext()
  plugin.apply(fake.ctx, { autoStart: false, tui: false, port: 9898 })
  tools = new Map(fake.tools.map((tool) => [tool.name, tool]))
})

afterAll(() => fake.dispose())

const call = (name: string, args: unknown) => (tools.get(name)!.presentCall as any)(args)
const result = (name: string, args: unknown, content: unknown[], isError = false) =>
  (tools.get(name)!.presentResult as any)(args, { content, isError })

describe('tool cards', () => {
  it('attaches presenters to exactly 15 tools aligned with the specs', () => {
    expect(tools.size).toBe(15)
    for (const tool of tools.values()) {
      expect(typeof tool.presentCall, tool.name).toBe('function')
      expect(typeof tool.presentResult, tool.name).toBe('function')
    }
    expect(Object.keys(plugin.__internals.cards.specs).sort()).toEqual([...tools.keys()].sort())
  })

  it('renders call titles per tool', () => {
    expect(call('blender_rt_see', {})).toMatchObject({ card: 'generic', title: 'Blender 看视口 · 560px', kind: 'read' })
    expect(call('blender_rt_see', { from: '7,-7,5', look_at: '0,0,1', max_size: 900, shading: 'SOLID' }).title)
      .toBe('Blender 看视口 · 自定义视角 7,-7,5 → 0,0,1 · SOLID · 900px')
    expect(call('blender_rt_see', { full: true, area: 2 }).title).toBe('Blender 截窗口 · 区域 2')
    expect(call('blender_rt_cmd', { name: 'get_selection', params: { a: 1, b: 2, c: 3, d: 4 } }).title)
      .toBe('Blender 命令 · get_selection · {a, b, c, …}')
    expect(call('blender_rt_commands', {}).title).toBe('Blender 命令清单')
    expect(call('blender_rt_plan', { op: 'catalog' }).title).toBe('Blender 规划 · catalog')
    expect(call('blender_rt_headless', { script_file: '/tmp/a/bake.py', file: '/tmp/m.blend', as_job: true }).title)
      .toBe('Blender 无头 · bake.py · m.blend · 作业')
    expect(call('blender_rt_job', { op: 'status', id: 'job-42' }).title).toBe('Blender 作业 · status · job-42')
    expect(call('blender_viewport', { op: 'doctor' }).title).toBe('Blender 通道 · doctor')
    expect(call('blender_viewport', {})).toBeUndefined()
    expect(call('blender_rt_see', {}).content).toBeUndefined()
  })

  it('puts code into the do call card and truncates long titles/code', () => {
    const code = '# 换材质' + NL + 'import bpy' + NL + 'o = bpy.data.objects["Cube"]' + NL + NL + 'o.location.z += 1'
    const doCall = call('blender_rt_do', { code })
    expect(doCall.title).toBe('Blender 执行 Python · import bpy（+3 行）')
    expect(doCall.kind).toBe('execute')
    expect(doCall.content).toHaveLength(1)
    expect(doCall.content[0].text.startsWith('▸ Python（5 行）' + NL)).toBe(true)
    expect(doCall.content[0].text.endsWith('o.location.z += 1')).toBe(true)
    expect(call('blender_rt_do', { code: 'x = 1', session: 'w1' }).title).toBe('Blender 执行 Python · x = 1 · 无头 w1')
    expect(call('blender_rt_do', { file: 'C:\\work\\scripts\\fix.py' }).title).toBe('Blender 执行 Python · fix.py')
    const longTitle = call('blender_rt_do', {
      code: 'bpy.ops.mesh.primitive_cube_add(size=2, location=(0, 0, 0), rotation=(0, 0, 0), scale=(1, 1, 1))' + NL + 'x = 1',
    }).title
    expect(longTitle.length).toBeLessThanOrEqual(80)
    expect(longTitle).toContain('…')
    const hugeCode = Array.from({ length: 3000 }, (_, i) => 'x' + String(i) + ' = ' + String(i)).join(NL)
    const huge = call('blender_rt_do', { code: hugeCode }).content[0].text
    expect(huge.length).toBeLessThan(4300)
    expect(huge).toContain('中间省略')
    expect(huge.endsWith('x2999 = 2999')).toBe(true)
  })

  it('strips the receipt envelope and keeps images last', () => {
    const envelope = JSON.stringify({ ok: true, op: 'asm_scan', ms: 120 })
    const image = { type: 'image', attachment: { attachmentId: 'att-1', mediaType: 'image/png', bytes: 10, width: 4, height: 4 } }
    const plan = result('blender_rt_plan', { op: 'asm_scan' }, [{ type: 'text', text: envelope }, { type: 'text', text: 'PLAN asm_scan ok · 12 件' + NL + '明细…' }, image])
    expect(plan.card).toBe('generic')
    expect(plan.content[0].text).not.toContain('"ok":true')
    expect(plan.content[0].text.startsWith('PLAN asm_scan ok · 12 件')).toBe(true)
    expect(JSON.stringify(plan.content[1])).toBe(JSON.stringify(image))
    expect(plan.title).toBeUndefined()
    const doRes = result('blender_rt_do', { code: 'x = 1' }, [{ type: 'text', text: 'DO ok · 105ms' + NL + 'stdout: 1' }, image])
    expect(doRes.content[0].text.startsWith('DO ok · 105ms' + NL + 'stdout: 1' + NL + NL + '▸ Python')).toBe(true)
    expect(doRes.content.filter((b: any) => b.type === 'text')).toHaveLength(1)
    expect(doRes.content[doRes.content.length - 1]).toBe(image)
    expect(result('blender_rt_do', {}, [{ type: 'text', text: 'boom' }], true)).toBeUndefined()
    expect(result('blender_rt_see', {}, [{ type: 'text', text: 'SEE ok' }, image])).toBeUndefined()
    expect(result('blender_rt_plan', { op: 'x' }, [{ type: 'text', text: '{' + NL + '"a": 1' + NL + '}' }, { type: 'text', text: 'b' }])).toBeUndefined()
    expect(result('blender_rt_plan', { op: 'x' }, [{ type: 'text', text: envelope }])).toBeUndefined()
  })

  it('keeps the model-facing render channel intact', () => {
    const rendered = (tools.get('blender_rt_plan')!.output as any).render({ op: 'x' }, { text: 'hi', envelope: { ok: true } })
    expect(rendered[0].text).toBe('{"ok":true}')
    expect(rendered[1].text).toBe('hi')
  })

  it('never throws on odd arguments and stays lossless', () => {
    expect(JSON.stringify(call('blender_rt_do', { code: 'x = 1' }))).toBe(JSON.stringify(call('blender_rt_do', { code: 'x = 1' })))
    for (const tool of tools.values()) {
      expect(() => (tool.presentCall as any)({})).not.toThrow()
      expect(() => (tool.presentCall as any)(null)).not.toThrow()
      expect(() => (tool.presentResult as any)(null, null)).not.toThrow()
      const view = (tool.presentCall as any)({})
      if (view !== undefined) expect(JSON.parse(JSON.stringify(view))).toEqual(view)
    }
  })
})
