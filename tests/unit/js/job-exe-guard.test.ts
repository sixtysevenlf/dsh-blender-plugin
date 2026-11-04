/**
 * 回归（PR #75 review）：job 通道的 blender.exe 预检与错误码透出。
 *
 * 旧行为：runtime/execution/jobs/start/spawn.ts 直接 spawn(BLENDER_EXE as string)，
 * blenderExe=null 时抛 Node 的 TypeError；/job 路由 catch 不带 code，tools/job 一律映射 backend_error。
 * 契约：路由透出 code='blender_exe_missing'，blender_rt_job 工具映射成自己的错误码 + 配置指引。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'

const h = vi.hoisted(() => ({
  backendResponse: null as Record<string, unknown> | null,
}))

vi.mock('../../../src/host/backend/index.js', () => ({
  ensureBackend: async () => true,
  base: (port: number) => 'http://127.0.0.1:' + String(port),
}))

vi.mock('../../../src/host/http.js', () => ({
  backendPost: async () => h.backendResponse,
  leasedText: () => undefined,
  splitArgs: (s: string) => s,
}))

vi.mock('../../../src/host/runtime-bridge.js', () => ({
  detectFileAlias: () => [],
}))

import { createJobRoutes } from '../../../runtime/http/routes/job.js'
import { executeJob } from '../../../src/tools/job/execute.js'

function routeHarness(jobStart: () => unknown) {
  const captured: { value: any } = { value: undefined }
  const deps: any = {
    engine: { job: { start: jobStart } },
    isReadOnly: () => true,
    leaseGate: () => null,
    readBody: async () => JSON.stringify({ op: 'start' }),
    json: (_res: any, _status: number, body: unknown) => {
      captured.value = body
    },
    stats: {},
    streamJson: async (_res: any, producer: () => Promise<unknown>) => await producer(),
    pushRtt: () => {},
  }
  return { route: createJobRoutes(deps), captured }
}

afterEach(() => {
  h.backendResponse = null
})

describe('/job route error envelope', () => {
  it('passes the blender_exe_missing code through', async () => {
    const err: any = new Error('没找到 Blender 可执行文件（blenderExe 为空；探测来源: not-found）')
    err.code = 'blender_exe_missing'
    err.hint = '设置 DSH_BLENDER_EXE'
    const { route, captured } = routeHarness(() => {
      throw err
    })
    await route.job({} as any, {} as any, new URL('http://127.0.0.1:9877/job'), '/job')
    expect(captured.value).toMatchObject({
      ok: false,
      op: 'start',
      code: 'blender_exe_missing',
    })
    expect(String(captured.value.error)).toContain('没找到 Blender')
  })
})

describe('executeJob failure mapping', () => {
  it('maps blender_exe_missing to its own tool code with configuration guidance', async () => {
    h.backendResponse = {
      ok: false,
      op: 'start',
      error: '没找到 Blender 可执行文件（blenderExe 为空；探测来源: not-found）',
      code: 'blender_exe_missing',
    }
    const out: any = await executeJob({ op: 'start', script: 'print(1)' }, 9877)
    expect(out.envelope.error.code).toBe('blender_exe_missing')
    expect(String(out.envelope.error.message)).toContain('没找到 Blender')
    expect(String(out.envelope.error.next_action)).toContain('DSH_BLENDER_EXE')
    expect(String(out.text)).toContain('code: blender_exe_missing')
    expect(String(out.text)).not.toContain('The "file" argument')
  })
})
