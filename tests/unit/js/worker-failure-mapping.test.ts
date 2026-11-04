import { afterEach, describe, expect, it, vi } from 'vitest'

// P0-1 (transport side): /worker must pass the structured error code through so
// the host tool layer can stop wrapping "Blender executable missing" in the
// generic backend_error code.

const h = vi.hoisted(() => ({
  backendResponse: null as Record<string, unknown> | null,
}))

vi.mock('../../../src/host/backend/index.js', () => ({
  ensureBackend: async () => true,
  base: (port: number) => 'http://127.0.0.1:' + String(port),
  withTimeout: vi.fn(),
}))

vi.mock('../../../src/host/http.js', () => ({
  backendPost: async () => h.backendResponse,
  leasedText: () => undefined,
  numTriple: () => undefined,
}))

import { createWorkerRoutes } from '../../../runtime/http/routes/worker.js'
import { executeWorker } from '../../../src/tools/worker/execute.js'

function routeHarness(workerStart: () => Promise<unknown>) {
  const captured: { value: any } = { value: undefined }
  const deps: any = {
    engine: { worker: { start: workerStart } },
    isReadOnly: () => true,
    leaseGate: () => null,
    readBody: async () => JSON.stringify({ op: 'start' }),
    json: () => {},
    stats: {},
    streamJson: async (_res: any, producer: () => Promise<unknown>) => {
      captured.value = await producer()
      return captured.value
    },
  }
  return { route: createWorkerRoutes(deps), captured }
}

afterEach(() => {
  h.backendResponse = null
})

describe('/worker route error envelope', () => {
  it('passes error, hint and code through when the engine throws a coded error', async () => {
    const err: any = new Error('没找到 Blender 可执行文件（blenderExe 为空）')
    err.code = 'blender_exe_missing'
    err.hint = '设置 DSH_BLENDER_EXE'
    const { route, captured } = routeHarness(async () => {
      throw err
    })
    await route.worker({} as any, {} as any, new URL('http://127.0.0.1:9877/worker'), '/worker')
    expect(captured.value).toMatchObject({
      ok: false,
      op: 'start',
      error: '没找到 Blender 可执行文件（blenderExe 为空）',
      hint: '设置 DSH_BLENDER_EXE',
      code: 'blender_exe_missing',
    })
  })

  it('keeps the historical error/hint shape and reports code:null for uncoded failures', async () => {
    const { route, captured } = routeHarness(async () => {
      throw new Error('worker 未就绪')
    })
    await route.worker({} as any, {} as any, new URL('http://127.0.0.1:9877/worker'), '/worker')
    expect(captured.value).toMatchObject({
      ok: false,
      op: 'start',
      error: 'worker 未就绪',
      hint: null,
      code: null,
    })
  })
})

describe('executeWorker failure mapping', () => {
  it('maps blender_exe_missing to its own tool code instead of backend_error', async () => {
    h.backendResponse = {
      ok: false,
      error: '没找到 Blender 可执行文件（blenderExe 为空；探测来源: not-found）',
      hint: '设置 DSH_BLENDER_EXE 或 dsh-blender.config.json 的 blenderExe',
      code: 'blender_exe_missing',
    }
    const out: any = await executeWorker({ op: 'start' }, {} as any, 9877)
    expect(out.envelope.error.code).toBe('blender_exe_missing')
    expect(out.envelope.error.message).toContain('没找到 Blender')
    expect(out.envelope.error.message).toContain('DSH_BLENDER_EXE')
    expect(out.envelope.error.next_action).toContain('DSH_BLENDER_EXE')
    expect(out.text).toContain('code: blender_exe_missing')
    expect(out.text).not.toContain('The "file" argument')
  })

  it('keeps backend_error for failures without the structured code', async () => {
    h.backendResponse = {
      ok: false,
      error: 'worker 未就绪：{"timedOut":true}',
      hint: 'worker stderr 尾部：boom',
    }
    const out: any = await executeWorker({ op: 'start' }, {} as any, 9877)
    expect(out.envelope.error.code).toBe('backend_error')
    expect(out.envelope.error.next_action).toContain('/worker')
  })
})
