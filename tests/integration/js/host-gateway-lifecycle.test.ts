/**
 * 2.10: the Gateway is a host-owned child. Host apply auto-starts it and host
 * dispose stops it; no detached Gateway survives the plugin.
 */
import { afterEach, describe, expect, it } from 'vitest'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import * as plugin from '../../../src/index'
import { freePort } from '../../helpers/backend'
import { createFakeContext } from '../../helpers/fake-context'

const closers: Array<() => void> = []

afterEach(() => {
  while (closers.length) {
    try {
      closers.pop()!()
    } catch {
      /* best effort */
    }
  }
})

async function waitForExit(pid: number, timeoutMs: number): Promise<boolean> {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    try {
      process.kill(pid, 0)
    } catch {
      return true
    }
    await new Promise((resolve) => setTimeout(resolve, 100))
  }
  return false
}

async function waitUntil<T>(fn: () => Promise<T | null>, timeoutMs: number): Promise<T | null> {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    const value = await fn().catch(() => null)
    if (value) return value
    await new Promise((resolve) => setTimeout(resolve, 150))
  }
  return null
}

describe('host-owned Gateway lifecycle', () => {
  it('starts on apply and leaves no Gateway listener behind after dispose', async () => {
    const workDir = fs.mkdtempSync(path.join(os.tmpdir(), 'dsh-host-gw-'))
    const previous = process.env.DSH_BLENDER_WORKDIR
    const previousNoSpawn = process.env.DSH_BLENDER_TEST_NO_SPAWN
    process.env.DSH_BLENDER_WORKDIR = workDir
    delete process.env.DSH_BLENDER_TEST_NO_SPAWN
    const port = await freePort()
    const fake = createFakeContext()
    try {
      plugin.apply(fake.ctx, { autoStart: true, tui: false, port })
      const health = await waitUntil(async () => {
        const response = await fetch('http://127.0.0.1:' + String(port) + '/health')
        return response.ok ? ((await response.json()) as any) : null
      }, 15000)
      expect(health?.ok).toBe(true)
      expect(health.gateway.pid).not.toBe(process.pid)
      expect(health.gateway.port).toBe(port)

      fake.dispose()
      const closed = await waitUntil(async () => {
        try {
          await fetch('http://127.0.0.1:' + String(port) + '/health')
          return null
        } catch {
          return true
        }
      }, 8000)
      expect(closed).toBe(true)
      expect(await waitForExit(health.gateway.pid, 10000)).toBe(true)

      const reloaded = createFakeContext()
      try {
        plugin.apply(reloaded.ctx, { autoStart: true, tui: false, port })
        const secondHealth = await waitUntil(async () => {
          const response = await fetch('http://127.0.0.1:' + String(port) + '/health')
          return response.ok ? ((await response.json()) as any) : null
        }, 15000)
        expect(secondHealth?.ok).toBe(true)
        expect(secondHealth.gateway.pid).not.toBe(health.gateway.pid)
      } finally {
        reloaded.dispose()
      }
    } finally {
      try {
        fake.dispose()
      } catch {
        /* already disposed */
      }
      if (previous === undefined) delete process.env.DSH_BLENDER_WORKDIR
      else process.env.DSH_BLENDER_WORKDIR = previous
      if (previousNoSpawn === undefined) delete process.env.DSH_BLENDER_TEST_NO_SPAWN
      else process.env.DSH_BLENDER_TEST_NO_SPAWN = previousNoSpawn
      try {
        fs.rmSync(workDir, { recursive: true, force: true })
      } catch {
        /* ignore */
      }
    }
    closers.length = 0
  }, 30000)
})
