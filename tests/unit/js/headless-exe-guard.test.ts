import { afterEach, describe, expect, it, vi } from 'vitest'

// （PR review）：runtime /headless 已经会回 code=blender_exe_missing，
// 工具层此前无条件压成 backend_error，导致 docs 承诺的「headless 报 blender_exe_missing」是假的。

const h = vi.hoisted(() => ({
  backendResponse: null as Record<string, unknown> | null,
}))

vi.mock('../../../src/host/backend/index.js', () => ({ ensureBackend: async () => true }))

vi.mock('../../../src/host/http.js', () => ({
  backendPost: async () => h.backendResponse,
  leasedText: () => undefined,
  splitArgs: (s: string) => String(s).split(/\s+/),
}))

vi.mock('../../../src/host/runtime-bridge.js', () => ({ detectFileAlias: () => [] }))

const { executeHeadless } = await import('../../../src/tools/headless/execute.js')

afterEach(() => {
  h.backendResponse = null
})

describe('blender_rt_headless failure mapping', () => {
  it('passes blender_exe_missing through with an actionable hint', async () => {
    h.backendResponse = {
      ok: false,
      error: '起不来 Blender 进程（null）：spawn null',
      hint: '自动探测没找到 blender 可执行文件。三种配置方式（任选一种）：① DSH_BLENDER_EXE ...',
      code: 'blender_exe_missing',
    }

    const out: any = await executeHeadless({ script: 'print(1)' }, {} as any, 9877)

    expect(out.envelope.error.code).toBe('blender_exe_missing')
    expect(out.envelope.error.message).toContain('起不来 Blender')
    expect(out.envelope.error.next_action).toContain('DSH_BLENDER_EXE')
    expect(out.text).toContain('code: blender_exe_missing')
  })

  it('still reports backend_error when the runtime did not send a structured code', async () => {
    h.backendResponse = { ok: false, error: '脚本崩了', hint: '看日志' }

    const out: any = await executeHeadless({ script: 'print(1)' }, {} as any, 9877)

    expect(out.envelope.error.code).toBe('backend_error')
    expect(out.envelope.error.message).toContain('脚本崩了')
  })
})
