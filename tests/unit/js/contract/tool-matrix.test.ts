/**
 * A1 · 15 个模型工具 × 7 个方面的可执行矩阵（逐工具、逐方面真实调用）。
 *
 * 与既有 contract 测试的分工：
 *   - tool-execution / tool-failure-matrix / tool-presenter / lossless 各自覆盖一个切面；
 *   - 本文件是**审计用矩阵**：每个 (tool, aspect) 一行，真实执行后写入 JSON，并把
 *     "不适用" 的原因显式记录（没有任何静默跳过）。
 *
 * 输出：设置 A1_MATRIX_OUT=<path> 时写 JSON；否则只断言不落盘。
 * 依据：refactor-plan §8.1 A1（15 个 tool：成功/参数错误/backend 不可达/超时 abort/
 * presenter/lossless/session 与无头降级）。
 */
import fs from 'node:fs'
import { afterAll, beforeAll, describe, expect, it } from 'vitest'
import { snapshotJsonValue } from '@deepseek-ai/dsh-session'
import * as plugin from '../../../../src/index'
import { createFakeContext, type CapturedTool } from '../../../helpers/fake-context'
import { closedPort, startFakeBackend } from '../../../helpers/fake-backend.mjs'

type Backend = Awaited<ReturnType<typeof startFakeBackend>>
type Row = { tool: string; aspect: string; status: 'PASS' | 'N/A'; evidence: string; reason?: string }

const ASPECTS = [
  { id: 'success', title: '成功执行', description: '代表性参数返回可读回执，且请求打到预期的 addon 通道' },
  { id: 'paramError', title: '参数错误', description: 'schema 校验失败必须 reject；工具级非法参数必须返回可读错误而不是静默成功' },
  { id: 'backendUnreachable', title: 'backend 不可达', description: '连接被拒时 5s 内给出可读错误（不挂死）' },
  { id: 'timeoutAbort', title: '超时/abort', description: 'exec.signal abort 后 5s 内结束：reject 带取消提示，或 resolve 带取消/后台化提示' },
  { id: 'presenter', title: 'presenter', description: 'presentCall 必为 lossless 卡片；presentResult 按卡片契约返回卡片或 undefined' },
  { id: 'lossless', title: 'lossless', description: '成功回执可 snapshotJsonValue 且 JSON round-trip 等价，无消毒告警' },
  { id: 'sessionFallback', title: 'session/无头降级', description: '显式 session / 无头通道路由到 /worker、/headless，而不是 GUI 通道' },
] as const

const TOOLS = [
  'blender_rt_see', 'blender_rt_do', 'blender_rt_watch', 'blender_rt_cmd', 'blender_rt_commands',
  'blender_rt_loop', 'blender_rt_perf', 'blender_rt_opt', 'blender_rt_headless', 'blender_rt_plan',
  'blender_rt_worker', 'blender_rt_txn', 'blender_rt_preset', 'blender_rt_job', 'blender_viewport',
]

const rows: Row[] = []
function record(tool: string, aspect: string, status: 'PASS' | 'N/A', evidence: string, reason?: string): void {
  rows.push(reason === undefined ? { tool, aspect, status, evidence } : { tool, aspect, status, evidence, reason })
}

let backend: Backend
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

/** 一次调用的请求切片（避免前面用例的请求污染断言） */
function requestsSince(n: number): Array<Record<string, any>> {
  return backend.requests.slice(n) as Array<Record<string, any>>
}

function textOf(out: any): string {
  return typeof out === 'string' ? out : String(out?.text ?? '')
}

function hasUndefined(value: unknown, seen = new Set<unknown>()): boolean {
  if (value === undefined) return true
  if (value === null || typeof value !== 'object') return false
  if (seen.has(value)) return false
  seen.add(value)
  if (Array.isArray(value)) return value.some((v) => hasUndefined(v, seen))
  return Object.values(value as Record<string, unknown>).some((v) => hasUndefined(v, seen))
}

function lossless(value: unknown): void {
  expect(() => snapshotJsonValue(value)).not.toThrow()
  expect(JSON.parse(JSON.stringify(value))).toEqual(value)
}

// ───────────────────────────── 成功执行 ─────────────────────────────
const SUCCESS_CASES: Array<[string, Record<string, unknown>, string[], ((out: any, reqs: Array<Record<string, any>>) => void)?]> = [
  ['blender_rt_see', { max_size: 320, force: true }, ['视口帧 320px'], (out, reqs) => {
    expect(out.image?.attachmentId).toContain('viewport-')
    expect(reqs.some((r) => r.method === 'GET' && r.path === '/frame.png' && String(r.search).includes('size=320'))).toBe(true)
  }],
  ['blender_rt_do', { code: 'print(1)', force_see: true }, ['ACT ok', 'fake stdout'], (out, reqs) => {
    expect(out.image).toBeTruthy()
    expect(reqs.some((r) => r.method === 'POST' && r.path === '/act' && r.body.code === 'print(1)')).toBe(true)
  }],
  ['blender_rt_watch', { code: 'x = 1', seconds: 0.2, fps: 1, max_size: 120 }, ['WATCH 0.2s @1fps', 'timeline:']],
  ['blender_rt_cmd', { name: 'get_scene_info', params: { a: 1 } }, ['CMD get_scene_info ok'], (_out, reqs) => {
    const req = reqs.find((r) => r.method === 'POST' && r.path === '/cmd')
    expect(req?.body).toMatchObject({ name: 'get_scene_info', params: { a: 1 } })
  }],
  ['blender_rt_commands', {}, ['可用命令 39 条', 'polyhaven=off']],
  ['blender_rt_loop', { op: 'status' }, ['LOOP status'], (_out, reqs) => {
    expect(reqs.some((r) => r.path === '/loop' && r.body?.op === 'status')).toBe(true)
  }],
  ['blender_rt_perf', { op: 'status' }, ['PERF status']],
  ['blender_rt_opt', { op: 'join', args: '{"dry_run":true}' }, ['OPT join'], (_out, reqs) => {
    expect(reqs.find((r) => r.path === '/opt')?.body?.op).toBe('opt_join')
  }],
  ['blender_rt_headless', { script: 'print(1)' }, ['HEADLESS'], (out, reqs) => {
    expect(out.envelope?.status).toBe('done')
    expect(reqs.some((r) => r.path === '/headless' && r.body?.script === 'print(1)')).toBe(true)
  }],
  ['blender_rt_plan', { op: 'catalog' }, ['PLAN catalog ok'], (out) => {
    expect(out.envelope?.verdict).toBe('pass')
  }],
  ['blender_rt_worker', { op: 'start', name: 'w1' }, ['WORKER start ok', 'w1']],
  ['blender_rt_txn', { op: 'list' }, ['TXN list ok']],
  ['blender_rt_preset', { op: 'list' }, ['PRESET list ok']],
  ['blender_rt_job', { op: 'list' }, ['作业（1）', 'job-1']],
  ['blender_viewport', { op: 'status' }, ['"running":true'], (out) => {
    expect(typeof out).toBe('string')
    expect(JSON.parse(out).endpoint).toContain(String(backend.port))
  }],
]

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

  // 矩阵自检：15 tools × 7 aspects 必须每格一行；PASS/N/A 都必须是真实执行后记录的。
  expect(new Set(rows.map((r) => r.tool))).toEqual(new Set(TOOLS))
  for (const name of TOOLS) {
    for (const aspect of ASPECTS) {
      const hits = rows.filter((r) => r.tool === name && r.aspect === aspect.id)
      expect(hits.length, name + ' / ' + aspect.id).toBe(1)
    }
  }
  expect(rows.length).toBe(TOOLS.length * ASPECTS.length)
  const nA = rows.filter((r) => r.status === 'N/A')
  expect(nA.length).toBe(9) // paramError: commands=1；sessionFallback: 8 个无 session 通道的工具
  for (const r of nA) expect(r.reason, r.tool + '/' + r.aspect).toBeTruthy()

  const outPath = process.env.A1_MATRIX_OUT
  if (outPath) {
    const payload = {
      generatedAt: new Date().toISOString(),
      node: process.version,
      pluginVersion: (plugin as any).__internals?.PLUGIN_VERSION ?? null,
      tools: TOOLS.length,
      aspects: ASPECTS.map((a) => ({ id: a.id, title: a.title, description: a.description })),
      summary: {
        total: rows.length,
        pass: rows.filter((r) => r.status === 'PASS').length,
        notApplicable: nA.length,
      },
      rows,
    }
    fs.mkdirSync(outPath.replace(/[\\/][^\\/]+$/, ''), { recursive: true })
    fs.writeFileSync(outPath, JSON.stringify(payload, null, 2) + '\n', 'utf8')
  }
})

describe('A1 success：15 个工具的成功执行', () => {
  it('covers exactly the 15 registered model tools', () => {
    expect(tools.map((t) => t.name).sort()).toEqual([...TOOLS].sort())
  })

  it.each(SUCCESS_CASES)('%s success', async (name, args, contains, extra) => {
    const before = backend.requests.length
    const out = await run(name, args)
    for (const s of contains) expect(textOf(out), name).toContain(s)
    if (extra) extra(out, requestsSince(before))
    record(name, 'success', 'PASS', textOf(out).split('\n')[0].slice(0, 160))
  })
})

// ───────────────────────────── 参数错误 ─────────────────────────────
type ParamCheck = { args: Record<string, unknown>; throws?: RegExp; contains?: string[] }
const PARAM_CASES: Array<[string, ParamCheck[]]> = [
  ['blender_rt_see', [{ args: { max_size: 'abc' }, throws: /"max_size" must be an integer/ }]],
  ['blender_rt_do', [{ args: {}, contains: ['需要 code 或 file 之一'] }]],
  ['blender_rt_watch', [{ args: { seconds: 'abc' }, throws: /"seconds" must be a number/ }]],
  ['blender_rt_cmd', [{ args: {}, throws: /missing required property "name"/ }]],
  ['blender_rt_loop', [{ args: {}, throws: /missing required property "op"/ }]],
  ['blender_rt_perf', [{ args: {}, throws: /missing required property "op"/ }]],
  ['blender_rt_opt', [{ args: {}, throws: /missing required property "op"/ }]],
  ['blender_rt_headless', [
    { args: { blend: 'D:/x.blend' }, contains: ['没有参数 blend'] },
    { args: { file: '   ' }, contains: ['空字符串'] },
  ]],
  ['blender_rt_plan', [{ args: { op: 'catalog', args: '{bad' }, contains: ['args 不是合法 JSON 对象'] }]],
  ['blender_rt_worker', [{ args: {}, throws: /missing required property "op"/ }]],
  ['blender_rt_txn', [{ args: {}, throws: /missing required property "op"/ }]],
  ['blender_rt_preset', [
    { args: {}, throws: /missing required property "op"/ },
    { args: { op: 'save', data: '{bad' }, contains: ['data 不是合法 JSON'] },
  ]],
  ['blender_rt_job', [
    { args: {}, throws: /missing required property "op"/ },
    { args: { op: 'start', file: '' }, contains: ['空字符串'] },
  ]],
  ['blender_viewport', [{ args: {}, throws: /missing required property "op"/ }]],
]

describe('A1 paramError：参数校验与工具级非法参数', () => {
  it.each(PARAM_CASES)('%s param error', async (name, checks) => {
    const evidence: string[] = []
    for (const check of checks) {
      if (check.throws) {
        await expect(run(name, check.args)).rejects.toThrow(check.throws)
        evidence.push('schema reject: ' + String(check.throws))
      } else {
        const out = await run(name, check.args)
        for (const s of check.contains || []) expect(textOf(out), name).toContain(s)
        evidence.push('工具级可读错误：' + textOf(out).split('\n')[0].slice(0, 100))
      }
    }
    record(name, 'paramError', 'PASS', evidence.join('；'))
  })

  it('blender_rt_commands has no parameter fields to violate', async () => {
    const out = await run('blender_rt_commands', {})
    expect(textOf(out)).toContain('可用命令 39 条')
    record('blender_rt_commands', 'paramError', 'N/A', '空参数调用成功并返回命令目录', 'parameters 为空对象，没有可违反的字段；非法参数面为零')
  })
})

// ───────────────────────────── backend 不可达 ─────────────────────────────
const UNREACHABLE_ARGS: Record<string, Record<string, unknown>> = Object.fromEntries(
  SUCCESS_CASES.map(([name, args]) => [name, args])
)

describe('A1 backendUnreachable：连接被拒时 5s 内可读失败', () => {
  it.each(TOOLS)('%s settles without hanging', async (name) => {
    const port = await closedPort()
    const f = createFakeContext()
    f.ctx.get = fake.ctx.get
    plugin.apply(f.ctx, { autoStart: false, tui: false, port })
    const t = f.tools.find((x) => x.name === name)!
    const started = Date.now()
    let evidence = ''
    try {
      const out = await Promise.race([
        (t.execute as any)(UNREACHABLE_ARGS[name] ?? { op: 'status' }, {}),
        new Promise((_, reject) => setTimeout(() => reject(new Error('tool hung for 5s')), 5000)),
      ])
      evidence = 'resolve: ' + textOf(out).split('\n')[0].slice(0, 140)
    } catch (error: any) {
      expect(String(error)).toMatch(/backend|连接|connect|ECONNREFUSED|不可用|启动|http \d{3}|frame|宿主取消/i)
      evidence = 'reject: ' + String(error.message || error).split('\n')[0].slice(0, 140)
    } finally {
      f.dispose()
    }
    expect(Date.now() - started, name).toBeLessThan(5000)
    record(name, 'backendUnreachable', 'PASS', evidence)
  })
})

// ───────────────────────────── 超时 / abort ─────────────────────────────
const ABORT_CASES: Array<[string, Record<string, unknown>, string]> = [
  ['blender_rt_see', { max_size: 200, auto: false }, 'GET /frame.png'],
  ['blender_rt_do', { code: 'x = 1', see: false, auto: false }, 'POST /act'],
  ['blender_rt_watch', { code: 'x = 1', seconds: 0.2, fps: 1, auto: false }, 'GET /frame.png'],
  ['blender_rt_cmd', { name: 'ping' }, 'POST /cmd'],
  ['blender_rt_commands', {}, 'GET /commands'],
  ['blender_rt_loop', { op: 'start', spec: {} }, 'POST /loop'],
  ['blender_rt_perf', { op: 'bench' }, 'POST /perf'],
  ['blender_rt_opt', { op: 'join', args: '{}' }, 'POST /opt'],
  ['blender_rt_headless', { script: 'print(1)' }, 'POST /headless'],
  ['blender_rt_plan', { op: 'catalog' }, 'POST /plan'],
  ['blender_rt_worker', { op: 'start', name: 'w1' }, 'POST /worker'],
  ['blender_rt_txn', { op: 'snapshot', args: {} }, 'POST /txn'],
  ['blender_rt_preset', { op: 'save', data: '{}' }, 'POST /preset'],
  ['blender_rt_job', { op: 'start', script: 'print(1)' }, 'POST /job'],
  ['blender_viewport', { op: 'status' }, 'GET /status'],
]

describe('A1 timeoutAbort：exec.signal abort 后快速结束', () => {
  it.each(ABORT_CASES)('%s aborts within budget', async (name, args, hangKey) => {
    backend.hangOn(hangKey)
    const ac = new AbortController()
    const started = Date.now()
    const pending = run(name, args, { signal: ac.signal })
    setTimeout(() => ac.abort(), 60)
    let evidence = ''
    try {
      const out = await Promise.race([
        pending,
        new Promise((_, reject) => setTimeout(() => reject(new Error('tool hung for 6s')), 6000)),
      ])
      const text = textOf(out)
      expect(text, name).toMatch(/取消|后台化|abort/i)
      evidence = 'resolve: ' + text.split('\n')[0].slice(0, 140)
    } catch (error: any) {
      expect(String(error.message || error), name).toMatch(/宿主取消了本次等待/)
      evidence = 'reject: ' + String(error.message || error).split('\n')[0].slice(0, 140)
    } finally {
      backend.unhang(hangKey)
    }
    expect(Date.now() - started, name).toBeLessThan(5000)
    record(name, 'timeoutAbort', 'PASS', evidence)
  })
})

// ───────────────────────────── presenter ─────────────────────────────
describe('A1 presenter：presentCall / presentResult 卡片契约', () => {
  it.each(SUCCESS_CASES)('%s presenter', async (name, args) => {
    const out = await run(name, args)
    const t = tool(name)
    expect(typeof t.presentCall, name).toBe('function')
    expect(typeof t.presentResult, name).toBe('function')
    const call = (t.presentCall as any)(args)
    expect(call, name).toBeTruthy()
    expect(call.title, name).toMatch(/^Blender/)
    lossless(call)

    const blocks = (t.output as any)?.render?.(args, out) ?? []
    expect(Array.isArray(blocks), name).toBe(true)
    const result = (t.presentResult as any)(args, { content: blocks, isError: false })
    const expectCard = !!(args?.code || args?.script || out?.envelope)
    if (expectCard) {
      expect(result, name).toBeTruthy()
      expect(Array.isArray(result.content), name).toBe(true)
      expect(result.content.length, name).toBeGreaterThan(0)
      lossless(result)
    } else {
      expect(result, name).toBeUndefined()
    }
    record(name, 'presenter', 'PASS',
      'presentCall=' + call.title + '；presentResult=' + (result === undefined ? 'undefined(无信封/无代码 → 宿主通用卡片)' : 'card(' + result.content.length + ' blocks)'))
  })
})

// ───────────────────────────── lossless ─────────────────────────────
describe('A1 lossless：成功回执无损', () => {
  it.each(SUCCESS_CASES)('%s lossless', async (name, args) => {
    const out = await run(name, args)
    lossless(out)
    expect(hasUndefined(out), name).toBe(false)
    expect(textOf(out), name).not.toContain('回执已消毒')
    const shape = typeof out === 'string' ? 'string(' + out.length + ' chars)' : Object.keys(out).sort().join(',')
    record(name, 'lossless', 'PASS', 'snapshotJsonValue 通过；JSON round-trip 等价；' + shape)
  })
})

// ───────────────────────────── session / 无头降级 ─────────────────────────────
const SESSION_CASES: Array<[string, Record<string, unknown>, string, (reqs: Array<Record<string, any>>) => void]> = [
  ['blender_rt_see', { max_size: 200, session: 'w1' }, '无头 see', (reqs) => {
    expect(reqs.find((r) => r.path === '/worker')?.body?.op).toBe('see')
  }],
  ['blender_rt_do', { code: 'x = 1', session: 'w1', see: false }, 'ACT(session=w1)', (reqs) => {
    expect(reqs.find((r) => r.path === '/worker')?.body).toMatchObject({ op: 'exec', name: 'w1', code: 'x = 1' })
  }],
  ['blender_rt_watch', { code: 'x = 1', seconds: 0.2, fps: 1, max_size: 120, session: 'w1' }, 'WATCH(headless)', (reqs) => {
    expect(reqs.some((r) => r.path === '/worker')).toBe(true)
  }],
  ['blender_rt_loop', { op: 'start', target: 'session' }, 'LOOP start (session=dsh-loop)', (reqs) => {
    expect(reqs.some((r) => r.path === '/worker')).toBe(true)
  }],
  ['blender_rt_plan', { op: 'catalog', session: 'auto' }, '没有无头映射', (reqs) => {
    // session 参数先做无头路由判定；catalog 没有无头映射时立即返回可读失败，既不碰 GUI 也不起 worker。
    expect(reqs.some((r) => r.path === '/worker')).toBe(false)
    expect(reqs.some((r) => r.path === '/act')).toBe(false)
  }],
  ['blender_rt_worker', { op: 'exec', name: 'w1', code: 'x = 1' }, 'WORKER exec ok', (reqs) => {
    const body = reqs.find((r) => r.path === '/worker')?.body
    expect(body).toMatchObject({ op: 'exec', name: 'w1' })
  }],
  ['blender_rt_headless', { script: 'print(1)' }, 'HEADLESS', (reqs) => {
    // headless 工具本身就是无头通道（没有 session 参数），它的成功路径 = /headless
    expect(reqs.some((r) => r.path === '/headless')).toBe(true)
  }],
]

const SESSION_N_A: Array<[string, string]> = [
  ['blender_rt_cmd', '工具 schema 无 session 参数，只走 GUI addon 的 /cmd 通道，不路由 worker'],
  ['blender_rt_commands', '只读目录来自 GUI addon /commands，无 session 概念'],
  ['blender_rt_perf', 'perf 只有 GUI addon /perf 通道，无 session 参数'],
  ['blender_rt_opt', 'opt 只有 GUI addon /opt 通道，无 session 参数'],
  ['blender_rt_txn', 'txn 只有 GUI addon /txn 通道，无 session 参数'],
  ['blender_rt_preset', 'preset 只有 GUI addon /preset 通道，无 session 参数'],
  ['blender_rt_job', 'job 自己就是后台作业通道（/job），schema 无 session 参数，无头降级不适用'],
  ['blender_viewport', 'GUI 运维工具（/status、/launch、/doctor），schema 无 session 参数，不路由无头 worker'],
]

describe('A1 sessionFallback：显式 session / 无头通道', () => {
  it.each(SESSION_CASES)('%s routes through headless channel', async (name, args, contains, check) => {
    const before = backend.requests.length
    const out = await run(name, args)
    expect(textOf(out), name).toContain(contains)
    check(requestsSince(before))
    record(name, 'sessionFallback', 'PASS', textOf(out).split('\n')[0].slice(0, 160))
  })

  it.each(SESSION_N_A)('%s has no session channel', (name, reason) => {
    record(name, 'sessionFallback', 'N/A', 'schema 无 session 参数；成功/失败路径已在其它方面覆盖', reason)
  })
})