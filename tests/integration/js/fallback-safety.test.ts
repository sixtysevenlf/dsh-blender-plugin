/**
 * 3.8: safe fallbacks. No provider / no addon / no executable must say the
 * capability is unavailable instead of silently launching a visible GUI,
 * fabricating output, or falling back to the retired inbound transport.
 */
import { afterEach, describe, expect, it } from 'vitest'
import os from 'node:os'
import path from 'node:path'
import { startBackend } from '../../helpers/backend'

const closers: Array<() => Promise<void>> = []
afterEach(async () => {
  while (closers.length) {
    const close = closers.pop()!
    try {
      await close()
    } catch {
      /* best effort teardown */
    }
  }
})

const caller = {
  'x-dsh-host-id': 'host-fallback',
  'x-dsh-session-id': 'dsh-fallback',
  'x-dsh-agent-id': 'agent-fallback',
  'x-dsh-agent-role': 'main',
}

function expectSafeFallback(json: Record<string, unknown>): void {
  const fallback = json.fallback as Record<string, unknown>
  expect(json.stage).toBeTruthy()
  expect(json.root_cause).toBeTruthy()
  expect(json.fix).toBeTruthy()
  expect(fallback).toBeTypeOf('object')
  expect(fallback.kind).toBe('unavailable')
  expect(String(fallback.summary)).toBeTruthy()
  // fallback 本身不得回退到已退役的入站拓扑或伪造成功（config 段仍会展示 legacy 配置键，不在本断言范围）。
  const fallbackText = String(fallback.summary) + ' ' + String(json.fix)
  expect(fallbackText).not.toMatch(/legacy listener|旧拓扑|伪造成功|fake success|回退.*9876/i)
}

describe('safe fallback for unavailable capabilities', () => {
  it('reports a session-stage-free config failure when no display provider can start virtual-gui', async () => {
    const backend = await startBackend()
    closers.push(() => backend.stop())

    const response = await backend.post('/lease', { action: 'start', kind: 'virtual-gui' }, caller)
    expect(response.status).toBe(503)
    expect(response.json.ok).toBe(false)
    expect(response.json.code).toBe('feature_disabled')
    expect(response.json.stage).toBe('config')
    expect(response.json.session).toBeUndefined()
    expectSafeFallback(response.json as Record<string, unknown>)
  })

  it('returns a bounded session decision when no GUI session is ready', async () => {
    const backend = await startBackend()
    closers.push(() => backend.stop())

    const t0 = Date.now()
    const response = await backend.post('/lease', { action: 'attach' }, caller)
    const elapsedMs = Date.now() - t0
    expect(response.status).toBe(503)
    expect(response.json.code).toBe('session_unavailable')
    expect(response.json.stage).toBe('session')
    expect(elapsedMs).toBeLessThan(100)
    expectSafeFallback(response.json as Record<string, unknown>)
  })

  it('reports the addon as unavailable when only the Gateway is running', async () => {
    const backend = await startBackend()
    closers.push(() => backend.stop())

    const response = await backend.get('/doctor')
    expect(response.status).toBe(200)
    expect(response.json.ok).toBe(false)
    expect(response.json.stage).toBe('transport')
    expectSafeFallback(response.json as Record<string, unknown>)
  })

  it('reports commands disconnected as a bounded transport diagnosis, not a status loop', async () => {
    const backend = await startBackend()
    closers.push(() => backend.stop())

    const t0 = Date.now()
    const response = await backend.get('/commands')
    const elapsedMs = Date.now() - t0
    expect(response.status).toBe(200)
    expect(response.json.ok).toBe(false)
    const diagnosis = response.json.diagnosis as Record<string, unknown>
    expect(diagnosis.kind).toBe('addon-unreachable')
    expect(diagnosis.stage).toBe('transport')
    expect(elapsedMs).toBeLessThan(3000)
    expect(response.json.stage).toBe('transport')
    expectSafeFallback(response.json as Record<string, unknown>)
  })

  it('reports blender_exe_missing as a config failure with an unavailable fallback', async () => {
    const missing = path.join(
      os.tmpdir(),
      'dsh-missing-blender-fallback-' + String(process.pid) + '.exe',
    )
    const backend = await startBackend({ env: { DSH_BLENDER_EXE: missing } })
    closers.push(() => backend.stop())

    const response = await backend.post('/worker', { op: 'start', isolated: true })
    expect(response.status).toBe(200)
    expect(response.json.ok).toBe(false)
    expect(response.json.code).toBe('blender_exe_missing')
    expect(response.json.stage).toBe('config')
    expect(String(response.json.root_cause)).toContain('worker 起不来')
    expectSafeFallback(response.json as Record<string, unknown>)
  })
})
