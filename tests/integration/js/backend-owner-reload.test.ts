/**
 * Reload reuse: a new plugin load must adopt the Gateway its previous load
 * started. The owner hostId is stable across processes, so the second load is
 * not mistaken for another DSH host (which would fail closed with GATEWAY_IN_USE).
 */
import { afterAll, describe, expect, it } from 'vitest'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { ensureBackendDetailed, getLastBackendFailure } from '../../../src/host/backend/index.js'
import { hostIdentity } from '../../../src/host/backend/host-id.js'
import { killChildHandle, spawnChild } from '../../../src/host/backend/spawn.js'
import { freePort } from '../../helpers/backend'

const workDir = fs.mkdtempSync(path.join(os.tmpdir(), 'dsh-owner-reload-'))
const previousWorkDir = process.env.DSH_BLENDER_WORKDIR

async function health(port: number): Promise<Record<string, unknown> | null> {
  try {
    const response = await fetch('http://127.0.0.1:' + String(port) + '/health')
    return response.ok ? ((await response.json()) as Record<string, unknown>) : null
  } catch {
    return null
  }
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

afterAll(async () => {
  killChildHandle()
  if (previousWorkDir === undefined) delete process.env.DSH_BLENDER_WORKDIR
  else process.env.DSH_BLENDER_WORKDIR = previousWorkDir
  try {
    fs.rmSync(workDir, { recursive: true, force: true })
  } catch {
    /* best effort */
  }
})

describe('Gateway owner identity across plugin reloads', () => {
  it('reuses the Gateway started by a previous plugin load instead of failing closed', async () => {
    process.env.DSH_BLENDER_WORKDIR = workDir
    const port = await freePort()
    spawnChild(port)
    const up = await waitUntil(() => health(port), 15000)
    expect(up?.ok).toBe(true)
    const gateway = up?.gateway as Record<string, unknown>
    expect(gateway.hostId).toBe(hostIdentity())

    // Two ensureBackend calls model two plugin loads in one DSH host process.
    expect(await ensureBackendDetailed(port)).toEqual({ ok: true, adopted: true })
    expect(getLastBackendFailure()).toBeNull()
    expect(await ensureBackendDetailed(port)).toEqual({ ok: true, adopted: true })

    killChildHandle()
    expect(await waitUntil(async () => ((await health(port)) === null ? true : null), 10000)).toBe(
      true,
    )
  }, 30000)
})
