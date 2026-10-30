import { afterAll, beforeAll, describe, expect, it } from 'vitest'
import * as plugin from '../../../../lib/index.js'
import { createFakeContext, type CapturedTool } from '../../../helpers/fake-context'
import { closedPort, startFakeBackend } from '../../../helpers/fake-backend.mjs'

let backend: Awaited<ReturnType<typeof startFakeBackend>>
let fake: ReturnType<typeof createFakeContext>
let tools: CapturedTool[]

function tool(name: string): CapturedTool {
  const t = tools.find((x) => x.name === name)
  if (!t) throw new Error('missing tool ' + name)
  return t
}

async function run(name: string, args: unknown, exec: Record<string, unknown> = {}): Promise<any> {
  return await (tool(name).execute as any)(args, exec)
}

beforeAll(async () => {
  backend = await startFakeBackend()
  fake = createFakeContext()
  const attachments = {
    imageLimits: { mediaTypes: ['image/png'] },
    async saveImage({ data, name }: { data: Uint8Array; name: string }) {
      return { attachmentId: 'att-' + name, mediaType: 'image/png', bytes: data.length, width: 1, height: 1, name }
    },
  }
  fake.ctx.get = ((name: string) => (name === 'attachments' ? attachments : undefined)) as any
  plugin.apply(fake.ctx, { autoStart: false, tui: false, port: backend.port })
  tools = fake.tools
})

afterAll(async () => {
  fake.dispose()
  await backend.close()
})

describe('model tool execution semantics', () => {
  it('tool blender_rt_see executes a viewport frame round-trip', async () => {
    const out = await run('blender_rt_see', { max_size: 320 })
    expect(out.text).toContain('视口帧 320px')
    expect(out.image?.attachmentId).toContain('viewport-')
    expect(backend.requests.some((r: any) => r.method === 'GET' && r.path === '/frame.png' && r.search.includes('size=320'))).toBe(true)
  })

  it('tool blender_rt_do executes code and returns the follow-up frame', async () => {
    const out = await run('blender_rt_do', { code: 'print(1)' })
    expect(out.text).toContain('ACT ok')
    expect(out.text).toContain('fake stdout')
    expect(out.image).toBeTruthy()
    const act = backend.requests.find((r: any) => r.method === 'POST' && r.path === '/act')
    expect(act?.body.code).toBe('print(1)')
  })

  it('tool blender_rt_watch samples a time window', async () => {
    const out = await run('blender_rt_watch', { code: 'x = 1', seconds: 0.2, fps: 1, max_size: 120 })
    expect(out.text).toContain('WATCH 0.2s @1fps')
    expect(out.text).toContain('timeline:')
  })

  it('tool blender_rt_cmd sends one addon command', async () => {
    const out = await run('blender_rt_cmd', { name: 'get_scene_info', params: { a: 1 } })
    expect(out.text).toContain('CMD get_scene_info ok')
    const req = backend.requests.find((r: any) => r.method === 'POST' && r.path === '/cmd')
    expect(req?.body).toMatchObject({ name: 'get_scene_info', params: { a: 1 } })
  })

  it('tool blender_rt_commands renders the command catalog', async () => {
    const out = await run('blender_rt_commands', {})
    expect(out.text).toContain('可用命令 39 条')
    expect(out.text).toContain('polyhaven=off')
  })

  it('tool blender_rt_loop runs an inner-loop op', async () => {
    const out = await run('blender_rt_loop', { op: 'status' })
    expect(out.text).toContain('LOOP status')
    expect(backend.requests.some((r: any) => r.path === '/loop' && r.body.op === 'status')).toBe(true)
  })

  it('tool blender_rt_perf runs a perf op', async () => {
    const out = await run('blender_rt_perf', { op: 'status' })
    expect(out.text).toContain('PERF status')
  })

  it('tool blender_rt_opt maps join to the canonical opt_join op', async () => {
    const out = await run('blender_rt_opt', { op: 'join', args: '{"dry_run":true}' })
    expect(out.text).toContain('OPT join')
    const req = backend.requests.find((r: any) => r.path === '/opt')
    expect(req?.body.op).toBe('opt_join')
  })

  it('tool blender_rt_headless returns a headless receipt', async () => {
    const out = await run('blender_rt_headless', { script: 'print(1)' })
    expect(out.text).toContain('HEADLESS')
    expect(out.envelope?.status).toBe('done')
    const req = backend.requests.find((r: any) => r.path === '/headless')
    expect(req?.body.script).toBe('print(1)')
  })

  it('tool blender_rt_plan returns a plan receipt', async () => {
    const out = await run('blender_rt_plan', { op: 'catalog' })
    expect(out.text).toContain('PLAN catalog ok')
    expect(out.envelope?.verdict).toBe('pass')
  })

  it('tool blender_rt_worker starts a worker session', async () => {
    const out = await run('blender_rt_worker', { op: 'start', name: 'w1' })
    expect(out.text).toContain('WORKER start ok')
    expect(out.text).toContain('w1')
  })

  it('tool blender_rt_txn lists snapshots and marks', async () => {
    const out = await run('blender_rt_txn', { op: 'list' })
    expect(out.text).toContain('TXN list ok')
  })

  it('tool blender_rt_preset lists the recipe library', async () => {
    const out = await run('blender_rt_preset', { op: 'list' })
    expect(out.text).toContain('PRESET list ok')
  })

  it('tool blender_rt_job lists jobs', async () => {
    const out = await run('blender_rt_job', { op: 'list' })
    expect(out.text).toContain('作业（1）')
    expect(out.text).toContain('job-1')
  })

  it('tool blender_viewport status reports backend state', async () => {
    const out = await run('blender_viewport', { op: 'status' })
    expect(typeof out).toBe('string')
    expect(JSON.parse(out).running).toBe(true)
    expect(JSON.parse(out).endpoint).toContain(String(backend.port))
  })

  it('rejects missing required parameters before touching the backend', async () => {
    expect((await run('blender_rt_do', {})).text).toContain('需要 code 或 file 之一')
    await expect(run('blender_rt_cmd', {})).rejects.toThrow('missing required property "name"')
    expect((await run('blender_rt_headless', { blend: 'D:/x.blend' })).text).toContain('没有参数 blend')
    expect((await run('blender_rt_headless', { file: '   ' })).text).toContain('空字符串')
    expect((await run('blender_rt_job', { op: 'start', file: '' })).text).toContain('空字符串')
    expect((await run('blender_rt_plan', { op: 'catalog', args: '{bad' })).text).toContain('args 不是合法 JSON 对象')
    expect((await run('blender_rt_preset', { op: 'save', data: '{bad' })).text).toContain('data 不是合法 JSON')
  })

  it('maps a backend lease conflict to a readable 409 message', async () => {
    backend.setHandler('POST /act', () => ({
      status: 409,
      headers: { 'content-type': 'application/json' },
      body: Buffer.from(JSON.stringify({ ok: false, error: 'leased', holder: 'other', expiresInMs: 5000, hint: 'wait' })),
    }))
    const out = await run('blender_rt_do', { code: 'x = 1', see: false })
    expect(out.text).toContain('写通道被别的会话占用')
    expect(out.text).toContain('other')
    backend.setHandler('POST /act')
  })

  it('aborts waiting when the host exec signal fires', async () => {
    backend.hangOn('POST /act')
    const ac = new AbortController()
    const pending = run('blender_rt_do', { code: 'x = 1', see: false }, { signal: ac.signal })
    setTimeout(() => ac.abort(), 50)
    await expect(pending).rejects.toThrow('宿主取消了本次等待')
    backend.unhang('POST /act')
  })

  it('passes session routing through to the backend act endpoint', async () => {
    const before = backend.requests.length
    const out = await run('blender_rt_do', { code: 'x = 1', session: 'w1', see: false })
    expect(out.text).toContain('ACT(session=w1) ok')
    const req = backend.requests.slice(before).find((r: any) => r.path === '/worker')
    expect(req?.body).toMatchObject({ op: 'exec', name: 'w1', code: 'x = 1' })
  })
})

describe('model tool failure semantics', () => {
  it('reports an unreachable backend without hanging', async () => {
    const port = await closedPort()
    const f = createFakeContext()
    plugin.apply(f.ctx, { autoStart: false, tui: false, port })
    const out = await (f.tools.find((t) => t.name === 'blender_viewport')!.execute as any)({ op: 'status' }, {})
    const parsed = JSON.parse(out)
    expect(parsed.running).toBe(false)
    expect(parsed.error).toBeTruthy()
    expect(parsed.hint).toContain('op=start')
    f.dispose()
  })
})
