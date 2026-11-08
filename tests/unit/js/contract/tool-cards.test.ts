import { afterAll, beforeAll, describe, expect, it } from 'vitest'
import * as plugin from '../../../../src/index'
import { createFakeContext, type CapturedTool } from '../../../helpers/fake-context'

// 3.1：卡片只服务 6 个模型工具（session/op/py/see/run/inspect）。标题、代码正文、
// 信封剥离、图片顺序与 lossless 都是宿主可见契约，逐项钉住。

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
  it('attaches presenters to exactly the 6 model tools', () => {
    expect(tools.size).toBe(6)
    for (const tool of tools.values()) {
      expect(typeof tool.presentCall, tool.name).toBe('function')
      expect(typeof tool.presentResult, tool.name).toBe('function')
    }
    expect(Object.keys(plugin.__internals.cards.specs).sort()).toEqual([...tools.keys()].sort())
  })

  it('renders call titles per tool', () => {
    expect(call('blender_session', { action: 'list' })).toMatchObject({
      card: 'generic',
      title: 'Blender 会话 · list',
      kind: 'other',
    })
    expect(call('blender_session', { action: 'attach', instance_id: 'inst-1' }).title).toBe(
      'Blender 会话 · attach · instance inst-1',
    )
    expect(call('blender_op', { action: 'plan', op: 'catalog' }).title).toBe(
      'Blender 算子 · plan · catalog',
    )
    expect(call('blender_op', {}).title).toBe('Blender 算子 · plan')
    expect(call('blender_py', { code: 'x = 1' }).title).toBe('Blender 执行 Python · x = 1')
    expect(call('blender_see', {}).title).toBe('Blender 看视口 · viewport · frame')
    expect(call('blender_see', { viewpoint: 'camera', mode: 'frame' }).title).toBe(
      'Blender 看视口 · camera · frame',
    )
    expect(call('blender_see', { from: '4,-4,3', look_at: '0,0,1' }).title).toBe(
      'Blender 看视口 · custom · frame · 4,-4,3 → 0,0,1',
    )
    expect(call('blender_run', { action: 'submit', kind: 'render' }).title).toBe(
      'Blender 作业 · submit · render',
    )
    expect(call('blender_run', {}).title).toBe('Blender 作业 · list')
    expect(call('blender_inspect', {}).title).toBe('Blender 只读检查 · 命令目录')
    expect(call('blender_inspect', { command: 'get_scene_info' }).title).toBe(
      'Blender 只读检查 · get_scene_info',
    )
    expect(call('blender_see', {}).content).toBeUndefined()
  })

  it('puts code into the py call card and truncates long titles/code', () => {
    const code =
      '# 换材质' +
      NL +
      'import bpy' +
      NL +
      'o = bpy.data.objects["Cube"]' +
      NL +
      NL +
      'o.location.z += 1'
    const pyCall = call('blender_py', { code })
    expect(pyCall.title).toBe('Blender 执行 Python · import bpy（+3 行）')
    expect(pyCall.kind).toBe('execute')
    expect(pyCall.content).toHaveLength(1)
    expect(pyCall.content[0].text.startsWith('▸ Python（5 行）' + NL)).toBe(true)
    expect(pyCall.content[0].text.endsWith('o.location.z += 1')).toBe(true)
    expect(call('blender_py', { file: 'C:\\work\\scripts\\fix.py' }).title).toBe(
      'Blender 执行 Python · fix.py',
    )
    const huge = call('blender_py', {
      code: Array.from({ length: 3000 }, (_, i) => 'x' + String(i) + ' = ' + String(i)).join(NL),
    }).content[0].text
    expect(huge.length).toBeLessThan(4300)
    expect(huge).toContain('中间省略')
    expect(huge.endsWith('x2999 = 2999')).toBe(true)
  })

  it('strips the receipt envelope and keeps images last', () => {
    const envelope = JSON.stringify({ ok: true, op: 'asm_scan', ms: 120 })
    const image = {
      type: 'image',
      attachment: { attachmentId: 'att-1', mediaType: 'image/png', bytes: 10, width: 4, height: 4 },
    }
    const op = result('blender_op', { action: 'plan', op: 'asm_scan' }, [
      { type: 'text', text: envelope },
      { type: 'text', text: 'OP asm_scan ok · 12 件' + NL + '明细…' },
      image,
    ])
    expect(op.card).toBe('generic')
    expect(op.content[0].text).not.toContain('"ok":true')
    expect(op.content[0].text.startsWith('OP asm_scan ok · 12 件')).toBe(true)
    expect(JSON.stringify(op.content[op.content.length - 1])).toBe(JSON.stringify(image))
    const pyRes = result('blender_py', { code: 'x = 1' }, [
      { type: 'text', text: 'PY ok · 105ms' + NL + 'stdout: 1' },
      image,
    ])
    expect(
      pyRes.content[0].text.startsWith('PY ok · 105ms' + NL + 'stdout: 1' + NL + NL + '▸ Python'),
    ).toBe(true)
    expect(pyRes.content.filter((b: any) => b.type === 'text')).toHaveLength(1)
    expect(pyRes.content[pyRes.content.length - 1]).toBe(image)
    expect(result('blender_py', {}, [{ type: 'text', text: 'boom' }], true)).toBeUndefined()
    expect(result('blender_see', {}, [{ type: 'text', text: 'SEE ok' }, image])).toBeUndefined()
    expect(
      result('blender_op', { action: 'plan', op: 'x' }, [
        { type: 'text', text: '{' + NL + '"a": 1' + NL + '}' },
        { type: 'text', text: 'b' },
      ]),
    ).toBeUndefined()
  })

  it('never throws on odd arguments and stays lossless', () => {
    for (const tool of tools.values()) {
      expect(() => (tool.presentCall as any)({})).not.toThrow()
      expect(() => (tool.presentCall as any)(null)).not.toThrow()
      expect(() => (tool.presentResult as any)(null, null)).not.toThrow()
      const view = (tool.presentCall as any)({})
      if (view !== undefined) expect(JSON.parse(JSON.stringify(view))).toEqual(view)
    }
  })
})
