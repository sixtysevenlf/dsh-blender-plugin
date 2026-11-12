import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { afterEach, describe, expect, it } from 'vitest'

import {
  CommandRunner,
  discoverPython,
  parsePythonProbe,
  parseVersionText,
  venvPythonPath,
} from '../../../runtime/vision/python-detect.js'
import { VisionError } from '../../../runtime/vision/protocol.js'

const homes: string[] = []

function tempDir(): string {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'dsh-vision-python-'))
  homes.push(dir)
  return dir
}

afterEach(() => {
  while (homes.length > 0) {
    const dir = homes.pop() as string
    fs.rmSync(dir, { recursive: true, force: true })
  }
})

interface Reply {
  command: string
  args: string[]
  probe?: Record<string, unknown> | null
  exitCode?: number
  stderr?: string
}

function fakeRunner(replies: Reply[]): {
  run: CommandRunner
  calls: Array<{ command: string; args: string[] }>
} {
  const calls: Array<{ command: string; args: string[] }> = []
  const pending = [...replies]
  const run: CommandRunner = async (command, args) => {
    calls.push({ command, args })
    const reply = pending.shift()
    if (!reply) return { code: 1, stdout: '', stderr: 'not found' }
    if (reply.probe === null)
      return { code: reply.exitCode ?? 1, stdout: '', stderr: reply.stderr ?? 'boom' }
    return {
      code: reply.exitCode ?? 0,
      stdout: JSON.stringify(reply.probe),
      stderr: reply.stderr ?? '',
    }
  }
  return { run, calls }
}

function validVenv(dir: string, platform: NodeJS.Platform = 'win32'): string {
  const python = venvPythonPath(dir, platform)
  fs.mkdirSync(path.dirname(python), { recursive: true })
  fs.writeFileSync(python, 'fake')
  return python
}

function probe(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    version: [3, 13, 1],
    versionText: '3.13.1',
    hasVenv: true,
    cv2: '5.0.0',
    numpy: '2.4.6',
    onnxruntime: null,
    ...overrides,
  }
}

const NO_ENV: NodeJS.ProcessEnv = {}

describe('python version helpers', () => {
  it('parses and validates supported versions', () => {
    expect(parseVersionText('Python 3.11.1')).toEqual({ major: 3, minor: 11, patch: 1 })
    expect(parseVersionText('3.13.0')).toEqual({ major: 3, minor: 13, patch: 0 })
    expect(parseVersionText('nope')).toBeNull()
    expect(parsePythonProbe(JSON.stringify(probe()))).toMatchObject({
      ok: true,
      version: { major: 3, minor: 13, patch: 1 },
      hasVenv: true,
    })
    expect(parsePythonProbe('not json')).toBeNull()
  })

  it('resolves the venv interpreter per platform', () => {
    expect(venvPythonPath('C:/venv', 'win32')).toBe(path.join('C:/venv', 'Scripts', 'python.exe'))
    expect(venvPythonPath('/venv', 'linux')).toBe(path.join('/venv', 'bin', 'python'))
  })
})

describe('custom vision.venv', () => {
  it('returns the venv interpreter and skips discovery when valid', async () => {
    const venv = tempDir()
    const python = validVenv(venv)
    const { run } = fakeRunner([{ command: python, args: [], probe: probe() }])
    const found = await discoverPython({
      run,
      platform: 'win32',
      env: NO_ENV,
      values: { python: '', venv, backend: 'opencv' },
    })
    expect(found).toMatchObject({ interpreter: python, source: 'venv', version: { minor: 13 } })
  })

  it('rejects a relative vision.venv before probing anything', async () => {
    const { run, calls } = fakeRunner([])
    await expect(
      discoverPython({
        run,
        platform: 'win32',
        env: NO_ENV,
        values: { python: '', venv: 'relative/venv', backend: 'opencv' },
      }),
    ).rejects.toMatchObject({
      code: 'vision_custom_env_invalid',
      details: { reason: 'relative_path' },
    })
    expect(calls).toEqual([])
  })

  it('fails explicitly when the venv has no interpreter and does not fall back', async () => {
    const venv = tempDir()
    fs.mkdirSync(path.join(venv, 'Scripts'), { recursive: true })
    const { run, calls } = fakeRunner([])
    await expect(
      discoverPython({
        run,
        platform: 'win32',
        env: NO_ENV,
        values: { python: '', venv, backend: 'opencv' },
      }),
    ).rejects.toMatchObject({
      code: 'vision_custom_env_invalid',
      stage: 'config',
      details: { reason: 'no_python' },
    })
    expect(calls).toEqual([])
  })

  it('rejects an out-of-range interpreter version', async () => {
    const venv = tempDir()
    const python = validVenv(venv)
    const { run } = fakeRunner([
      { command: python, args: [], probe: probe({ version: [3, 10, 9], versionText: '3.10.9' }) },
    ])
    await expect(
      discoverPython({
        run,
        platform: 'win32',
        env: NO_ENV,
        values: { python: '', venv, backend: 'opencv' },
      }),
    ).rejects.toMatchObject({ code: 'vision_custom_env_invalid', details: { reason: 'version' } })
  })

  it('lists missing cv2 / numpy modules', async () => {
    const venv = tempDir()
    const python = validVenv(venv)
    const { run } = fakeRunner([
      { command: python, args: [], probe: probe({ cv2: null, numpy: null }) },
    ])
    await expect(
      discoverPython({
        run,
        platform: 'win32',
        env: NO_ENV,
        values: { python: '', venv, backend: 'opencv' },
      }),
    ).rejects.toMatchObject({
      code: 'vision_custom_env_invalid',
      details: { reason: 'missing_modules', missingModules: ['cv2', 'numpy'] },
    })
  })

  it('requires onnxruntime in the custom venv only for backend=onnxruntime', async () => {
    const venv = tempDir()
    const python = validVenv(venv)
    const { run } = fakeRunner([{ command: python, args: [], probe: probe({ onnxruntime: null }) }])
    await expect(
      discoverPython({
        run,
        platform: 'win32',
        env: NO_ENV,
        values: { python: '', venv, backend: 'onnxruntime' },
      }),
    ).rejects.toMatchObject({
      code: 'vision_custom_env_invalid',
      details: { missingModules: ['onnxruntime'] },
    })
  })

  it('reports an unparsable probe as vision_custom_env_invalid', async () => {
    const venv = tempDir()
    const python = validVenv(venv)
    const { run } = fakeRunner([
      { command: python, args: [], probe: null, stderr: 'access denied' },
    ])
    await expect(
      discoverPython({
        run,
        platform: 'win32',
        env: NO_ENV,
        values: { python: '', venv, backend: 'opencv' },
      }),
    ).rejects.toMatchObject({
      code: 'vision_custom_env_invalid',
      details: { reason: 'probe_failed' },
    })
  })
})

describe('system discovery', () => {
  it('uses vision.python before the platform launchers', async () => {
    const { run, calls } = fakeRunner([
      { command: 'C:/Python312/python.exe', args: [], probe: probe({ version: [3, 12, 4] }) },
    ])
    const found = await discoverPython({
      run,
      platform: 'win32',
      env: NO_ENV,
      values: { python: 'C:/Python312/python.exe', venv: '', backend: 'opencv' },
    })
    expect(found).toMatchObject({ source: 'python', interpreter: 'C:/Python312/python.exe' })
    expect(calls).toHaveLength(1)
  })

  it('skips out-of-range launchers and falls through the ordered candidates', async () => {
    const { run, calls } = fakeRunner([
      {
        command: 'py',
        args: ['-3.13'],
        probe: probe({ version: [3, 10, 0], versionText: '3.10.0' }),
      },
      {
        command: 'py',
        args: ['-3.12'],
        probe: probe({ version: [3, 12, 2], versionText: '3.12.2' }),
      },
    ])
    const found = await discoverPython({
      run,
      platform: 'win32',
      env: NO_ENV,
      values: { python: '', venv: '', backend: 'opencv' },
    })
    expect(found).toMatchObject({ interpreter: 'py', args: ['-3.12'], source: 'system' })
    expect(calls.map((call) => call.args[0])).toEqual(['-3.13', '-3.12'])
  })

  it('prefers DSH_BLENDER_PYTHON over platform launchers', async () => {
    const { run } = fakeRunner([
      { command: 'C:/custom/python.exe', args: [], probe: probe({ version: [3, 11, 8] }) },
    ])
    const found = await discoverPython({
      run,
      platform: 'linux',
      env: { DSH_BLENDER_PYTHON: 'C:/custom/python.exe' },
      values: { python: '', venv: '', backend: 'opencv' },
    })
    expect(found).toMatchObject({ interpreter: 'C:/custom/python.exe', source: 'env' })
  })

  it('ignores candidates that cannot import venv', async () => {
    const { run } = fakeRunner([
      { command: 'python3.13', args: [], probe: probe({ hasVenv: false }) },
      {
        command: 'python3.12',
        args: [],
        probe: probe({ version: [3, 12, 0], versionText: '3.12.0' }),
      },
    ])
    const found = await discoverPython({
      run,
      platform: 'linux',
      env: NO_ENV,
      values: { python: '', venv: '', backend: 'opencv' },
    })
    expect(found).toMatchObject({ interpreter: 'python3.12', source: 'system' })
  })

  it('throws python_missing when nothing usable exists', async () => {
    const { run } = fakeRunner([])
    await expect(
      discoverPython({
        run,
        platform: 'win32',
        env: NO_ENV,
        values: { python: '', venv: '', backend: 'opencv' },
      }),
    ).rejects.toBeInstanceOf(VisionError)
    await expect(
      discoverPython({
        run,
        platform: 'win32',
        env: NO_ENV,
        values: { python: '', venv: '', backend: 'opencv' },
      }),
    ).rejects.toMatchObject({ code: 'python_missing', stage: 'config', retryable: false })
  })
})
