/**
 * 6 个模型工具 × 7 个方面的可执行矩阵（逐工具、逐方面真实调用）。
 *
 * 与既有 contract 测试的分工：
 *   - tool-execution / tool-failure-matrix / tool-presenter 各自覆盖一个切面；
 *   - 本文件是**审计用矩阵**：每个 (tool, aspect) 一行，真实执行后写入 JSON，并把
 *     "不适用" 的原因显式记录（没有任何静默跳过）。
 *
 * 输出：设置 A1_MATRIX_OUT=<path> 时写 JSON；否则只断言不落盘。
 * 依据：3.1 六工具面（session/op/py/see/run/inspect）——成功/参数错误/backend 不可达/
 * 超时 abort/presenter/lossless/session 门。
 */
import fs from 'node:fs'
import { afterAll, beforeAll, describe, expect, it } from 'vitest'
import { snapshotJsonValue } from '@deepseek-ai/dsh-session'
import * as plugin from '../../../../src/index'
import { createFakeContext, type CapturedTool } from '../../../helpers/fake-context'
import { closedPort, startFakeBackend } from '../../../helpers/fake-backend.mjs'

type Backend = Awaited<ReturnType<typeof startFakeBackend>>
type Row = {
  tool: string
  aspect: string
  status: 'PASS' | 'N/A'
  evidence: string
  reason?: string
}

const ASPECTS = [
  {
    id: 'success',
    title: '成功执行',
    description: '代表性参数返回可读回执，且请求打到预期的 Gateway 通道',
  },
  {
    id: 'paramError',
    title: '参数错误',
    description: 'schema 校验失败必须 reject；工具级非法参数必须返回可读错误而不是静默成功',
  },
  {
    id: 'backendUnreachable',
    title: 'backend 不可达',
    description: '连接被拒时 5s 内给出可读错误（不挂死）',
  },
  {
    id: 'timeoutAbort',
    title: '超时/abort',
    description: 'exec.signal abort 后 5s 内结束：reject 带取消提示，或 resolve 带取消提示',
  },
  {
    id: 'presenter',
    title: 'presenter',
    description: 'presentCall 必为 lossless 卡片；presentResult 按卡片契约返回卡片或 undefined',
  },
  {
    id: 'lossless',
    title: 'lossless',
    description: '成功回执可 snapshotJsonValue 且 JSON round-trip 等价，无消毒告警',
  },
  {
    id: 'sessionFallback',
    title: 'session 门',
    description:
      '需要 GUI 会话的工具在没有 ready 会话时给出决策请求，不启动 Blender、不转 headless',
  },
] as const

const TOOLS = [
  'blender_session',
  'blender_op',
  'blender_py',
  'blender_see',
  'blender_run',
  'blender_inspect',
]

const rows: Row[] = []
function record(
  tool: string,
  aspect: string,
  status: 'PASS' | 'N/A',
  evidence: string,
  reason?: string,
): void {
  rows.push(
    reason === undefined
      ? { tool, aspect, status, evidence }
      : { tool, aspect, status, evidence, reason },
  )
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
type SuccessCase = [
  string,
  Record<string, unknown>,
  string[],
  (out: any, reqs: Array<Record<string, any>>) => void,
]

const SUCCESS_CASES: SuccessCase[] = [
  [
    'blender_session',
    { action: 'list' },
    ['SESSION list'],
    (_out, reqs) => {
      const req = reqs.find((r) => r.path === '/lease')
      expect(req?.body).toMatchObject({ action: 'list' })
    },
  ],
  [
    'blender_op',
    { action: 'plan', op: 'catalog' },
    ['OP catalog'],
    (_out, reqs) => {
      const req = reqs.find((r) => r.path === '/plan')
      expect(req?.body).toMatchObject({ op: 'catalog' })
    },
  ],
  [
    'blender_py',
    { code: 'x = 1', see: false },
    ['PY ok'],
    (_out, reqs) => {
      const req = reqs.find((r) => r.path === '/act')
      expect(req?.body).toMatchObject({ code: 'x = 1', py: true })
    },
  ],
  [
    'blender_see',
    { max_size: 1024 },
    ['SEE viewport'],
    (_out, reqs) => {
      const req = reqs.find((r) => r.path === '/view')
      expect(req?.body).toMatchObject({ mode: 'current', width: 1024, height: 576 })
    },
  ],
  [
    'blender_run',
    { action: 'list' },
    ['RUN list'],
    (_out, reqs) => {
      const req = reqs.find((r) => r.path === '/job')
      expect(req?.body).toMatchObject({ surface: 'queue', op: 'list' })
    },
  ],
  [
    'blender_inspect',
    {},
    ['INSPECT help'],
    (_out, reqs) => {
      expect(reqs.some((r) => r.method === 'GET' && r.path === '/commands')).toBe(true)
    },
  ],
]

beforeAll(async () => {
  backend = await startFakeBackend()
  fake = createFakeContext()
  const attachments = {
    imageLimits: { mediaTypes: ['image/png'] },
    async saveImage({ data, name }: { data: Uint8Array; name: string }) {
      return {
        attachmentId: 'att-' + name,
        mediaType: 'image/png',
        bytes: data.length,
        width: 1,
        height: 1,
        name,
      }
    },
  }
  fake.ctx.get = ((name: string) => (name === 'attachments' ? attachments : undefined)) as any
  plugin.apply(fake.ctx, { autoStart: false, tui: false, port: backend.port })
  tools = fake.tools
})

afterAll(async () => {
  fake.dispose()
  await backend.close()

  // 矩阵自检：6 tools × 7 aspects 必须每格一行；PASS/N/A 都必须是真实执行后记录的。
  expect(new Set(rows.map((r) => r.tool))).toEqual(new Set(TOOLS))
  for (const name of TOOLS) {
    for (const aspect of ASPECTS) {
      const hits = rows.filter((r) => r.tool === name && r.aspect === aspect.id)
      expect(hits.length, name + ' / ' + aspect.id).toBe(1)
    }
  }
  expect(rows.length).toBe(TOOLS.length * ASPECTS.length)
  const nA = rows.filter((r) => r.status === 'N/A')
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

describe('success：6 个工具的成功执行', () => {
  it('covers exactly the 6 registered model tools', () => {
    expect(tools.map((t) => t.name).sort()).toEqual([...TOOLS].sort())
  })

  it.each(SUCCESS_CASES)('%s success', async (name, args, contains, extra) => {
    const before = backend.requests.length
    const out = await run(name, args)
    for (const s of contains) expect(textOf(out), name).toContain(s)
    extra(out, requestsSince(before))
    record(name, 'success', 'PASS', textOf(out).split('\n')[0].slice(0, 160))
  })
})

// ───────────────────────────── 参数错误 ─────────────────────────────
type ParamCheck = { args: Record<string, unknown>; throws?: RegExp; contains?: string[] }
const PARAM_CASES: Array<[string, ParamCheck[]]> = [
  ['blender_session', [{ args: {}, throws: /action/ }]],
  ['blender_op', [{ args: { action: 'nope' }, contains: ['blender_op action'] }]],
  ['blender_py', [{ args: {}, contains: ['code'] }]],
  ['blender_see', [{ args: { view_size: '1024x1024' }, contains: ['view_size'] }]],
  [
    'blender_run',
    [{ args: { action: 'submit', kind: 'render', script: 'print(1)' }, contains: ['script'] }],
  ],
  ['blender_inspect', [{ args: { command: 123 }, throws: /command/ }]],
]

describe('paramError：参数校验与工具级非法参数', () => {
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
})

// ───────────────────────────── backend 不可达 ─────────────────────────────
const UNREACHABLE_ARGS: Record<string, Record<string, unknown>> = Object.fromEntries(
  SUCCESS_CASES.map(([name, args]) => [name, args]),
) as Record<string, Record<string, unknown>>

describe('backendUnreachable：连接被拒时 5s 内可读失败', () => {
  it.each(TOOLS)('%s settles without hanging', async (name) => {
    const port = await closedPort()
    const f = createFakeContext()
    f.ctx.get = fake.ctx.get
    plugin.apply(f.ctx, { autoStart: false, tui: false, port })
    const t = f.tools.find((x) => x.name === name)!
    const started = Date.now()
    let evidence: string
    try {
      const out = await Promise.race([
        (t.execute as any)(UNREACHABLE_ARGS[name] ?? {}, {}),
        new Promise((_, reject) => setTimeout(() => reject(new Error('tool hung for 5s')), 5000)),
      ])
      evidence = 'resolve: ' + textOf(out).split('\n')[0].slice(0, 140)
    } catch (error: any) {
      expect(String(error)).toMatch(
        /backend|连接|connect|ECONNREFUSED|不可用|启动|Gateway|http \d{3}|宿主取消/i,
      )
      evidence =
        'reject: ' +
        String(error.message || error)
          .split('\n')[0]
          .slice(0, 140)
    } finally {
      f.dispose()
    }
    expect(Date.now() - started, name).toBeLessThan(5000)
    record(name, 'backendUnreachable', 'PASS', evidence)
  })
})

// ───────────────────────────── 超时 / abort ─────────────────────────────
const ABORT_CASES: Array<[string, Record<string, unknown>, string]> = [
  ['blender_session', { action: 'list' }, 'POST /lease'],
  ['blender_op', { action: 'plan', op: 'catalog' }, 'POST /plan'],
  ['blender_py', { code: 'x = 1', see: false }, 'POST /act'],
  ['blender_see', { max_size: 1024 }, 'POST /view'],
  ['blender_run', { action: 'list' }, 'POST /job'],
  ['blender_inspect', {}, 'GET /commands'],
]

describe('timeoutAbort：exec.signal abort 后快速结束', () => {
  it.each(ABORT_CASES)('%s aborts within budget', async (name, args, hangKey) => {
    backend.hangOn(hangKey)
    const ac = new AbortController()
    const started = Date.now()
    const pending = run(name, args, { signal: ac.signal })
    setTimeout(() => ac.abort(), 60)
    let evidence: string
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
      evidence =
        'reject: ' +
        String(error.message || error)
          .split('\n')[0]
          .slice(0, 140)
    } finally {
      backend.unhang(hangKey)
    }
    expect(Date.now() - started, name).toBeLessThan(5000)
    record(name, 'timeoutAbort', 'PASS', evidence)
  })
})

// ───────────────────────────── presenter ─────────────────────────────
describe('presenter：presentCall / presentResult 卡片契约', () => {
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
    if (result !== undefined) {
      expect(Array.isArray(result.content), name).toBe(true)
      expect(result.content.length, name).toBeGreaterThan(0)
      lossless(result)
    }
    record(
      name,
      'presenter',
      'PASS',
      'presentCall=' +
        call.title +
        '；presentResult=' +
        (result === undefined
          ? 'undefined(无信封/无代码 → 宿主通用卡片)'
          : 'card(' + result.content.length + ' blocks)'),
    )
  })
})

// ───────────────────────────── lossless ─────────────────────────────
describe('lossless：成功回执无损', () => {
  it.each(SUCCESS_CASES)('%s lossless', async (name, args) => {
    const out = await run(name, args)
    lossless(out)
    expect(hasUndefined(out), name).toBe(false)
    expect(textOf(out), name).not.toContain('回执已消毒')
    const shape =
      typeof out === 'string'
        ? 'string(' + out.length + ' chars)'
        : Object.keys(out).sort().join(',')
    record(name, 'lossless', 'PASS', 'snapshotJsonValue 通过；JSON round-trip 等价；' + shape)
  })
})

// ───────────────────────────── session 门 ─────────────────────────────
// fake backend 默认返回一个 ready user-gui 会话；这里显式把 /lease list 改成空，
// 验证 py/see 在没有 ready 会话时给出决策请求，而不是启动 Blender 或转 headless。
const NO_SESSION_HANDLER = () => ({
  status: 200,
  headers: { 'content-type': 'application/json' },
  body: JSON.stringify({ ok: true, sessions: [] }),
})

const SESSION_GATED: Array<[string, Record<string, unknown>]> = [
  ['blender_py', { code: 'x = 1', see: false }],
  ['blender_see', { max_size: 1024 }],
]

describe('sessionFallback：无 ready 会话时拒绝并给两个选择', () => {
  it.each(SESSION_GATED)('%s requires a ready GUI session', async (name, args) => {
    backend.setHandler('POST /lease', NO_SESSION_HANDLER)
    const before = backend.requests.length
    let evidence: string
    try {
      const out = await run(name, args)
      const text = textOf(out)
      expect(text, name).toContain('session_required')
      expect(text, name).toContain('blender_session')
      const reqs = requestsSince(before)
      expect(
        reqs.some((r) => r.path === '/worker' || r.path === '/headless'),
        name,
      ).toBe(false)
      expect(
        reqs.some((r) => r.path === '/act' || r.path === '/view'),
        name,
      ).toBe(false)
      evidence = text.split('\n')[0].slice(0, 160)
    } finally {
      backend.setHandler('POST /lease', null)
    }
    record(name, 'sessionFallback', 'PASS', evidence)
  })

  it.each(['blender_session', 'blender_op', 'blender_run', 'blender_inspect'])(
    '%s has no headless fallback concept',
    (name) => {
      record(
        name,
        'sessionFallback',
        'N/A',
        '工具的失败路径已由 backendUnreachable / paramError 覆盖',
        '3.1 没有 GUI→headless 自动降级：session 是会话面，op=catalog/dry_run 与 inspect 是只读面，run 直接提交队列',
      )
    },
  )
})
