import { afterAll, beforeAll, describe, expect, it } from 'vitest'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import * as plugin from '../../../../src/index'
import { createFakeContext } from '../../../helpers/fake-context'
import { closedPort } from '../../../helpers/fake-backend.mjs'
import { validateFileOpt } from '../../../../runtime/file_guard.js'
import { setPaused } from '../../../../src/host/backend/index'
import { validateAvailabilityEnvelope } from '../../../../src/contracts/availability'
import { formatActText } from '../../../../src/tools/py/envelope.js'

const { losslessSanitize, errorLine, redactSecrets, toolError, headlessReceipt } =
  plugin.__internals

const CASES: Array<[string, Record<string, unknown>]> = [
  ['blender_session', { action: 'list' }],
  ['blender_op', { action: 'plan', op: 'catalog' }],
  ['blender_py', { code: 'x = 1', see: false }],
  ['blender_see', {}],
  ['blender_run', { action: 'list' }],
  ['blender_inspect', {}],
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

describe('error, lossless and security contract', () => {
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
    expect(failure.envelope.error).toEqual({
      code: 'invalid_params',
      message: 'missing name',
      next_action: 'pass name=ping',
    })
    expect(failure.envelope.stage).toBe('config')
    expect(failure.envelope.root_cause).toBe('missing name')
    expect(failure.envelope.fix).toBe('pass name=ping')
    expect(failure.envelope.fallback.kind).toBe('unavailable')
    expect(failure.envelope.evidence).toEqual({ code: 'invalid_params' })
    expect(validateAvailabilityEnvelope(failure.envelope)).toEqual([])
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

  it('redacts tracebacks and secrets in Python and headless receipts', () => {
    const RAW =
      'Traceback (most recent call last):\n  File "C:/secret.py", line 1\nsk-abcdefghijklmnop\nBearer abcdefghijkl'
    const act = formatActText({ ok: true, ms: 3, stdout: RAW, stderr: RAW }).join(
      String.fromCharCode(10),
    )
    expect(act).toContain('PY ok')
    expect(act).not.toContain('Traceback')
    expect(act).not.toContain('sk-abcdefghijklmnop')
    expect(act).not.toContain('Bearer abcdefghijkl')
    const receipt = headlessReceipt({ ok: false, error: 'boom', traceback: RAW }, {})
    expect(String(receipt.text)).not.toContain('Traceback')
    expect(String(receipt.text)).not.toContain('sk-abcdefghijklmnop')
    expect(String(receipt.text)).not.toContain('Bearer abcdefghijkl')
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
