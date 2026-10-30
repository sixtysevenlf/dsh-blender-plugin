import { afterAll, beforeAll, describe, expect, it } from 'vitest'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import * as plugin from '../../../../lib/index.js'
import { createFakeContext } from '../../../helpers/fake-context'
import { closedPort } from '../../../helpers/fake-backend.mjs'
import { validateFileOpt } from '../../../../runtime/file_guard.js'
import { setPaused } from '../../../../lib/host/backend.js'

const { losslessSanitize, errorLine, redactSecrets, toolError } = plugin.__internals

const CASES: Array<[string, Record<string, unknown>]> = [
  ['blender_rt_see', { max_size: 320 }],
  ['blender_rt_do', { code: 'x = 1', see: false }],
  ['blender_rt_watch', { code: 'x = 1', seconds: 0.1, fps: 1, max_size: 120 }],
  ['blender_rt_cmd', { name: 'ping' }],
  ['blender_rt_commands', {}],
  ['blender_rt_loop', { op: 'status' }],
  ['blender_rt_perf', { op: 'status' }],
  ['blender_rt_opt', { op: 'join', args: '{"dry_run":true}' }],
  ['blender_rt_headless', { script: 'print(1)' }],
  ['blender_rt_plan', { op: 'definitely-not-an-op' }],
  ['blender_rt_worker', { op: 'start', name: 'w1' }],
  ['blender_rt_txn', { op: 'list' }],
  ['blender_rt_preset', { op: 'list' }],
  ['blender_rt_job', { op: 'list' }],
  ['blender_viewport', { op: 'status' }]
]

let fake: ReturnType<typeof createFakeContext>

beforeAll(async () => {
  const port = await closedPort()
  // ensureBackend() normally spawns a backend on first use; pause it so every
  // network tool takes its real "backend unreachable" failure path.
  setPaused(true)
  fake = createFakeContext()
  plugin.apply(fake.ctx, { autoStart: false, tui: false, port })
})

afterAll(() => {
  fake.dispose()
  setPaused(false)
})

function parse(value: any): any {
  if (value && typeof value === 'object') return value
  if (typeof value === 'string') {
    try {
      return JSON.parse(value)
    } catch {
      return null
    }
  }
  return null
}

describe('A8 error, lossless and security contract', () => {
  it('strips stack frames and secrets from diagnostics', () => {
    const raw =
      'Traceback (most recent call last):\n  File "x.py", line 1\nsk-abcdefghijklmnop\nBearer abcdefghijkl\napi_key=supersecret\nPOST failed'
    const clean = redactSecrets(raw)
    expect(clean).not.toContain('Traceback')
    expect(clean).not.toContain('File "')
    expect(clean).not.toContain('sk-abcdefghijklmnop')
    expect(clean).not.toContain('supersecret')
    expect(clean).toContain('POST failed')
    expect(errorLine(new Error('boom\n    at x (y:1:2)'))).toBe('boom')
  })

  it('builds a lossless error envelope with code/message/next_action', () => {
    const failure = toolError('invalid_params', 'missing name', 'pass name=ping')
    expect(failure.text).toContain('code: invalid_params')
    expect(failure.text).toContain('next_action: pass name=ping')
    expect(failure.envelope.error).toEqual({ code: 'invalid_params', message: 'missing name', next_action: 'pass name=ping' })
    expect(() => losslessSanitize(failure)).not.toThrow()
    expect(losslessSanitize(failure).fixes).toEqual([])
  })

  it('gives every model tool a machine-readable failure when the backend is closed', async () => {
    for (const [name, args] of CASES) {
      const tool = fake.tools.find((t) => t.name === name)!
      const out = await (tool.execute as any)(args, {})
      const parsed = parse(out)
      if (parsed && parsed.envelope && parsed.envelope.error) {
        expect(parsed.envelope.error.code, name).toBeTruthy()
        expect(parsed.envelope.error.message, name).toBeTruthy()
        expect(parsed.envelope.error.next_action, name).toBeTruthy()
        expect(String(parsed.text), name).toContain('code:')
        expect(String(parsed.text), name).toContain('next_action:')
        continue
      }
      // text-only tools: viewport status returns a JSON string, others a text body
      const text = typeof out === 'string' ? out : String(out && out.text)
      if (parsed && typeof parsed.code === 'string') {
        expect(parsed.code, name).toBeTruthy()
        expect(String(parsed.next_action || ''), name).toBeTruthy()
        continue
      }
      expect(text, name).toContain('code:')
      expect(text, name).toContain('next_action:')
    }
  })

  it('rejects symlinked file= paths', () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'dsh-symlink-'))
    const target = path.join(dir, 'target.blend')
    const link = path.join(dir, 'link.blend')
    fs.writeFileSync(target, 'x')
    try {
      try {
        fs.symlinkSync(target, link, 'file')
      } catch (e: any) {
        // Windows without Developer Mode cannot create symlinks; the code path is still
        // covered by the explicit code check below when a link can be created.
        expect(['EPERM', 'EACCES', 'UNKNOWN'].includes(String(e.code))).toBe(true)
        return
      }
      expect(() => validateFileOpt(link)).toThrow()
      try {
        validateFileOpt(link)
      } catch (e: any) {
        expect(e.code).toBe('file-symlink')
      }
    } finally {
      try {
        fs.rmSync(dir, { recursive: true, force: true })
      } catch {}
    }
  })
})
