/**
 * P0-3 回归：status/doctor 必须说真话 + 有界超时。
 *
 * 现场（会话日志 seq 52-56）：op=start 只探 /health 就宣称「后端已在运行」，
 * op=doctor 又一律说「后端可能没起」，formatStatusError 把所有异常压成
 * code=backend_unreachable —— agent 因此判断通道不可用。
 *
 * 本文件把 host 侧的四条诚实性契约钉死：
 *   1) op=start：/health 通 ≠ addon/bpy 可用 → 再用有界 /status 判定；
 *   2) op=doctor catch：先探 /health 才能说「后端没起」；
 *   3) formatStatusError：超时（backend_timeout）≠ 连接拒绝（backend_unreachable）；
 *   4) formatStatus：addon 超时 → code=addon_unresponsive，next_action 指向 Blender addon。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  backendJson: vi.fn(),
  backendPost: vi.fn(),
  probeHttp: vi.fn(),
  waitHttp: vi.fn(),
  base: vi.fn((port: number) => 'http://127.0.0.1:' + String(port)),
  startBackend: vi.fn(async () => 'spawned'),
  setPaused: vi.fn(),
  killChildHandle: vi.fn(() => false),
  killBackendByCmd: vi.fn(() => [] as number[]),
  sleep: vi.fn(async () => {}),
  childAlive: vi.fn(() => true),
  getChild: vi.fn((): { pid: number } | null => null),
  isPaused: vi.fn(() => false),
}))

vi.mock('../../../src/host/http.js', () => ({
  backendJson: mocks.backendJson,
  backendPost: mocks.backendPost,
}))
vi.mock('../../../src/host/runtime-bridge.js', () => ({
  probeHttp: mocks.probeHttp,
  waitHttp: mocks.waitHttp,
}))
vi.mock('../../../src/host/backend/index.js', () => ({
  base: mocks.base,
  startBackend: mocks.startBackend,
  setPaused: mocks.setPaused,
  killChildHandle: mocks.killChildHandle,
  killBackendByCmd: mocks.killBackendByCmd,
  sleep: mocks.sleep,
  childAlive: mocks.childAlive,
  getChild: mocks.getChild,
  isPaused: mocks.isPaused,
}))
vi.mock('../../../src/tui/index.js', () => ({
  tuiHostSummary: () => 'tui: test',
}))

const { executeViewport } = await import('../../../src/tools/viewport/execute.js')
const { formatStatus, formatStatusError } = await import('../../../src/tools/viewport/presenter.js')

const PORT = 9877
const HEALTH = 'http://127.0.0.1:9877/health'

const HEALTHY_STATUS = {
  ok: true,
  region: { window: [1920, 1080], views: [{ region: [0, 0, 1920, 1080], shading: 'SOLID' }] },
  addonError: null,
}

beforeEach(() => {
  vi.clearAllMocks()
  mocks.base.mockImplementation((port: number) => 'http://127.0.0.1:' + String(port))
  mocks.probeHttp.mockResolvedValue(false)
  mocks.waitHttp.mockResolvedValue(false)
  mocks.startBackend.mockResolvedValue('spawned')
})

describe('op=start: /health 通不等于通道就绪', () => {
  it('keeps the historical wording when the addon/bpy round trip also works', async () => {
    mocks.probeHttp.mockResolvedValue(true)
    mocks.backendJson.mockResolvedValue(HEALTHY_STATUS)

    const out = await executeViewport({ op: 'start' }, PORT)

    expect(out).toContain('后端已在运行')
    expect(out).toContain('addon/bpy 可用')
    expect(mocks.startBackend).not.toHaveBeenCalled()
  })

  it('bounds the /status probe so start cannot outlive the host budget', async () => {
    mocks.probeHttp.mockResolvedValue(true)
    mocks.backendJson.mockResolvedValue(HEALTHY_STATUS)

    await executeViewport({ op: 'start' }, PORT)

    const call = mocks.backendJson.mock.calls[0]
    expect(call[1]).toBe('/status')
    const timeoutMs = Number(call[2])
    expect(timeoutMs).toBeGreaterThan(0)
    expect(timeoutMs).toBeLessThanOrEqual(6000)
  })

  it('separates "backend up, addon/bpy not responding" from "channel ready"', async () => {
    mocks.probeHttp.mockResolvedValue(true)
    mocks.backendJson.mockResolvedValue({
      ok: true,
      region: null,
      addonError: 'addon timeout: execute_code',
    })

    const out = await executeViewport({ op: 'start' }, PORT)

    expect(out).toContain('后端在（9877')
    expect(out).toContain('addon/bpy 未响应')
    expect(out).toContain('addon timeout: execute_code')
    expect(out).toContain('DSH Blender Bridge')
    expect(out).not.toContain('addon/bpy 可用')
  })

  it('does not claim a ready channel when the bounded /status probe times out', async () => {
    mocks.probeHttp.mockResolvedValue(true)
    mocks.backendJson.mockRejectedValue(new Error('This operation was aborted'))

    const out = await executeViewport({ op: 'start' }, PORT)

    expect(out).toContain('addon/bpy 未响应')
    expect(out).toContain('This operation was aborted')
    expect(out).not.toContain('addon/bpy 可用')
  })
})

describe('op=doctor catch: /health decides who is at fault', () => {
  it('says the backend is up and the addon/bpy round trip failed when /health answers', async () => {
    mocks.backendJson.mockRejectedValue(new Error('This operation was aborted'))
    mocks.probeHttp.mockResolvedValue(true)

    const out = await executeViewport({ op: 'doctor' }, PORT)

    expect(out).toContain('后端在（9877')
    expect(out).toContain('问题在 addon 或 bpy 往返')
    expect(out).toContain('This operation was aborted')
    expect(out).not.toContain('后端没起')
    const [url, timeoutMs] = mocks.probeHttp.mock.calls[0]
    expect(String(url)).toBe(HEALTH)
    expect(Number(timeoutMs)).toBeLessThanOrEqual(1000)
  })

  it('only blames a missing backend when /health is also down', async () => {
    mocks.backendJson.mockRejectedValue(new Error('fetch failed'))
    mocks.probeHttp.mockResolvedValue(false)

    const out = await executeViewport({ op: 'doctor' }, PORT)

    expect(out).toContain('后端没起')
    expect(out).toContain('op=start')
  })
})

describe('formatStatusError: timeout and refused connections are different verdicts', () => {
  it('classifies an aborted/timed-out /status as backend_timeout, not backend_unreachable', () => {
    const parsed = JSON.parse(formatStatusError(PORT, new Error('This operation was aborted')))

    expect(parsed.running).toBe(false)
    expect(parsed.url).toBe('http://127.0.0.1:9877/')
    expect(parsed.error).toContain('This operation was aborted')
    expect(parsed.code).toBe('backend_timeout')
    expect(parsed.next_action).toContain('op=doctor')
    expect(parsed.hint).toBeTruthy()
  })

  it('keeps backend_unreachable + op=start for a refused connection', () => {
    const refused = new TypeError('fetch failed', {
      cause: new Error('connect ECONNREFUSED 127.0.0.1:9877'),
    })
    const parsed = JSON.parse(formatStatusError(PORT, refused))

    expect(parsed.running).toBe(false)
    expect(parsed.url).toBe('http://127.0.0.1:9877/')
    expect(parsed.error).toContain('ECONNREFUSED')
    expect(parsed.code).toBe('backend_unreachable')
    expect(parsed.next_action).toContain('op=start')
    expect(parsed.hint).toBeTruthy()
  })
})

describe('formatStatus: addon errors are surfaced, never folded into op=start', () => {
  it('reports code=addon_unresponsive when addonError is a timeout', () => {
    const parsed = JSON.parse(
      formatStatus(PORT, {
        ok: true,
        region: null,
        addonError: 'addon timeout: execute_code',
        stats: {},
        ledger: { writable: true, workDir: { wsl: '/tmp' } },
      }),
    )

    expect(parsed.running).toBe(true)
    expect(parsed.code).toBe('addon_unresponsive')
    expect(parsed.addonError).toBe('addon timeout: execute_code')
    expect(parsed.viewportRegion).toBeNull()
    expect(parsed.next_action).toContain('DSH Blender Bridge')
    expect(parsed.next_action).not.toContain('op=start')
  })

  it('labels a non-timeout addon error honestly instead of hiding it', () => {
    const parsed = JSON.parse(
      formatStatus(PORT, {
        ok: true,
        region: null,
        addonError: 'addon error: AI 操作已暂停',
        stats: {},
        ledger: { writable: true, workDir: { wsl: '/tmp' } },
      }),
    )

    expect(parsed.running).toBe(true)
    expect(parsed.code).toBe('addon_error')
    expect(parsed.addonError).toBe('addon error: AI 操作已暂停')
    expect(parsed.next_action).toContain('DSH Blender Bridge')
  })

  it('keeps the healthy payload shape when the addon answered', () => {
    const parsed = JSON.parse(formatStatus(PORT, HEALTHY_STATUS))

    expect(parsed.running).toBe(true)
    expect(parsed.addonError).toBeNull()
    expect(parsed.code).toBeUndefined()
    expect(parsed.viewportRegion).toEqual([0, 0, 1920, 1080])
    expect(parsed.shading).toBe('SOLID')
  })
})
