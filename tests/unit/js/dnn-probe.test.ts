import { EventEmitter } from 'node:events'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { afterEach, describe, expect, it } from 'vitest'
import {
  probeDnn,
  resetProbeCacheForTests,
  type DnnProbeCandidate,
  type ProbeSpawn,
} from '../../../src/host/dnn-probe.js'

const tempDirs: string[] = []

function tempDir(): string {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'dsh-blender-dnn-probe-'))
  tempDirs.push(dir)
  return dir
}

function candidate(overrides: Partial<DnnProbeCandidate> = {}): DnnProbeCandidate {
  return {
    features: { dnn: true },
    vision: { python: '', venv: '', model: 'yolov5n' },
    dnn: { backend: 'opencv', device: 'cpu' },
    ...overrides,
  }
}

interface FakeChild extends EventEmitter {
  stdin: {
    written: string
    write: (chunk: string) => void
    end: () => void
    on?: (event: 'error', cb: (error: unknown) => void) => void
  }
  stdout: EventEmitter
  stderr: EventEmitter
  killed: boolean
  kill: (signal?: string) => void
  stdinError?: (error: unknown) => void
}

function fakeChild(respond?: (child: FakeChild) => void): FakeChild {
  const child = new EventEmitter() as FakeChild
  child.stdout = new EventEmitter()
  child.stderr = new EventEmitter()
  child.killed = false
  child.kill = () => {
    child.killed = true
  }
  child.stdin = {
    written: '',
    write(chunk: string) {
      this.written += chunk
    },
    end() {
      if (respond) respond(child)
    },
    on(event: 'error', cb: (error: unknown) => void) {
      if (event === 'error') child.stdinError = cb
    },
  }
  return child
}

function spawnWith(child: FakeChild): ProbeSpawn & { calls: unknown[][] } {
  const calls: unknown[][] = []
  const spawn = ((...args: unknown[]) => {
    calls.push(args)
    return child
  }) as unknown as ProbeSpawn & { calls: unknown[][] }
  spawn.calls = calls
  return spawn
}

const pythonResult = (checks: unknown[] = []) => ({
  ok: checks.every((c) => (c as { status: string }).status !== 'fail'),
  checks,
  summary: { pass: checks.length, warn: 0, fail: 0 },
  elapsedMs: 12,
  candidate: candidate(),
})

const pythonInterpreter = { command: 'C:/py/python.exe', args: [], source: 'venv' as const }

afterEach(() => {
  resetProbeCacheForTests()
  for (const dir of tempDirs.splice(0)) fs.rmSync(dir, { recursive: true, force: true })
})

describe('dnn probe', () => {
  it('rejects a malformed candidate before spawning anything', async () => {
    const home = tempDir()
    const spawn = spawnWith(fakeChild())
    for (const bad of [
      candidate({ dnn: { backend: 'tensorrt' as 'opencv', device: 'cpu' } }),
      candidate({ dnn: { backend: 'opencv', device: 'auto' as 'cpu' } }),
      { features: { dnn: 'yes' }, vision: candidate().vision, dnn: candidate().dnn },
    ]) {
      const result = await probeDnn({
        candidate: bad,
        home,
        spawn,
        resolveInterpreter: () => pythonInterpreter,
      })
      expect(result.ok).toBe(false)
      expect(result.code).toBe('invalid_params')
    }
    expect(spawn.calls).toHaveLength(0)
  })

  it('accepts the nested vision.dnn candidate shape sent by the config card', async () => {
    const home = tempDir()
    const child = fakeChild((c) => {
      const payload = JSON.stringify(
        pythonResult([{ id: 'python', label: '解释器版本', status: 'pass', detail: '3.11.1' }]),
      )
      c.stdout.emit('data', payload)
      c.emit('close', 0)
    })
    const spawn = spawnWith(child)
    const probeScript = path.join(tempDir(), 'probe.py')
    fs.writeFileSync(probeScript, '# probe')
    const nested = {
      features: { dnn: true },
      vision: { python: '', venv: '', model: 'yolov5n', dnn: { backend: 'opencv', device: 'cpu' } },
    }
    const result = await probeDnn({
      candidate: nested,
      home,
      spawn,
      resolveInterpreter: () => pythonInterpreter,
      probeScript,
    })
    expect(result.ok).toBe(true)
    const payload = JSON.parse(child.stdin.written)
    expect(payload.candidate).toEqual(candidate())
  })

  it('runs probe.py without a shell, merges host checks and returns the python result', async () => {
    const home = tempDir()
    const child = fakeChild((c) => {
      const payload = JSON.stringify(
        pythonResult([{ id: 'python', label: '解释器版本', status: 'pass', detail: '3.11.1' }]),
      )
      c.stdout.emit('data', payload.slice(0, 12))
      c.stdout.emit('data', payload.slice(12))
      c.emit('close', 0)
    })
    const spawn = spawnWith(child)
    const probeScript = path.join(tempDir(), 'probe.py')
    fs.writeFileSync(probeScript, '# probe')
    const result = await probeDnn({
      candidate: candidate(),
      home,
      envLocked: true,
      spawn,
      resolveInterpreter: () => pythonInterpreter,
      probeScript,
    })
    expect(result.ok).toBe(true)
    const [, args, options] = spawn.calls[0] as [string, string[], Record<string, unknown>]
    expect(args).toContain('-B')
    expect(args).toContain(probeScript)
    expect(options.shell).toBe(false)
    expect(options.windowsHide).toBe(true)
    expect((options.env as Record<string, string>).PYTHONDONTWRITEBYTECODE).toBe('1')
    expect(result.checks.map((c) => c.id)).toEqual(
      expect.arrayContaining(['feature', 'interpreter', 'python']),
    )
    expect(result.checks.find((c) => c.id === 'feature')?.detail).toContain('DSH_BLENDER_DNN')
    expect(result.summary.pass).toBeGreaterThan(0)
    const payload = JSON.parse(child.stdin.written)
    expect(payload.candidate).toEqual(candidate())
    expect(payload.home).toBe(home)
    expect(payload.modelsDir).toBe(path.join(home, 'vision', 'models'))
  })

  it('leaves no runtime.json, models directory or worker state after repeated probes', async () => {
    const home = tempDir()
    const probeScript = path.join(tempDir(), 'probe.py')
    fs.writeFileSync(probeScript, '# probe')
    const runOnce = async () => {
      const child = fakeChild((c) => {
        c.stdout.emit('data', JSON.stringify(pythonResult([{ id: 'python', status: 'pass' }])))
        c.emit('close', 0)
      })
      return await probeDnn({
        candidate: candidate(),
        home,
        spawn: spawnWith(child),
        resolveInterpreter: () => pythonInterpreter,
        probeScript,
      })
    }
    expect((await runOnce()).ok).toBe(true)
    expect((await runOnce()).ok).toBe(true)
    expect(fs.existsSync(path.join(home, 'vision'))).toBe(false)
    expect(fs.existsSync(path.join(home, 'vision', 'runtime.json'))).toBe(false)
    expect(fs.existsSync(path.join(home, 'vision', 'models'))).toBe(false)
  })

  it('times out after the budget, kills the child and reports dnn_probe_timeout', async () => {
    const home = tempDir()
    const child = fakeChild()
    const spawn = spawnWith(child)
    const started = Date.now()
    const result = await probeDnn({
      candidate: candidate(),
      home,
      spawn,
      timeoutMs: 40,
      resolveInterpreter: () => pythonInterpreter,
    })
    expect(Date.now() - started).toBeLessThan(3000)
    expect(result.ok).toBe(false)
    expect(result.code).toBe('dnn_probe_timeout')
    expect(child.killed).toBe(true)
  })

  it('reports dnn_probe_failed on a non-zero exit', async () => {
    const child = fakeChild((c) => c.emit('close', 3))
    const result = await probeDnn({
      candidate: candidate(),
      home: tempDir(),
      spawn: spawnWith(child),
      resolveInterpreter: () => pythonInterpreter,
    })
    expect(result.ok).toBe(false)
    expect(result.code).toBe('dnn_probe_failed')
    expect(result.message).toContain('3')
  })

  it('reports dnn_probe_failed on unparseable stdout or a spawn error', async () => {
    const garbage = fakeChild((c) => {
      c.stdout.emit('data', 'hello')
      c.emit('close', 0)
    })
    const first = await probeDnn({
      candidate: candidate(),
      home: tempDir(),
      spawn: spawnWith(garbage),
      resolveInterpreter: () => pythonInterpreter,
    })
    expect(first.code).toBe('dnn_probe_failed')

    const broken = fakeChild()
    const spawn = spawnWith(broken)
    const pending = probeDnn({
      candidate: candidate(),
      home: tempDir(),
      spawn,
      resolveInterpreter: () => pythonInterpreter,
    })
    broken.emit('error', new Error('ENOENT'))
    const second = await pending
    expect(second.code).toBe('dnn_probe_failed')
    expect(second.message).toContain('ENOENT')
  })

  it('fails with a structured check when no interpreter can be resolved', async () => {
    const home = tempDir()
    const spawn = spawnWith(fakeChild())
    const result = await probeDnn({
      candidate: candidate(),
      home,
      env: {},
      spawn,
      resolveInterpreter: () => null,
    })
    expect(result.ok).toBe(false)
    expect(result.checks.find((c) => c.id === 'interpreter')?.status).toBe('fail')
    expect(spawn.calls).toHaveLength(0)
  })

  it('warns without failing when the candidate has no model or DNN is disabled', async () => {
    const home = tempDir()
    const child = fakeChild((c) => {
      c.stdout.emit(
        'data',
        JSON.stringify(
          pythonResult([
            { id: 'models_dir', label: 'models 目录', status: 'warn', detail: '目录不存在' },
            { id: 'models_yml', label: 'models.yml', status: 'warn', detail: '未找到' },
          ]),
        ),
      )
      c.emit('close', 0)
    })
    const result = await probeDnn({
      candidate: candidate({ features: { dnn: false } }),
      home,
      spawn: spawnWith(child),
      resolveInterpreter: () => pythonInterpreter,
    })
    expect(result.ok).toBe(true)
    expect(result.summary.warn).toBe(2)
    expect(result.summary.fail).toBe(0)
  })

  it('does not write runtime.json, models or worker state during a probe', async () => {
    const home = tempDir()
    const before = fs.readdirSync(home)
    const child = fakeChild((c) => {
      c.stdout.emit('data', JSON.stringify(pythonResult()))
      c.emit('close', 0)
    })
    await probeDnn({
      candidate: candidate(),
      home,
      spawn: spawnWith(child),
      resolveInterpreter: () => pythonInterpreter,
    })
    expect(fs.readdirSync(home)).toEqual(before)
    expect(fs.existsSync(path.join(home, 'vision', 'runtime.json'))).toBe(false)
    expect(fs.existsSync(path.join(home, 'vision', 'models'))).toBe(false)
  })

  it('resolves a managed venv interpreter when no custom venv or python is set', async () => {
    const home = tempDir()
    const exe =
      process.platform === 'win32'
        ? path.join(home, 'vision', 'venv', 'Scripts', 'python.exe')
        : path.join(home, 'vision', 'venv', 'bin', 'python')
    fs.mkdirSync(path.dirname(exe), { recursive: true })
    fs.writeFileSync(exe, '')
    const child = fakeChild((c) => {
      c.stdout.emit('data', JSON.stringify(pythonResult()))
      c.emit('close', 0)
    })
    const spawn = spawnWith(child)
    const result = await probeDnn({
      candidate: candidate(),
      home,
      env: {},
      spawn,
      which: () => null,
    })
    expect(result.ok).toBe(true)
    const [command] = spawn.calls[0] as [string, string[]]
    expect(command).toBe(exe)
  })

  it('gives the child only a whitelisted environment, never the host secrets', async () => {
    const child = fakeChild((c) => {
      c.stdout.emit('data', JSON.stringify(pythonResult()))
      c.emit('close', 0)
    })
    const spawn = spawnWith(child)
    const result = await probeDnn({
      candidate: candidate(),
      home: tempDir(),
      env: {
        PATH: '/usr/bin',
        HOME: '/home/u',
        USERPROFILE: 'C:/Users/u',
        SYSTEMROOT: 'C:/Windows',
        TEMP: '/tmp',
        TMPDIR: '/tmp',
        PYTHONHOME: '/opt/python',
        HTTP_PROXY: 'http://proxy:8080',
        HTTPS_PROXY: 'http://proxy:8080',
        NO_PROXY: 'localhost',
        OPENAI_API_KEY: 'sk-secret',
        GITHUB_TOKEN: 'ghp-secret',
        AWS_SECRET_ACCESS_KEY: 'aws-secret',
        DSH_BLENDER_PLUGIN_CONFIG: '{"vision":{"python":"x"}}',
        MY_RANDOM_SECRET: 'nope',
      },
      spawn,
      resolveInterpreter: () => pythonInterpreter,
    })
    expect(result.ok).toBe(true)
    const [, , options] = spawn.calls[0] as [string, string[], { env: Record<string, string> }]
    const env = options.env
    expect(env.PATH).toBe('/usr/bin')
    expect(env.HOME).toBe('/home/u')
    expect(env.USERPROFILE).toBe('C:/Users/u')
    expect(env.SYSTEMROOT).toBe('C:/Windows')
    expect(env.TEMP).toBe('/tmp')
    expect(env.PYTHONHOME).toBe('/opt/python')
    expect(env.HTTP_PROXY).toBe('http://proxy:8080')
    expect(env.HTTPS_PROXY).toBe('http://proxy:8080')
    expect(env.NO_PROXY).toBe('localhost')
    for (const secret of [
      'OPENAI_API_KEY',
      'GITHUB_TOKEN',
      'AWS_SECRET_ACCESS_KEY',
      'DSH_BLENDER_PLUGIN_CONFIG',
      'MY_RANDOM_SECRET',
      'TMPDIR',
    ]) {
      expect(env).not.toHaveProperty(secret)
    }
    expect(Object.values(env).join(' ')).not.toContain('sk-secret')
  })

  it('turns an async stdin EPIPE into dnn_probe_failed and kills the child', async () => {
    const child = fakeChild()
    const pending = probeDnn({
      candidate: candidate(),
      home: tempDir(),
      spawn: spawnWith(child),
      resolveInterpreter: () => pythonInterpreter,
    })
    expect(child.stdinError).toBeTypeOf('function')
    child.stdinError?.(new Error('EPIPE: broken pipe'))
    const result = await pending
    expect(result.ok).toBe(false)
    expect(result.code).toBe('dnn_probe_failed')
    expect(result.message).toContain('EPIPE')
    expect(child.killed).toBe(true)
  })

  it('warns when the candidate features.dnn contradicts the DSH_BLENDER_DNN lock', async () => {
    const child = fakeChild((c) => {
      c.stdout.emit('data', JSON.stringify(pythonResult()))
      c.emit('close', 0)
    })
    const result = await probeDnn({
      candidate: candidate(),
      home: tempDir(),
      envLocked: true,
      envDnnValue: false,
      spawn: spawnWith(child),
      resolveInterpreter: () => pythonInterpreter,
    })
    expect(result.ok).toBe(true)
    const feature = result.checks.find((check) => check.id === 'feature')
    expect(feature?.status).toBe('warn')
    expect(feature?.detail).toContain('DSH_BLENDER_DNN')
    expect(result.summary.warn).toBe(1)
  })

  it('passes the feature check when the candidate matches the lock', async () => {
    const child = fakeChild((c) => {
      c.stdout.emit('data', JSON.stringify(pythonResult()))
      c.emit('close', 0)
    })
    const result = await probeDnn({
      candidate: candidate(),
      home: tempDir(),
      envLocked: true,
      envDnnValue: true,
      spawn: spawnWith(child),
      resolveInterpreter: () => pythonInterpreter,
    })
    expect(result.ok).toBe(true)
    expect(result.checks.find((check) => check.id === 'feature')?.status).toBe('pass')
  })
})
