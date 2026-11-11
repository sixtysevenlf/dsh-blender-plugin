/**
 * 29 条 HTTP 路由 × 5 个方面的可执行矩阵（逐路由真实发 HTTP 请求）。
 *
 * 为什么用 in-process 真实路由 + fake engine：
 *   - 被测对象是 runtime/http 的**路由契约层**：真实 createRouter / createRoutes /
 *     CONTRACT_ROUTES / lease / response / selectionStore 全部是生产代码；
 *   - 只有 engine（Blender 通道）是可控 stub —— 否则 /act、/cmd 等必须启动真实 Blender，
 *     路由契约测试会退化成 E2E 且无法稳定注入"下游失败"；
 *   - 真实 server 进程启动、29 条启动日志、/health 等 smoke 由
 *     tests/integration/js/backend-routes.test.ts 覆盖（本次也一并跑，见 evidence/a5-routes.log）。
 *
 * 输出：设置 A5_MATRIX_OUT=<path> 时写 JSON。
 * 依据：行为矩阵（每个路由：正常/非法参数/错误码 content-type/lease 写保护/CORS，
 *       非法输入不得产生 500）。
 */
import fs from 'node:fs'
import http from 'node:http'
import net from 'node:net'
import { afterAll, beforeAll, beforeEach, describe, expect, it } from 'vitest'
import { createRouter } from '../../../runtime/http/router'
import { createRoutes, CONTRACT_ROUTES } from '../../../runtime/http/routes/index'
import { json, streamJson, readBody, hostJson, hostErrText } from '../../../runtime/http/response'
import {
  LEASE,
  leaseView,
  releaseStaleLease,
  leaseAcquire,
  leaseGate,
  isReadOnly,
} from '../../../runtime/http/lease'
import { trimJob, clampIntParam } from '../../../runtime/http/jobs'
import { HOST, stats, pushRtt, rttView, reqCounts, bump } from '../../../runtime/http/config'
import { describeConfig } from '../../../runtime/config'
import { engineProvenance } from '../../../runtime/engine'
import { createSelectionStore } from '../../../runtime/selectionstore'

type Row = {
  route: string
  aspect: string
  status: 'PASS' | 'N/A'
  evidence: string
  reason?: string
}

const ASPECTS = [
  {
    id: 'normal',
    title: '正常请求',
    description: '正确方法+最小合法输入 → 契约状态码与 content-type',
  },
  {
    id: 'invalidInput',
    title: '非法输入',
    description: 'malformed JSON / 非法 query / 方法不匹配 → 不产生 500，按契约返回',
  },
  {
    id: 'errorMapping',
    title: '错误码/content-type',
    description: '注入 engine 下游失败 → 按契约映射状态码（200 ok:false / 502 / 500）与媒体类型',
  },
  {
    id: 'lease',
    title: 'lease 写保护',
    description: '他人持租约时写操作 409、只读操作与只读路由放行',
  },
  {
    id: 'cors',
    title: 'CORS',
    description: '/host/* 的 Origin 白名单与预检（非 host 路由按契约无 CORS）',
  },
] as const

const TINY_PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==',
  'base64',
)

const rows: Row[] = []
const invalidStatuses: number[] = []
function record(
  route: string,
  aspect: string,
  status: 'PASS' | 'N/A',
  evidence: string,
  reason?: string,
): void {
  rows.push(
    reason === undefined
      ? { route, aspect, status, evidence }
      : { route, aspect, status, evidence, reason },
  )
}

// ───────────────────────────── fake engine（仅替换 Blender 通道） ─────────────────────────────
const faults = new Set<string>()
function fault(key: string): never {
  const e: any = new Error('injected fault: ' + key)
  e.kind = 'internal'
  e.diagnosis = { kind: 'injected_fault' }
  throw e
}
function ok(key: string, value: unknown): unknown {
  if (faults.has(key)) fault(key)
  return value
}

const engine: any = {
  status: async () =>
    ok('status', {
      region: { window: null, views: [] },
      plugin: { version: '3.1.0-alpha.1' },
      runs: [],
      jobs: [],
      ledger: { writable: true },
    }),
  doctor: async () => ok('doctor', { kind: 'ok' }),
  launchBlender: async () => ok('launchBlender', { ok: true, launched: false, already: true }),
  metrics: () =>
    ok('metrics', {
      inflight: 0,
      calls: 0,
      errors: 0,
      lastCmd: null,
      lastDiagnosis: null,
      aiPausedAt: null,
    }),
  renderFlag: () => ok('renderFlag', { present: false }),
  sceneEpoch: async () => 'epoch-1',
  frame: async () => ok('frame', { png: TINY_PNG, ms: 1, hash: 'frame-hash' }),
  frameArea: async () => ok('frameArea', { png: TINY_PNG, ms: 1, hash: 'area-hash' }),
  act: async () =>
    ok('act', { ok: true, ms: 1, mainThreadMs: 1, executed: true, stdout: 'ok', stderr: '' }),
  commands: async () =>
    ok('commands', {
      scene: 'Scene',
      file: null,
      total: 25,
      available: ['ping'],
      disabled: [],
      integrations: {},
    }),
  cmd: async () => ok('cmd', { pong: true }),
  loopStart: async () => ok('loopStart', { ok: true }),
  loopStatus: async () => ok('loopStatus', { ok: true, op: 'status' }),
  loopStop: async () => ok('loopStop', { ok: true }),
  loopBench: async () => ok('loopBench', { ok: true }),
  loopBoard: async () => ok('loopBoard', { ok: true }),
  loopExport: async () => ok('loopExport', { ok: true }),
  loopHelp: async () => ok('loopHelp', { ok: true }),
  perf: async () => ok('perf', { ok: true }),
  opt: async () => ok('opt', { ok: true }),
  view: async () =>
    ok('view', { png: TINY_PNG, meta: { mode: 'viewport', ms: 1, width: 1, height: 1 } }),
  headless: async () => ok('headless', { ok: true, ms: 2, status: 'done' }),
  plan: async () => ok('plan', { ok: true, verdict: 'pass', summary: 'stub' }),
  worker: {
    start: async () => ok('workerStart', { ok: true, name: 'default', alive: true }),
    exec: async () => ok('workerExec', { ok: true, ms: 1 }),
    see: async () => ok('workerSee', { ok: true }),
    watch: async () => ok('workerWatch', { ok: true }),
    reset: async () => ok('workerReset', { ok: true }),
    status: async () => ok('workerStatus', { ok: true, name: 'default', alive: true }),
    stop: async () => ok('workerStop', { ok: true }),
    restart: async () => ok('workerRestart', { ok: true }),
    list: () => ok('workerList', []),
    snapshot: () => ok('workerSnapshot', null),
    state: () => ok('workerState', null),
  },
  txn: async () => ok('txn', { ok: true, snapshots: [] }),
  preset: async () => ok('preset', { ok: true, count: 0, presets: [] }),
  job: {
    start: () => ok('jobStart', { id: 'job-1', status: 'running' }),
    status: () => ok('jobStatus', { id: 'job-1', status: 'done' }),
    collect: (id: unknown) =>
      ok(
        'jobCollect',
        id
          ? { ok: true, id: String(id), status: 'done', stdoutTail: 'out', stderrTail: 'err' }
          : { ok: false, status: 'unknown' },
      ),
    kill: () => ok('jobKill', { id: 'job-1', status: 'killed' }),
    list: () => ok('jobList', [{ id: 'job-1', status: 'done', ms: 7, artifacts: [] }]),
    wait: async () => ok('jobWait', { id: 'job-1', status: 'done' }),
  },
  hostSceneItems: async () =>
    ok('hostSceneItems', { objects: [{ name: 'Cube' }], materials: [], collections: [] }),
  start: async () => ok('start', { ok: true }),
  stop: async () => ok('stop', { ok: true }),
  protocol: async () => ok('protocol', { ok: true }),
}

let server: http.Server
let port: number
let base: string

function freePort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const s = net.createServer()
    s.once('error', reject)
    s.listen(0, '127.0.0.1', () => {
      const p = (s.address() as net.AddressInfo).port
      s.close(() => resolve(p))
    })
  })
}

type Resp = { status: number; headers: Headers; text: string; json: any }
async function call(
  path: string,
  init: {
    method?: string
    body?: unknown
    raw?: string
    origin?: string
    headers?: Record<string, string>
  } = {},
): Promise<Resp> {
  const headers: Record<string, string> = { ...(init.headers || {}) }
  let body: string | undefined
  if (init.raw !== undefined) body = init.raw
  else if (init.body !== undefined) body = JSON.stringify(init.body)
  if (body !== undefined) headers['content-type'] = 'application/json'
  if (init.origin) headers.origin = init.origin
  const res = await fetch(base + path, { method: init.method || 'GET', headers, body })
  const text = await res.text()
  let json: any
  try {
    json = JSON.parse(text)
  } catch {
    json = null
  }
  return { status: res.status, headers: res.headers, text, json }
}

function expectJson(r: Resp, status: number): any {
  expect(r.status, r.text.slice(0, 200)).toBe(status)
  expect(r.headers.get('content-type')).toBe('application/json; charset=utf-8')
  expect(r.headers.get('cache-control')).toBe('no-store')
  expect(r.json).toBeTruthy()
  return r.json
}
function expectNdjson(r: Resp, status = 200): any {
  expect(r.status).toBe(status)
  expect(r.headers.get('content-type')).toBe('application/x-ndjson; charset=utf-8')
  expect(r.headers.get('x-dsh-stream')).toBe('1')
  const lines = r.text.trim().split('\n').filter(Boolean)
  expect(lines.length).toBeGreaterThan(0)
  return JSON.parse(lines[lines.length - 1])
}
function expectPng(r: Resp, status = 200): void {
  expect(r.status).toBe(status)
  expect(r.headers.get('content-type')).toBe('image/png')
  expect(r.headers.get('cache-control')).toBe('no-store')
}
function parseAny(r: Resp): any {
  const ct = r.headers.get('content-type') || ''
  if (ct.includes('application/x-ndjson')) {
    const lines = r.text.trim().split('\n').filter(Boolean)
    return JSON.parse(lines[lines.length - 1])
  }
  return r.json
}

async function releaseLease(): Promise<void> {
  await call('/release', { method: 'POST', body: { holder: '' } })
}

beforeAll(async () => {
  port = await freePort()
  const store = createSelectionStore()
  const E: any = {
    engine,
    selectionStore: store,
    PORT: port,
    HOST,
    stats,
    json,
    streamJson,
    readBody,
    pushRtt,
    rttView,
    reqCounts,
    bump,
    LEASE,
    leaseView,
    releaseStaleLease,
    leaseAcquire,
    leaseGate,
    isReadOnly,
    trimJob,
    clampIntParam,
    describeConfig,
    engineProvenance,
    hostJson,
    hostErrText,
  }
  const routes = createRoutes(E)
  server = http.createServer(createRouter({ HOST, PORT: port, bump, routes }))
  await new Promise<void>((resolve) => server.listen(port, '127.0.0.1', () => resolve()))
  base = 'http://127.0.0.1:' + String(port)
})

beforeEach(async () => {
  faults.clear()
  await releaseLease()
})

afterAll(async () => {
  faults.clear()
  await releaseLease()
  server.closeAllConnections?.()
  await new Promise<void>((resolve) => server.close(() => resolve()))

  // ── 矩阵自检 ──
  expect(CONTRACT_ROUTES.length).toBe(29)
  expect(new Set(CONTRACT_ROUTES).size).toBe(29)
  expect(ROUTE_PATHS.slice().sort()).toEqual(CONTRACT_ROUTES.slice().sort())
  for (const path of ROUTE_PATHS) {
    for (const aspect of ASPECTS) {
      const hits = rows.filter((r) => r.route === path && r.aspect === aspect.id)
      expect(hits.length, path + ' / ' + aspect.id).toBe(1)
    }
  }
  expect(rows.length).toBe(ROUTE_PATHS.length * ASPECTS.length)
  // 硬约束：非法输入不得产生 500（29 条各一个记录）
  expect(invalidStatuses.length).toBe(29)
  expect(invalidStatuses.filter((s) => s === 500)).toEqual([])
  for (const r of rows.filter((x) => x.status === 'N/A'))
    expect(r.reason, r.route + '/' + r.aspect).toBeTruthy()

  const outPath = process.env.A5_MATRIX_OUT
  if (outPath) {
    const payload = {
      generatedAt: new Date().toISOString(),
      node: process.version,
      routes: ROUTE_PATHS.length,
      contractRoutes: CONTRACT_ROUTES,
      aspects: ASPECTS.map((a) => ({ id: a.id, title: a.title, description: a.description })),
      summary: {
        total: rows.length,
        pass: rows.filter((r) => r.status === 'PASS').length,
        notApplicable: rows.filter((r) => r.status === 'N/A').length,
        invalidRequests: invalidStatuses.length,
        invalidRequestsProducing500: invalidStatuses.filter((s) => s === 500).length,
      },
      rows,
    }
    fs.mkdirSync(outPath.replace(/[\\/][^\\/]+$/, ''), { recursive: true })
    fs.writeFileSync(outPath, JSON.stringify(payload, null, 2) + '\n', 'utf8')
  }
})

const ROUTE_PATHS = CONTRACT_ROUTES.slice()

// ───────────────────────────── normal ─────────────────────────────
const NORMAL: Array<[string, () => Promise<string>]> = [
  [
    '/health',
    async () => {
      const j = expectJson(await call('/health'), 200)
      expect(j.ok).toBe(true)
      expect(j.lease.active).toBe(false)
      expect(typeof j.backend.pid).toBe('number')
      return (
        '200 application/json · ok=true lease.active=false backend.pid=' + String(j.backend.pid)
      )
    },
  ],
  [
    '/status',
    async () => {
      const j = expectJson(await call('/status'), 200)
      expect(j.ok).toBe(true)
      expect(j.plugin.version).toBeTruthy()
      return '200 application/json · ok=true plugin.version=' + String(j.plugin.version)
    },
  ],
  [
    '/doctor',
    async () => {
      const j = expectJson(await call('/doctor'), 200)
      expect(j.ok).toBe(true)
      expect(String(j.config.http)).toContain(String(port))
      return '200 application/json · ok=true config.http=' + String(j.config.http)
    },
  ],
  [
    '/launch',
    async () => {
      const j = expectJson(await call('/launch', { method: 'POST', body: {} }), 200)
      expect(j.ok).toBe(true)
      return '200 application/json · ok=true（stub already=true）'
    },
  ],
  [
    '/who',
    async () => {
      const j = expectJson(await call('/who'), 200)
      expect(j.ok).toBe(true)
      expect(j.lease.active).toBe(false)
      expect(j.metrics).toBeTruthy()
      return '200 application/json · ok=true lease/metrics 均存在'
    },
  ],
  [
    '/lease',
    async () => {
      const granted = expectJson(
        await call('/lease', { method: 'POST', body: { holder: 'matrix-holder' } }),
        200,
      )
      expect(granted.ok).toBe(true)
      expect(granted.lease.active).toBe(true)
      const released = expectJson(
        await call('/release', { method: 'POST', body: { holder: 'matrix-holder' } }),
        200,
      )
      expect(released.released).toBe('matrix-holder')
      expect(released.lease.active).toBe(false)
      return '200 application/json · acquire→active=true，release→released=matrix-holder'
    },
  ],
  [
    '/release',
    async () => {
      const j = expectJson(await call('/release', { method: 'POST', body: {} }), 200)
      expect(j.ok).toBe(true)
      expect(j.lease.active).toBe(false)
      return '200 application/json · ok=true（无租约时 released=null）'
    },
  ],
  [
    '/frame',
    async () => {
      const j = expectJson(await call('/frame'), 410)
      expect(j.error).toBe('gone')
      return '410 application/json · error=gone（已移除的人肉面板）'
    },
  ],
  [
    '/frame.png',
    async () => {
      expectPng(await call('/frame.png?size=320'))
      return '200 image/png · cache-control=no-store'
    },
  ],
  [
    '/act',
    async () => {
      const j = expectJson(await call('/act', { method: 'POST', body: { code: 'x = 1' } }), 200)
      expect(j.ok).toBe(true)
      expect(j.executed).toBe(true)
      return '200 application/json · ok=true executed=true'
    },
  ],
  [
    '/commands',
    async () => {
      const j = expectJson(await call('/commands'), 200)
      expect(j.ok).toBe(true)
      expect(j.total).toBe(25)
      return '200 application/json · ok=true total=25'
    },
  ],
  [
    '/cmd',
    async () => {
      const j = expectJson(
        await call('/cmd', { method: 'POST', body: { name: 'ping', params: {} } }),
        200,
      )
      expect(j.ok).toBe(true)
      expect(j.result).toMatchObject({ pong: true })
      return '200 application/json · ok=true result.pong=true'
    },
  ],
  [
    '/loop',
    async () => {
      const j = expectJson(await call('/loop', { method: 'POST', body: { op: 'status' } }), 200)
      expect(j.ok).toBe(true)
      expect(j.op).toBe('status')
      return '200 application/json · ok=true op=status'
    },
  ],
  [
    '/perf',
    async () => {
      const j = expectJson(await call('/perf', { method: 'POST', body: { op: 'status' } }), 200)
      expect(j.ok).toBe(true)
      expect(j.op).toBe('status')
      return '200 application/json · ok=true op=status'
    },
  ],
  [
    '/opt',
    async () => {
      const j = expectJson(await call('/opt', { method: 'POST', body: { op: 'opt_analyze' } }), 200)
      expect(j.ok).toBe(true)
      expect(j.op).toBe('opt_analyze')
      return '200 application/json · ok=true op=opt_analyze'
    },
  ],
  [
    '/view',
    async () => {
      const j = expectJson(await call('/view', { method: 'POST', body: { asJson: true } }), 200)
      expect(j.ok).toBe(true)
      expect(j.meta).toBeTruthy()
      return '200 application/json · ok=true meta 存在（asJson 分支）'
    },
  ],
  [
    '/headless',
    async () => {
      const j = expectNdjson(
        await call('/headless', { method: 'POST', body: { script: 'print(1)' } }),
      )
      expect(j.ok).toBe(true)
      expect(j.result).toBeTruthy()
      return '200 application/x-ndjson · x-dsh-stream=1 · ok=true'
    },
  ],
  [
    '/plan',
    async () => {
      const j = expectJson(await call('/plan', { method: 'POST', body: { op: 'catalog' } }), 200)
      expect(j.ok).toBe(true)
      expect(j.op).toBe('catalog')
      return '200 application/json · ok=true op=catalog'
    },
  ],
  [
    '/worker',
    async () => {
      const j = expectNdjson(await call('/worker', { method: 'POST', body: { op: 'status' } }))
      expect(j.ok).toBe(true)
      expect(j.result.ok).toBe(true)
      return '200 application/x-ndjson · x-dsh-stream=1 · ok=true op=status'
    },
  ],
  [
    '/txn',
    async () => {
      const j = expectNdjson(await call('/txn', { method: 'POST', body: { op: 'list' } }))
      expect(j.ok).toBe(true)
      expect(j.op).toBe('list')
      return '200 application/x-ndjson · x-dsh-stream=1 · ok=true op=list'
    },
  ],
  [
    '/preset',
    async () => {
      const j = expectNdjson(await call('/preset', { method: 'POST', body: { op: 'list' } }))
      expect(j.ok).toBe(true)
      expect(j.op).toBe('list')
      return '200 application/x-ndjson · x-dsh-stream=1 · ok=true op=list'
    },
  ],
  [
    '/job',
    async () => {
      const j = expectJson(await call('/job', { method: 'POST', body: { op: 'list' } }), 200)
      expect(j.ok).toBe(true)
      expect(j.op).toBe('list')
      expect(Array.isArray(j.jobs)).toBe(true)
      return '200 application/json · ok=true op=list jobs 数组'
    },
  ],
  [
    '/host/scene-items',
    async () => {
      const j = expectJson(await call('/host/scene-items?q=Cube&limit=5'), 200)
      expect(j.ok).toBe(true)
      expect(Array.isArray(j.objects)).toBe(true)
      return '200 application/json · ok=true objects 数组（limit 夹取生效）'
    },
  ],
  [
    '/host/jobs',
    async () => {
      const j = expectJson(await call('/host/jobs?limit=5'), 200)
      expect(j.ok).toBe(true)
      expect(Array.isArray(j.jobs)).toBe(true)
      expect(j.total).toBeGreaterThanOrEqual(j.jobs.length)
      return '200 application/json · ok=true jobs/total 存在'
    },
  ],
  [
    '/host/jobs/log',
    async () => {
      const j = expectJson(await call('/host/jobs/log?id=job-1&tail=100'), 200)
      expect(j.ok).toBe(true)
      expect(j.id).toBe('job-1')
      expect(j.stdout).toBe('out')
      return '200 application/json · ok=true id=job-1 stdout 存在'
    },
  ],
  [
    '/host/state',
    async () => {
      const j = expectJson(await call('/host/state'), 200)
      expect(j.ok).toBe(true)
      expect(j.channel).toBeTruthy()
      expect(j.jobs).toBeTruthy()
      return '200 application/json · ok=true channel/jobs 存在（纯内存视图）'
    },
  ],
  [
    '/host/selection',
    async () => {
      const j = expectJson(
        await call('/host/selection', {
          method: 'POST',
          body: { selection: { names: ['Cube'], count: 1 }, scene: { name: 'Scene' } },
        }),
        200,
      )
      expect(j.ok).toBe(true)
      expect(j.seq).toBeGreaterThan(0)
      return '200 application/json · ok=true seq=' + String(j.seq)
    },
  ],
  [
    '/host/selection/latest',
    async () => {
      const put = expectJson(
        await call('/host/selection', {
          method: 'POST',
          body: { selection: { names: ['Camera'], count: 1 } },
        }),
        200,
      )
      const j = expectJson(await call('/host/selection/latest'), 200)
      expect(j.ok).toBe(true)
      expect(j.seq).toBe(put.seq)
      expect(j.unconsumed).toBe(true)
      return '200 application/json · ok=true seq=' + String(j.seq) + ' unconsumed=true'
    },
  ],
  [
    '/host/selection/consume',
    async () => {
      const put = expectJson(
        await call('/host/selection', {
          method: 'POST',
          body: { selection: { names: ['Cube'], count: 1 } },
        }),
        200,
      )
      const j = expectJson(
        await call('/host/selection/consume', { method: 'POST', body: { seq: put.seq } }),
        200,
      )
      expect(j.ok).toBe(true)
      expect(j.consumed_seq).toBe(put.seq)
      return '200 application/json · ok=true consumed_seq=' + String(j.consumed_seq)
    },
  ],
]

describe('normal：29 条路由的正常请求', () => {
  it('contract route table has exactly the 29 advertised routes', () => {
    expect(CONTRACT_ROUTES).toHaveLength(29)
    expect(new Set(CONTRACT_ROUTES).size).toBe(29)
    expect(CONTRACT_ROUTES).toContain('/host/selection/consume')
    expect(CONTRACT_ROUTES).not.toContain('/stream.mjpg')
  })

  it.each(NORMAL)('%s normal', async (path, fn) => {
    const evidence = await fn()
    record(path, 'normal', 'PASS', evidence)
  })
})

// ───────────────────────────── invalid input ─────────────────────────────
const INVALID: Array<[string, () => Promise<string>]> = [
  [
    '/health',
    async () => {
      const bad = await call('/health?size=abc&unknown=1')
      expectJson(bad, 200)
      const wrong = await call('/health', { method: 'POST', body: {} })
      expectJson(wrong, 404)
      return '非法 query 忽略→200；方法不匹配→404 application/json（均非 500）'
    },
  ],
  [
    '/status',
    async () => {
      expectJson(await call('/status?x=%zz'), 200)
      expectJson(await call('/status', { method: 'POST' }), 404)
      return '非法 query→200；方法不匹配→404（非 500）'
    },
  ],
  [
    '/doctor',
    async () => {
      expectJson(await call('/doctor?area=abc'), 200)
      expectJson(await call('/doctor', { method: 'PUT' }), 404)
      return '非法 query→200；方法不匹配→404（非 500）'
    },
  ],
  [
    '/launch',
    async () => {
      const bad = await call('/launch', { method: 'POST', raw: '{not-json' })
      expectJson(bad, 200) // JSON parse 失败 ⇒ payload {}，按正常流程
      expectJson(await call('/launch'), 404)
      return 'malformed JSON→200 json；GET→404（非 500）'
    },
  ],
  [
    '/who',
    async () => {
      expectJson(await call('/who?zone=abc'), 200)
      expectJson(await call('/who', { method: 'POST' }), 404)
      return '非法 query→200；方法不匹配→404（非 500）'
    },
  ],
  [
    '/lease',
    async () => {
      expectJson(await call('/lease', { method: 'POST', raw: 'not-json' }), 200) // payload {} → anonymous
      await releaseLease()
      expectJson(await call('/lease'), 404)
      return 'malformed JSON→200 json；GET→404（非 500）'
    },
  ],
  [
    '/release',
    async () => {
      expectJson(await call('/release', { method: 'POST', raw: 'not-json' }), 200)
      expectJson(await call('/release'), 404)
      return 'malformed JSON→200 json；GET→404（非 500）'
    },
  ],
  [
    '/frame',
    async () => {
      expectJson(await call('/frame?x=1'), 410)
      expectJson(await call('/frame', { method: 'POST' }), 410)
      return '任意方法/参数恒 410 application/json（非 500）'
    },
  ],
  [
    '/frame.png',
    async () => {
      expectPng(await call('/frame.png?size=abc&area=zzz'))
      expectJson(await call('/frame.png', { method: 'POST' }), 404)
      return '非法 query→200 image/png（size=NaN 被引擎兜底）；POST→404（非 500）'
    },
  ],
  [
    '/act',
    async () => {
      const bad = await call('/act', { method: 'POST', raw: 'not-json' })
      expectJson(bad, 200)
      expectJson(await call('/act'), 404)
      return 'malformed JSON→200 json；GET→404（非 500）'
    },
  ],
  [
    '/commands',
    async () => {
      expectJson(await call('/commands?x=1'), 200)
      expectJson(await call('/commands', { method: 'POST' }), 404)
      return '非法 query→200；方法不匹配→404（非 500）'
    },
  ],
  [
    '/cmd',
    async () => {
      expectJson(await call('/cmd', { method: 'POST', raw: 'not-json' }), 200)
      expectJson(await call('/cmd'), 404)
      return 'malformed JSON→200 json；GET→404（非 500）'
    },
  ],
  [
    '/loop',
    async () => {
      expectJson(await call('/loop', { method: 'POST', raw: 'not-json' }), 200)
      expectJson(await call('/loop'), 404)
      return 'malformed JSON→200 json；GET→404（非 500）'
    },
  ],
  [
    '/perf',
    async () => {
      expectJson(await call('/perf', { method: 'POST', raw: 'not-json' }), 200)
      expectJson(await call('/perf'), 404)
      return 'malformed JSON→200 json；GET→404（非 500）'
    },
  ],
  [
    '/opt',
    async () => {
      expectJson(await call('/opt', { method: 'POST', raw: 'not-json' }), 200)
      expectJson(await call('/opt'), 404)
      return 'malformed JSON→200 json；GET→404（非 500）'
    },
  ],
  [
    '/view',
    async () => {
      expectPng(await call('/view', { method: 'POST', raw: 'not-json' }))
      expectJson(await call('/view'), 404)
      return 'malformed JSON→200 image/png；GET→404（非 500）'
    },
  ],
  [
    '/headless',
    async () => {
      expectNdjson(await call('/headless', { method: 'POST', raw: 'not-json' }))
      expectJson(await call('/headless'), 404)
      return 'malformed JSON→200 x-ndjson；GET→404（非 500）'
    },
  ],
  [
    '/plan',
    async () => {
      expectJson(await call('/plan', { method: 'POST', raw: 'not-json' }), 200)
      expectJson(await call('/plan'), 404)
      return 'malformed JSON→200 json；GET→404（非 500）'
    },
  ],
  [
    '/worker',
    async () => {
      expectNdjson(await call('/worker', { method: 'POST', raw: 'not-json' }))
      expectJson(await call('/worker'), 404)
      return 'malformed JSON→200 x-ndjson；GET→404（非 500）'
    },
  ],
  [
    '/txn',
    async () => {
      expectNdjson(await call('/txn', { method: 'POST', raw: 'not-json' }))
      expectJson(await call('/txn'), 404)
      return 'malformed JSON→200 x-ndjson；GET→404（非 500）'
    },
  ],
  [
    '/preset',
    async () => {
      expectNdjson(await call('/preset', { method: 'POST', raw: 'not-json' }))
      expectJson(await call('/preset'), 404)
      return 'malformed JSON→200 x-ndjson；GET→404（非 500）'
    },
  ],
  [
    '/job',
    async () => {
      expectJson(await call('/job', { method: 'POST', raw: 'not-json' }), 200)
      expectJson(await call('/job'), 404)
      return 'malformed JSON→200 json；GET→404（非 500）'
    },
  ],
  [
    '/host/scene-items',
    async () => {
      expectJson(await call('/host/scene-items?limit=abc&q='), 200) // limit 回默认 200
      expectJson(await call('/host/scene-items', { method: 'DELETE' }), 404)
      return '非法 limit→200 json；DELETE→404 hostJson（非 500）'
    },
  ],
  [
    '/host/jobs',
    async () => {
      expectJson(await call('/host/jobs?limit=abc'), 200)
      expectJson(await call('/host/jobs', { method: 'PUT' }), 404)
      return '非法 limit→200 json（夹取默认）；PUT→404（非 500）'
    },
  ],
  [
    '/host/jobs/log',
    async () => {
      const j = expectJson(await call('/host/jobs/log?id=&tail=abc'), 200)
      expect(j.ok).toBe(false)
      expect(j.error).toBe('unknown_job')
      expectJson(await call('/host/jobs/log', { method: 'POST' }), 404)
      return '空 id/非法 tail→200 json ok=false unknown_job；POST→404（非 500）'
    },
  ],
  [
    '/host/state',
    async () => {
      expectJson(await call('/host/state'), 200)
      expectJson(await call('/host/state', { method: 'DELETE' }), 404)
      return '正常 GET→200；DELETE→404 hostJson（非 500）'
    },
  ],
  [
    '/host/selection',
    async () => {
      const bad = await call('/host/selection', { method: 'POST', raw: 'not-json' })
      const j = expectJson(bad, 400)
      expect(j.error).toBe('invalid_payload')
      expectJson(await call('/host/selection', { method: 'GET' }), 404) // GET 无此路由 → hostJson 404
      return 'malformed JSON→400 json invalid_payload；GET→404（非 500）'
    },
  ],
  [
    '/host/selection/latest',
    async () => {
      expectJson(await call('/host/selection/latest'), 200)
      expectJson(await call('/host/selection/latest', { method: 'DELETE' }), 404)
      return '正常 GET→200；DELETE→404（非 500）'
    },
  ],
  [
    '/host/selection/consume',
    async () => {
      const bad = await call('/host/selection/consume', { method: 'POST', body: { seq: 'abc' } })
      const j = expectJson(bad, 400)
      expect(j.error).toBe('invalid_seq')
      const malformed = await call('/host/selection/consume', { method: 'POST', raw: 'not-json' })
      expectJson(malformed, 400)
      return '非法 seq→400 json invalid_seq；malformed JSON→400（非 500）'
    },
  ],
]

describe('invalidInput：非法输入与方法不匹配不得产生 500', () => {
  it.each(INVALID)('%s rejects invalid input without 500', async (path, fn) => {
    const evidence = await fn()
    // 额外全局约束：本路由在本用例内至少发过一次请求，且没有任何一次是 500
    const probe = await call(path)
    const status = probe.status
    invalidStatuses.push(status)
    expect(status, path).not.toBe(500)
    record(path, 'invalidInput', 'PASS', evidence + '；补探状态=' + String(status))
  })
})

// ───────────────────────────── error mapping ─────────────────────────────
const ERROR_CASES: Array<[string, () => Promise<string>]> = [
  [
    '/status',
    async () => {
      faults.add('status')
      const j = expectJson(await call('/status'), 200)
      expect(j.ok).toBe(false)
      return 'engine.status 抛错→200 application/json ok=false（路由内 catch）'
    },
  ],
  [
    '/doctor',
    async () => {
      faults.add('doctor')
      const j = expectJson(await call('/doctor'), 200)
      expect(j.ok).toBe(false)
      return 'engine.doctor 抛错→200 application/json ok=false'
    },
  ],
  [
    '/launch',
    async () => {
      faults.add('launchBlender')
      expectJson(await call('/launch', { method: 'POST', body: {} }), 500)
      return 'engine.launchBlender 抛错→500 application/json（router catch）'
    },
  ],
  [
    '/who',
    async () => {
      faults.add('metrics')
      expectJson(await call('/who'), 500)
      return 'engine.metrics 抛错→500 application/json（router catch）'
    },
  ],
  [
    '/frame.png',
    async () => {
      faults.add('frame')
      const j = expectJson(await call('/frame.png'), 502)
      expect(j.ok).toBe(false)
      return 'engine.frame 抛错→502 application/json'
    },
  ],
  [
    '/act',
    async () => {
      faults.add('act')
      const j = expectJson(await call('/act', { method: 'POST', body: { code: 'x=1' } }), 200)
      expect(j.ok).toBe(false)
      return 'engine.act 抛错→200 application/json ok=false'
    },
  ],
  [
    '/commands',
    async () => {
      faults.add('commands')
      const j = expectJson(await call('/commands'), 200)
      expect(j.ok).toBe(false)
      return 'engine.commands 抛错→200 application/json ok=false'
    },
  ],
  [
    '/cmd',
    async () => {
      faults.add('cmd')
      const j = expectJson(await call('/cmd', { method: 'POST', body: { name: 'ping' } }), 200)
      expect(j.ok).toBe(false)
      return 'engine.cmd 抛错→200 application/json ok=false'
    },
  ],
  [
    '/loop',
    async () => {
      faults.add('loopStatus')
      const j = expectJson(await call('/loop', { method: 'POST', body: { op: 'status' } }), 200)
      expect(j.ok).toBe(false)
      return 'engine.loopStatus 抛错→200 application/json ok=false'
    },
  ],
  [
    '/perf',
    async () => {
      faults.add('perf')
      const j = expectJson(await call('/perf', { method: 'POST', body: { op: 'status' } }), 200)
      expect(j.ok).toBe(false)
      return 'engine.perf 抛错→200 application/json ok=false'
    },
  ],
  [
    '/opt',
    async () => {
      faults.add('opt')
      const j = expectJson(await call('/opt', { method: 'POST', body: { op: 'opt_analyze' } }), 200)
      expect(j.ok).toBe(false)
      return 'engine.opt 抛错→200 application/json ok=false'
    },
  ],
  [
    '/view',
    async () => {
      faults.add('view')
      const j = expectJson(await call('/view', { method: 'POST', body: { asJson: true } }), 502)
      expect(j.ok).toBe(false)
      return 'engine.view 抛错→502 application/json'
    },
  ],
  [
    '/headless',
    async () => {
      faults.add('headless')
      const j = expectNdjson(
        await call('/headless', { method: 'POST', body: { script: 'print(1)' } }),
      )
      expect(j.ok).toBe(false)
      return 'engine.headless 抛错→200 x-ndjson ok=false（流式回执尾部）'
    },
  ],
  [
    '/plan',
    async () => {
      faults.add('plan')
      const j = expectJson(await call('/plan', { method: 'POST', body: { op: 'catalog' } }), 200)
      expect(j.ok).toBe(false)
      return 'engine.plan 抛错→200 application/json ok=false'
    },
  ],
  [
    '/worker',
    async () => {
      faults.add('workerStatus')
      const j = expectNdjson(await call('/worker', { method: 'POST', body: { op: 'status' } }))
      expect(j.ok).toBe(false)
      return 'engine.worker.status 抛错→200 x-ndjson ok=false'
    },
  ],
  [
    '/txn',
    async () => {
      faults.add('txn')
      const j = expectNdjson(await call('/txn', { method: 'POST', body: { op: 'list' } }))
      expect(j.ok).toBe(false)
      return 'engine.txn 抛错→200 x-ndjson ok=false'
    },
  ],
  [
    '/preset',
    async () => {
      faults.add('preset')
      const j = expectNdjson(await call('/preset', { method: 'POST', body: { op: 'list' } }))
      expect(j.ok).toBe(false)
      return 'engine.preset 抛错→200 x-ndjson ok=false'
    },
  ],
  [
    '/job',
    async () => {
      faults.add('jobList')
      const j = expectJson(await call('/job', { method: 'POST', body: { op: 'list' } }), 200)
      expect(j.ok).toBe(false)
      return 'engine.job.list 抛错→200 application/json ok=false'
    },
  ],
  [
    '/host/scene-items',
    async () => {
      faults.add('hostSceneItems')
      const j = expectJson(await call('/host/scene-items'), 502)
      expect(j.ok).toBe(false)
      return 'engine.hostSceneItems 抛错→502 application/json'
    },
  ],
  [
    '/host/jobs',
    async () => {
      faults.add('jobList')
      const j = expectJson(await call('/host/jobs'), 200)
      expect(j.ok).toBe(true)
      return 'engine.job.list 抛错→200 application/json ok=true jobs=[]（只读视图降级）'
    },
  ],
  [
    '/host/jobs/log',
    async () => {
      faults.add('jobCollect')
      const j = expectJson(await call('/host/jobs/log?id=job-1'), 200)
      expect(j.ok).toBe(false)
      expect(j.error).toBe('unknown_job')
      return 'engine.job.collect 抛错→200 application/json ok=false unknown_job'
    },
  ],
  [
    '/host/state',
    async () => {
      faults.add('renderFlag')
      const j = expectJson(await call('/host/state'), 200)
      expect(j.ok).toBe(true)
      return 'engine.renderFlag 抛错→200 application/json ok=true（视图块保守降级）'
    },
  ],
]

const ERROR_NA: Array<[string, string]> = [
  ['/health', '不调用 engine（provenance 自身 try/catch）'],
  ['/lease', '纯租约状态机，无 engine 依赖'],
  ['/release', '纯租约状态机，无 engine 依赖'],
  ['/frame', '已移除路由，恒 410'],
  ['/host/selection', '纯内存 selectionStore.put，无 engine 依赖'],
  ['/host/selection/latest', '纯内存 selectionStore.snapshot，无 engine 依赖'],
  ['/host/selection/consume', '纯内存 selectionStore.consume，无 engine 依赖'],
]

describe('errorMapping：下游失败的错误码与 content-type', () => {
  it.each(ERROR_CASES)('%s maps engine failure', async (path, fn) => {
    const evidence = await fn()
    record(path, 'errorMapping', 'PASS', evidence)
  })
  it.each(ERROR_NA)('%s has no engine failure path', (path, reason) => {
    record(path, 'errorMapping', 'N/A', '无 engine 调用点，错误路径不适用', reason)
  })
})

// ───────────────────────────── lease ─────────────────────────────
type LeaseSpec =
  | { kind: 'gated'; write: Record<string, unknown>; read?: Record<string, unknown> }
  | { kind: 'exempt'; method: 'GET' | 'POST'; body?: Record<string, unknown>; expected: number }
  | { kind: 'leaseEndpoint' }

const LEASE_SPECS: Record<string, LeaseSpec> = {
  '/health': { kind: 'exempt', method: 'GET', expected: 200 },
  '/status': { kind: 'exempt', method: 'GET', expected: 200 },
  '/doctor': { kind: 'exempt', method: 'GET', expected: 200 },
  '/launch': { kind: 'exempt', method: 'POST', body: {}, expected: 200 },
  '/who': { kind: 'exempt', method: 'GET', expected: 200 },
  '/lease': { kind: 'leaseEndpoint' },
  '/release': { kind: 'leaseEndpoint' },
  '/frame': { kind: 'exempt', method: 'GET', expected: 410 },
  '/frame.png': { kind: 'exempt', method: 'GET', expected: 200 },
  '/act': { kind: 'gated', write: { code: 'x = 1' } },
  '/commands': { kind: 'exempt', method: 'GET', expected: 200 },
  '/cmd': { kind: 'gated', write: { name: 'execute_code' } },
  '/loop': { kind: 'gated', write: { op: 'start' }, read: { op: 'status' } },
  '/perf': { kind: 'gated', write: { op: 'bench' }, read: { op: 'status' } },
  '/opt': { kind: 'gated', write: { op: 'join' }, read: { op: 'opt_analyze' } },
  '/view': { kind: 'exempt', method: 'POST', body: { asJson: true }, expected: 200 },
  '/headless': { kind: 'gated', write: { script: 'print(1)' } },
  '/plan': { kind: 'gated', write: { op: 'dispatch' }, read: { op: 'catalog' } },
  '/worker': { kind: 'gated', write: { op: 'exec', code: 'x = 1' }, read: { op: 'status' } },
  '/txn': { kind: 'gated', write: { op: 'snapshot' }, read: { op: 'list' } },
  '/preset': { kind: 'gated', write: { op: 'save' }, read: { op: 'list' } },
  '/job': { kind: 'gated', write: { op: 'start' }, read: { op: 'list' } },
  '/host/scene-items': { kind: 'exempt', method: 'GET', expected: 200 },
  '/host/jobs': { kind: 'exempt', method: 'GET', expected: 200 },
  '/host/jobs/log': { kind: 'exempt', method: 'GET', expected: 200 },
  '/host/state': { kind: 'exempt', method: 'GET', expected: 200 },
  '/host/selection': {
    kind: 'exempt',
    method: 'POST',
    body: { selection: { names: ['Cube'], count: 1 } },
    expected: 200,
  },
  '/host/selection/latest': { kind: 'exempt', method: 'GET', expected: 200 },
  '/host/selection/consume': { kind: 'exempt', method: 'POST', expected: 200 },
}

async function acquire(holder: string): Promise<void> {
  expectJson(await call('/lease', { method: 'POST', body: { holder } }), 200)
}

const LEASE_ROUTES: Array<[string, LeaseSpec]> = ROUTE_PATHS.map((p) => [p, LEASE_SPECS[p]])

describe('lease：他人持租约时的写保护/只读放行', () => {
  it.each(LEASE_ROUTES)('%s lease semantics', async (path, spec) => {
    const holderA = 'matrix-A'
    try {
      await acquire(holderA)
      if (spec.kind === 'leaseEndpoint') {
        if (path === '/lease') {
          const denied = expectJson(
            await call('/lease', { method: 'POST', body: { holder: 'matrix-B' } }),
            409,
          )
          expect(denied.error).toBe('leased')
          expect(denied.holder).toBe(holderA)
          record(
            path,
            'lease',
            'PASS',
            '他人持租约时再次 acquire→409 application/json error=leased',
          )
        } else {
          const foreign = expectJson(
            await call('/release', { method: 'POST', body: { holder: 'matrix-B' } }),
            200,
          )
          expect(foreign.released).toBeNull()
          const mine = expectJson(
            await call('/release', { method: 'POST', body: { holder: holderA } }),
            200,
          )
          expect(mine.released).toBe(holderA)
          expect(mine.lease.active).toBe(false)
          record(
            path,
            'lease',
            'PASS',
            '非持有者 release→released=null；持有者 release→released=matrix-A，租约清空',
          )
        }
        return
      }
      if (spec.kind === 'exempt') {
        let body = { ...(spec.body || {}) }
        if (path === '/host/selection/consume') {
          const put = expectJson(
            await call('/host/selection', {
              method: 'POST',
              body: { selection: { names: ['Cube'], count: 1 } },
            }),
            200,
          )
          body = { seq: put.seq }
        }
        const r = await call(path, {
          method: spec.method,
          body: spec.method === 'POST' ? body : undefined,
        })
        expect(r.status, path).toBe(spec.expected)
        expect(r.status).not.toBe(409)
        record(
          path,
          'lease',
          'PASS',
          '租约被他人持有仍放行→' +
            String(r.status) +
            '（' +
            (r.headers.get('content-type') || '') +
            '）',
        )
        return
      }
      // gated：外部 holder 的写请求必须 409
      const denied = expectJson(
        await call(path, { method: 'POST', body: { ...spec.write, holder: 'matrix-B' } }),
        409,
      )
      expect(denied.ok).toBe(false)
      expect(denied.error).toBe('leased')
      expect(denied.holder).toBe(holderA)
      let evidence = '他人 holder 写操作→409 application/json error=leased holder=matrix-A'
      if (spec.read) {
        const readable = await call(path, {
          method: 'POST',
          body: { ...spec.read, holder: 'matrix-B' },
        })
        expect(readable.status, path).toBe(200)
        const parsed = parseAny(readable)
        expect(parsed.ok).toBe(true)
        evidence += '；只读 op（' + JSON.stringify(spec.read) + '）→200 ok=true'
      }
      record(path, 'lease', 'PASS', evidence)
    } finally {
      await releaseLease()
    }
  })
})

// ───────────────────────────── CORS ─────────────────────────────
const HOST_ROUTES = [
  '/host/scene-items',
  '/host/jobs',
  '/host/jobs/log',
  '/host/state',
  '/host/selection',
  '/host/selection/latest',
  '/host/selection/consume',
]
const NON_HOST_ROUTES = ROUTE_PATHS.filter((p) => !p.startsWith('/host/'))

describe('cors：/host Origin 白名单与预检', () => {
  it.each(HOST_ROUTES)('%s host CORS', async (path) => {
    const allowed = 'dsh-app://app'
    const preflight = await call(path, { method: 'OPTIONS', origin: allowed })
    expect(preflight.status).toBe(204)
    expect(preflight.headers.get('access-control-allow-origin')).toBe(allowed)
    expect(preflight.headers.get('access-control-allow-methods')).toContain('GET')
    expect(preflight.headers.get('vary')).toBe('Origin')

    const localOrigin = 'http://127.0.0.1:3456'
    const allowedGet = await call(path, { origin: localOrigin })
    expect(allowedGet.headers.get('access-control-allow-origin')).toBe(localOrigin)

    const evil = await call(path, { origin: 'https://evil.example' })
    expect(evil.headers.get('access-control-allow-origin')).toBeNull()
    record(
      path,
      'cors',
      'PASS',
      'OPTIONS→204 + ACAO 白名单；GET 本机 origin 回显；evil origin 无 ACAO',
    )
  })

  it.each(NON_HOST_ROUTES)('%s has no CORS by contract', async (path) => {
    const r = await call(path, { origin: 'dsh-app://app' })
    expect(r.headers.get('access-control-allow-origin')).toBeNull()
    record(
      path,
      'cors',
      'N/A',
      '已断言响应无 access-control-allow-origin（非 500：' + String(r.status) + '）',
      'CORS 头只属于 /host/* 宿主集成契约（docs/宿主集成-接口契约-v2.3增补.md）：非 host 路由按设计不设 CORS',
    )
  })
})
