import crypto from 'node:crypto'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  INSTALL_LOCK_FILE,
  PBS_PINS,
  buildRequirementsText,
  createVisionInstaller,
  installLogPath,
  managedVenvPath,
  noticesPath,
  platformKey,
  readRequirementsLock,
  visionStatePath,
} from '../../../runtime/vision/installer.js'
import {
  PYTHON_PROBE_SCRIPT,
  venvPythonPath,
  type CommandRunner,
} from '../../../runtime/vision/python-detect.js'

const homes: string[] = []

function tempHome(): string {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'dsh-vision-install-'))
  homes.push(dir)
  return dir
}

afterEach(() => {
  while (homes.length > 0) {
    const dir = homes.pop() as string
    fs.rmSync(dir, { recursive: true, force: true })
    fs.rmSync(path.join(os.tmpdir(), 'dsh-vision-install-'), { recursive: true, force: true })
  }
})

interface FakeOptions {
  systemPython?: string | null
  probeVersion?: [number, number, number]
  venvOk?: boolean
  pipResults?: number[]
  verifyOk?: boolean
  modules?: { cv2: string; numpy: string; onnxruntime: string | null }
  onCreateVenv?: (target: string) => void
}

function fakeRunner(options: FakeOptions = {}): {
  run: CommandRunner
  calls: Array<{ command: string; args: string[]; timeoutMs?: number; env?: NodeJS.ProcessEnv }>
  pipCalls: number
} {
  const calls: Array<{
    command: string
    args: string[]
    timeoutMs?: number
    env?: NodeJS.ProcessEnv
  }> = []
  let pipIndex = 0
  let pipCalls = 0
  const versions = options.probeVersion ?? [3, 12, 4]
  const run: CommandRunner = async (command, args, runOptions) => {
    calls.push({
      command,
      args: [...args],
      timeoutMs: runOptions?.timeoutMs,
      env: runOptions?.env,
    })
    if (args[0] === '-m' && args[1] === 'venv') {
      if (options.venvOk === false) return { code: 1, stdout: '', stderr: 'venv failed' }
      const target = args[2] as string
      const python = venvPythonPath(target, 'win32')
      fs.mkdirSync(path.dirname(python), { recursive: true })
      fs.writeFileSync(python, 'fake')
      options.onCreateVenv?.(target)
      return { code: 0, stdout: '', stderr: '' }
    }
    if (args[0] === '-m' && args[1] === 'pip') {
      pipCalls += 1
      const code = options.pipResults?.[pipIndex] ?? 0
      pipIndex += 1
      return { code, stdout: '', stderr: code === 0 ? '' : 'pip failed' }
    }
    if (args.includes('-c')) {
      const script = args[args.length - 1] as string
      if (script.includes('findContours')) {
        if (options.verifyOk === false)
          return { code: 1, stdout: '', stderr: 'contour check failed' }
        const modules = options.modules ?? { cv2: '5.0.0', numpy: '2.4.6', onnxruntime: null }
        return { code: 0, stdout: JSON.stringify(modules), stderr: '' }
      }
      if (script === PYTHON_PROBE_SCRIPT) {
        return {
          code: 0,
          stdout: JSON.stringify({
            version: versions,
            versionText: versions.join('.'),
            hasVenv: true,
            cv2: '5.0.0',
            numpy: '2.4.6',
            onnxruntime: null,
          }),
          stderr: '',
        }
      }
    }
    if (command === 'tar') return { code: 0, stdout: '', stderr: '' }
    return { code: options.systemPython ? 0 : 1, stdout: '', stderr: 'not found' }
  }
  return {
    run,
    calls,
    get pipCalls() {
      return pipCalls
    },
  } as never
}

function managedInstaller(
  home: string,
  runner: ReturnType<typeof fakeRunner>,
  download?: (url: string, dest: string) => Promise<{ bytes: number }>,
) {
  return createVisionInstaller({
    homeDir: home,
    platform: 'win32',
    arch: 'x64',
    env: {},
    run: runner.run,
    values: () => ({ python: 'C:/fake/python.exe', venv: '', backend: 'opencv' }),
    download,
  })
}

describe('requirements lock', () => {
  it('maps platforms and renders hash-pinned requirements', () => {
    expect(platformKey('win32', 'x64')).toBe('win32-x64')
    expect(platformKey('linux', 'arm64')).toBe('linux-arm64')
    expect(platformKey('darwin', 'x64')).toBe('darwin-x64')
    expect(platformKey('freebsd', 'x64')).toBeNull()
    const lock = readRequirementsLock()
    const selection = buildRequirementsText(lock, {
      platform: 'win32',
      arch: 'x64',
      pythonMinor: 11,
    })
    expect(selection.hashPolicy).toBe('require-hashes')
    expect(selection.hashVerified).toBe(true)
    expect(selection.requirements).toContain('opencv-python-headless==5.0.0.93')
    expect(selection.requirements).toContain('numpy==2.4.6')
    expect(selection.requirements).toContain(
      '829717b6a95554f273e49e357cee3b3a2a26b6f4842fbc1bed2b45bdd8f87e0e',
    )
    expect(selection.requirements).toContain(
      '1e254a00cdf42b1e4d5b3d68d33af63268d41340d8885df2ab6470f2e1500147',
    )
  })

  it('rejects unsupported platforms instead of silently installing', () => {
    const lock = readRequirementsLock()
    expect(() =>
      buildRequirementsText(lock, { platform: 'freebsd', arch: 'x64', pythonMinor: 11 }),
    ).toThrow(/unsupported platform/)
  })

  it('never mixes hashed and unhashed requirements', () => {
    const lock = readRequirementsLock()
    // requireHashes 默认 true：缺 sha256 直接 fail fast，而不是降级成 pinned-only。
    const broken = {
      ...lock,
      packages: lock.packages.map((pkg, index) =>
        index === 0 ? { ...pkg, files: pkg.files.map((file) => ({ ...file, sha256: '' })) } : pkg,
      ),
    }
    expect(() =>
      buildRequirementsText(broken, { platform: 'win32', arch: 'x64', pythonMinor: 11 }),
    ).toThrow(/sha256/)

    // requireHashes:false 时整份文件都不要 hash，pip 不会报 mixed-shape 错误。
    const relaxed = { ...lock, installer: { requireHashes: false } }
    const selection = buildRequirementsText(relaxed, {
      platform: 'win32',
      arch: 'x64',
      pythonMinor: 11,
    })
    expect(selection.hashPolicy).toBe('pinned-only')
    expect(selection.hashVerified).toBe(false)
    expect(selection.requirements).not.toContain('--hash=')
    expect(selection.requirements).toContain('opencv-python-headless==5.0.0.93')
  })
})

describe('custom vision.venv', () => {
  it('marks a valid custom venv ready and never creates the managed venv', async () => {
    const home = tempHome()
    const venv = path.join(home, 'my-venv')
    const python = venvPythonPath(venv, 'win32')
    fs.mkdirSync(path.dirname(python), { recursive: true })
    fs.writeFileSync(python, 'fake')
    const runner = fakeRunner()
    const installer = createVisionInstaller({
      homeDir: home,
      platform: 'win32',
      arch: 'x64',
      env: {},
      run: runner.run,
      values: () => ({ python: '', venv, backend: 'opencv' }),
    })
    const status = await installer.ensureInstalled()
    expect(status.status).toBe('ready')
    expect(status.source).toBe('custom')
    expect(status.env.source).toBe('custom')
    expect(status.python).toBe(python)
    expect(status.cv2).toBe('5.0.0')
    expect(status.licenses).toEqual([])
    expect(status.noticesPath).toBeNull()
    expect(fs.existsSync(managedVenvPath(home))).toBe(false)
    expect(runner.calls.some((call) => call.args[0] === '-m' && call.args[1] === 'pip')).toBe(false)
  })

  it('fails explicitly for an invalid custom venv without falling back', async () => {
    const home = tempHome()
    const venv = path.join(home, 'my-venv')
    const python = venvPythonPath(venv, 'win32')
    fs.mkdirSync(path.dirname(python), { recursive: true })
    fs.writeFileSync(python, 'fake')
    const runner = fakeRunner()
    // The fake reports a good python; make it look module-less instead.
    const broken = createVisionInstaller({
      homeDir: home,
      platform: 'win32',
      arch: 'x64',
      env: {},
      run: async (command, args, options) => {
        if (args.includes('-c')) {
          return {
            code: 0,
            stdout: JSON.stringify({
              version: [3, 12, 1],
              versionText: '3.12.1',
              hasVenv: true,
              cv2: null,
              numpy: null,
              onnxruntime: null,
            }),
            stderr: '',
          }
        }
        return runner.run(command, args, options)
      },
      values: () => ({ python: '', venv, backend: 'opencv' }),
    })
    const status = await broken.ensureInstalled()
    expect(status.status).toBe('failed')
    expect(status.error?.code).toBe('vision_custom_env_invalid')
    expect(status.error?.details?.missingModules).toEqual(['cv2', 'numpy'])
    expect(fs.existsSync(managedVenvPath(home))).toBe(false)
    expect(fs.existsSync(visionStatePath(home))).toBe(true)
  })
})

describe('managed install', () => {
  it('creates the venv, installs hash-locked requirements, verifies and writes notices', async () => {
    const home = tempHome()
    const runner = fakeRunner()
    const installer = managedInstaller(home, runner)
    const status = await installer.ensureInstalled()
    expect(status.status).toBe('ready')
    expect(status.source).toBe('managed')
    expect(status.env.source).toBe('managed')
    expect(status.venv).toBe(managedVenvPath(home))
    expect(status.hashPolicy).toBe('require-hashes')
    expect(status.hashVerified).toBe(true)
    expect(status.cv2).toBe('5.0.0')
    expect(status.numpy).toBe('2.4.6')
    expect(status.licenses).toHaveLength(3)
    expect(fs.existsSync(noticesPath(home))).toBe(true)
    expect(fs.readFileSync(noticesPath(home), 'utf8')).toContain('Apache-2.0')
    const persisted = JSON.parse(fs.readFileSync(visionStatePath(home), 'utf8'))
    expect(persisted.status).toBe('ready')
    const pip = runner.calls.find((call) => call.args[0] === '-m' && call.args[1] === 'pip')
    expect(pip?.args).toContain('--require-hashes')
    const req = fs.readFileSync(path.join(home, 'vision', 'cache', 'requirements.txt'), 'utf8')
    expect(req).toContain('--hash=sha256:')
    expect(fs.existsSync(path.join(home, 'vision', 'install.lock'))).toBe(false)
  })

  it('retries pip failures and succeeds on the third attempt', async () => {
    const home = tempHome()
    const runner = fakeRunner({ pipResults: [1, 1, 0] })
    const status = await managedInstaller(home, runner).ensureInstalled()
    expect(status.status).toBe('ready')
    expect(status.attempts).toBe(3)
    expect(runner.pipCalls).toBe(3)
  })

  it('records a failed install with attempts, log path and proxy hint', async () => {
    const home = tempHome()
    const runner = fakeRunner({ pipResults: [1, 1, 1] })
    const status = await managedInstaller(home, runner).ensureInstalled()
    expect(status.status).toBe('failed')
    expect(status.error?.code).toBe('vision_install_failed')
    expect(status.error?.details?.attempts).toBe(3)
    expect(status.error?.fix).toContain('代理')
    expect(status.logPath).toBe(installLogPath(home))
    const log = fs.readFileSync(installLogPath(home), 'utf8')
    expect(log).toContain('attempt=3')
    expect(log).toContain('stage=installing')
  })

  it('shares one in-flight install between concurrent calls', async () => {
    const home = tempHome()
    const runner = fakeRunner()
    const installer = managedInstaller(home, runner)
    const first = installer.ensureInstalled()
    const second = installer.ensureInstalled()
    expect(second).toBe(first)
    await first
    expect(runner.pipCalls).toBe(1)
  })

  it('does not start a second install when a fresh lock is held by another process', async () => {
    const home = tempHome()
    fs.mkdirSync(path.join(home, 'vision'), { recursive: true })
    fs.writeFileSync(
      path.join(home, 'vision', INSTALL_LOCK_FILE),
      JSON.stringify({ pid: 123, startedAt: Date.now() }),
    )
    const runner = fakeRunner()
    const status = await managedInstaller(home, runner).ensureInstalled()
    expect(status.status).toBe('not_installed')
    expect(runner.calls).toEqual([])
    expect(fs.existsSync(path.join(home, 'vision', INSTALL_LOCK_FILE))).toBe(true)
  })

  it('resumes an existing managed venv without creating it again', async () => {
    const home = tempHome()
    const python = venvPythonPath(managedVenvPath(home), 'win32')
    fs.mkdirSync(path.dirname(python), { recursive: true })
    fs.writeFileSync(python, 'fake')
    const runner = fakeRunner()
    const status = await managedInstaller(home, runner).ensureInstalled()
    expect(status.status).toBe('ready')
    expect(runner.calls.some((call) => call.args[0] === '-m' && call.args[1] === 'venv')).toBe(
      false,
    )
    expect(runner.calls.some((call) => call.args[0] === '-m' && call.args[1] === 'pip')).toBe(false)
  })

  it('gives venv and pip their own long budgets instead of the 15 s probe default', async () => {
    const home = tempHome()
    const runner = fakeRunner()
    const status = await managedInstaller(home, runner).ensureInstalled()
    expect(status.status).toBe('ready')
    const venv = runner.calls.find((call) => call.args[0] === '-m' && call.args[1] === 'venv')
    const pip = runner.calls.find((call) => call.args[0] === '-m' && call.args[1] === 'pip')
    expect(venv?.timeoutMs).toBe(300_000)
    expect(pip?.timeoutMs).toBe(1_800_000)
  })

  it('lets callers override the install budgets', async () => {
    const home = tempHome()
    const runner = fakeRunner()
    const installer = createVisionInstaller({
      homeDir: home,
      platform: 'win32',
      arch: 'x64',
      env: { PATH: 'C:/bin', DSH_BLENDER_SECRET: 's', OPENAI_API_KEY: 'k' },
      run: runner.run,
      values: () => ({ python: 'C:/fake/python.exe', venv: '', backend: 'opencv' }),
      timeouts: { venvMs: 11_000, pipMs: 22_000 },
    })
    await installer.ensureInstalled()
    const venv = runner.calls.find((call) => call.args[0] === '-m' && call.args[1] === 'venv')
    const pip = runner.calls.find((call) => call.args[0] === '-m' && call.args[1] === 'pip')
    expect(venv?.timeoutMs).toBe(11_000)
    expect(pip?.timeoutMs).toBe(22_000)
    // 环境变量白名单：宿主 secret 不能传给 venv/pip/tar。
    expect(pip?.env?.DSH_BLENDER_SECRET).toBeUndefined()
    expect(pip?.env?.OPENAI_API_KEY).toBeUndefined()
    expect(pip?.env?.PATH).toBe('C:/bin')
    expect(pip?.env?.PIP_CACHE_DIR).toBeTruthy()
  })

  it('keeps the previous venv and leaves no temp dirs when pip fails', async () => {
    const home = tempHome()
    const venv = managedVenvPath(home)
    const python = venvPythonPath(venv, 'win32')
    fs.mkdirSync(path.dirname(python), { recursive: true })
    fs.writeFileSync(python, 'fake')
    fs.writeFileSync(path.join(venv, 'marker.txt'), 'old')
    const runner = fakeRunner({ pipResults: [1, 1, 1], verifyOk: false })
    const status = await managedInstaller(home, runner).ensureInstalled()
    expect(status.status).toBe('failed')
    expect(fs.readFileSync(path.join(venv, 'marker.txt'), 'utf8')).toBe('old')
    expect(
      fs
        .readdirSync(path.join(home, 'vision'))
        .filter((name) => name.startsWith('venv.tmp-') || name.startsWith('venv.old-')),
    ).toEqual([])
  })

  it('restores the old venv when the final swap fails', async () => {
    const home = tempHome()
    const venv = managedVenvPath(home)
    const python = venvPythonPath(venv, 'win32')
    fs.mkdirSync(path.dirname(python), { recursive: true })
    fs.writeFileSync(python, 'fake')
    fs.writeFileSync(path.join(venv, 'marker.txt'), 'old')
    const runner = fakeRunner({ verifyOk: false })
    const original = fs.renameSync
    const spy = vi.spyOn(fs, 'renameSync').mockImplementation((from, to) => {
      if (String(to) === venv && String(from).includes('venv.tmp-')) {
        throw new Error('simulated swap failure')
      }
      return original(from, to)
    })
    try {
      const status = await managedInstaller(home, runner).ensureInstalled()
      expect(status.status).toBe('failed')
    } finally {
      spy.mockRestore()
    }
    expect(fs.readFileSync(path.join(venv, 'marker.txt'), 'utf8')).toBe('old')
    expect(
      fs
        .readdirSync(path.join(home, 'vision'))
        .filter((name) => name.startsWith('venv.tmp-') || name.startsWith('venv.old-')),
    ).toEqual([])
  })
})

describe('managed python download fallback', () => {
  it('downloads, verifies and extracts the managed python when no system interpreter exists', async () => {
    const home = tempHome()
    const pin = PBS_PINS['win32-x64']
    const originalSha = pin.sha256
    const originalBytes = pin.bytes
    try {
      const payload = Buffer.from('standalone-python-archive')
      pin.sha256 = crypto.createHash('sha256').update(payload).digest('hex')
      pin.bytes = payload.length
      const calls: Array<{ command: string; args: string[] }> = []
      const status = await createVisionInstaller({
        homeDir: home,
        platform: 'win32',
        arch: 'x64',
        env: {},
        run: async (command, args) => {
          calls.push({ command, args: [...args] })
          if (command === 'tar') {
            const root = args[args.length - 1] as string
            const python = path.join(root, 'python', 'python.exe')
            fs.mkdirSync(path.dirname(python), { recursive: true })
            fs.writeFileSync(python, 'fake')
            return { code: 0, stdout: '', stderr: '' }
          }
          if (args.includes('-c')) {
            const script = args[args.length - 1] as string
            if (script.includes('findContours')) {
              return {
                code: 0,
                stdout: JSON.stringify({ cv2: '5.0.0', numpy: '2.4.6', onnxruntime: null }),
                stderr: '',
              }
            }
            const managed = command.toLowerCase().includes('vision')
            if (managed) {
              return {
                code: 0,
                stdout: JSON.stringify({
                  version: [3, 13, 18],
                  versionText: '3.13.18',
                  hasVenv: true,
                  cv2: null,
                  numpy: null,
                  onnxruntime: null,
                }),
                stderr: '',
              }
            }
            return { code: 1, stdout: '', stderr: 'no system python' }
          }
          if (args[0] === '-m' && args[1] === 'venv') {
            const target = args[2] as string
            const python = venvPythonPath(target, 'win32')
            fs.mkdirSync(path.dirname(python), { recursive: true })
            fs.writeFileSync(python, 'fake')
            return { code: 0, stdout: '', stderr: '' }
          }
          return { code: 0, stdout: '', stderr: '' }
        },
        values: () => ({ python: '', venv: '', backend: 'opencv' }),
        download: async (_url, dest) => {
          fs.mkdirSync(path.dirname(dest), { recursive: true })
          fs.writeFileSync(dest, payload)
          return { bytes: payload.length }
        },
      }).ensureInstalled()
      expect(status.status).toBe('ready')
      expect(status.source).toBe('managed')
      expect(calls.some((call) => call.command === 'tar')).toBe(true)
      expect(calls.some((call) => call.args[0] === '-m' && call.args[1] === 'venv')).toBe(true)
    } finally {
      pin.sha256 = originalSha
      pin.bytes = originalBytes
    }
  })

  it('installs the runtime even when features.dnn is disabled', async () => {
    const home = tempHome()
    fs.mkdirSync(path.join(home, 'vision'), { recursive: true })
    fs.writeFileSync(
      path.join(home, 'vision', 'runtime.json'),
      JSON.stringify({ features: { dnn: false } }),
    )
    const status = await managedInstaller(home, fakeRunner()).ensureInstalled()
    expect(status.status).toBe('ready')
  })

  it('refuses to extract or execute a python tarball whose checksum does not match', async () => {
    const home = tempHome()
    const pin = PBS_PINS['win32-x64']
    const originalSha = pin.sha256
    const originalBytes = pin.bytes
    try {
      const payload = Buffer.from('tampered')
      pin.sha256 = crypto.createHash('sha256').update(Buffer.from('expected')).digest('hex')
      pin.bytes = Buffer.from('expected').length
      const calls: Array<{ command: string; args: string[] }> = []
      const status = await createVisionInstaller({
        homeDir: home,
        platform: 'win32',
        arch: 'x64',
        env: {},
        run: async (command, args) => {
          calls.push({ command, args: [...args] })
          return { code: 1, stdout: '', stderr: 'no python found' }
        },
        values: () => ({ python: '', venv: '', backend: 'opencv' }),
        download: async (_url, dest) => {
          fs.mkdirSync(path.dirname(dest), { recursive: true })
          fs.writeFileSync(dest, payload)
          return { bytes: payload.length }
        },
      }).ensureInstalled()
      expect(status.status).toBe('failed')
      expect(status.error?.code).toBe('python_missing')
      expect(status.error?.details?.reason).toBe('checksum_mismatch')
      expect(calls.some((call) => call.command === 'tar')).toBe(false)
      expect(calls.some((call) => call.args[0] === '-m' && call.args[1] === 'venv')).toBe(false)
    } finally {
      pin.sha256 = originalSha
      pin.bytes = originalBytes
    }
  })
})
