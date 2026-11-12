import { EventEmitter } from 'node:events'
import { PassThrough } from 'node:stream'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import {
  MAX_RESTARTS_PER_WINDOW,
  PROTOCOL_VERSION,
  VisionError,
} from '../../../runtime/vision/protocol.js'
import {
  DEFAULT_DNN_VALUES,
  createRuntimeConfigStore,
  writeRuntimeSnapshot,
} from '../../../runtime/vision/runtime-config.js'
import type { DnnConfigValues } from '../../../runtime/vision/runtime-config.js'
import { createVisionManager, resolveVisionWorkerLaunch } from '../../../runtime/vision/manager.js'
import type {
  VisionChildProcess,
  VisionLaunch,
  VisionManager,
  VisionManagerDeps,
  VisionSpawn,
} from '../../../runtime/vision/manager.js'

// The manager owns exactly one private worker child: lazy start, one in-flight
// set, bounded restart budget, idle reap, and the two-phase config switch.
// Tests inject a fake spawn factory (real child_process pipes are EPERM in this
// sandbox) and keep everything deterministic.

let home: string

beforeEach(() => {
  home = fs.mkdtempSync(path.join(os.tmpdir(), 'dsh-vision-manager-'))
})

afterEach(() => {
  vi.useRealTimers()
  fs.rmSync(home, { recursive: true, force: true })
})

function values(overrides: Partial<DnnConfigValues> = {}): DnnConfigValues {
  return { ...DEFAULT_DNN_VALUES, ...overrides }
}

function writeSnapshot(revision: number, overrides: Partial<DnnConfigValues> = {}): void {
  writeRuntimeSnapshot(home, {
    version: 1,
    revision,
    updatedAt: new Date(revision * 1000).toISOString(),
    values: values(overrides),
  })
}

type FrameHandler = (frame: Record<string, unknown>, child: FakeChild) => void

interface WorkerBehavior {
  ops: Record<string, FrameHandler>
  shutdownExits: boolean
  killExits: boolean
  /** 返回 true 表示接管 hello（例如延迟回复），默认立即应答。 */
  onHello?: (frame: Record<string, unknown>, child: FakeChild) => boolean
}

class FakeChild extends EventEmitter implements VisionChildProcess {
  pid: number
  stdin = new PassThrough()
  stdout = new PassThrough()
  stderr = new PassThrough()
  frames: Record<string, unknown>[] = []
  killed = false
  killExits = true
  exited = false

  constructor(pid: number, onFrame: FrameHandler) {
    super()
    this.pid = pid
    this.stdin.on('data', (buf: Buffer) => {
      for (const line of buf.toString('utf8').split('\n')) {
        if (!line.trim()) continue
        const frame = JSON.parse(line) as Record<string, unknown>
        this.frames.push(frame)
        onFrame(frame, this)
      }
    })
  }

  reply(frame: unknown): void {
    this.stdout.write(JSON.stringify(frame) + '\n')
  }

  exit(code: number | null = 0): void {
    if (this.exited) return
    this.exited = true
    this.emit('exit', code, null)
  }

  crash(): void {
    this.exit(1)
  }

  kill(): boolean {
    this.killed = true
    if (this.killExits) this.exit(null)
    return true
  }
}

interface Harness {
  manager: VisionManager
  spawns: Array<{ command: string; args: string[] }>
  launches: Array<{ values: DnnConfigValues; launch: VisionLaunch }>
  behavior: WorkerBehavior
  child(): FakeChild
  workerSpawnCount(): number
}

function makeHarness(extra: Partial<VisionManagerDeps> = {}): Harness {
  const behavior: WorkerBehavior = { ops: {}, shutdownExits: true, killExits: true }
  const spawns: Array<{ command: string; args: string[] }> = []
  const launches: Array<{ values: DnnConfigValues; launch: VisionLaunch }> = []
  const children: FakeChild[] = []

  const spawnFn: VisionSpawn = (command, args) => {
    spawns.push({ command, args })
    if (command === 'taskkill.exe') {
      const dummy = new FakeChild(0, () => {})
      return dummy
    }
    const child = new FakeChild(1000 + children.length, (frame, c) => {
      if (frame.op === 'hello') {
        if (behavior.onHello?.(frame, c)) return
        c.reply({ id: frame.id, ok: true, result: { protocolVersion: PROTOCOL_VERSION } })
        return
      }
      if (frame.op === 'shutdown') {
        if (behavior.shutdownExits) c.exit(0)
        return
      }
      behavior.ops[String(frame.op)]?.(frame, c)
    })
    child.killExits = behavior.killExits
    children.push(child)
    return child
  }

  const launch = (v: DnnConfigValues): VisionLaunch => {
    const resolved: VisionLaunch = {
      command: 'python',
      args: ['-u', 'main.py'],
      cwd: home,
      env: {},
      python: 'python',
      pythonSource: 'managed',
    }
    launches.push({ values: v, launch: resolved })
    return resolved
  }

  const manager = createVisionManager({
    homeDir: home,
    store: createRuntimeConfigStore({ homeDir: home, env: {} }),
    spawn: spawnFn,
    launch,
    idleReapMs: 10_000,
    shutdownGraceMs: 1_000,
    forceKillWaitMs: 200,
    ...extra,
  })

  return {
    manager,
    spawns,
    launches,
    behavior,
    child(): FakeChild {
      const last = children[children.length - 1]
      if (!last) throw new Error('no worker child was spawned')
      return last
    },
    workerSpawnCount(): number {
      return children.length
    },
  }
}

describe('resolveVisionWorkerLaunch', () => {
  it('prefers an explicit venv, then vision.python, then the managed venv', () => {
    const venvLaunch = resolveVisionWorkerLaunch(
      home,
      values({ venv: 'C:/tools/vision-venv' }),
      {},
      'win32',
    )
    expect(venvLaunch.pythonSource).toBe('venv')
    expect(venvLaunch.command).toContain('vision-venv')

    const pythonLaunch = resolveVisionWorkerLaunch(
      home,
      values({ python: 'C:/Python313/python.exe' }),
      {},
      'win32',
    )
    expect(pythonLaunch.pythonSource).toBe('python')
    expect(pythonLaunch.command).toBe('C:/Python313/python.exe')

    const managed = resolveVisionWorkerLaunch(home, values(), {}, 'win32')
    expect(managed.pythonSource).toBe('managed')
    expect(managed.command).toContain(path.join('vision', 'venv'))
  })

  it('builds a minimal worker environment without host secrets', () => {
    const launch = resolveVisionWorkerLaunch(
      home,
      values(),
      { PATH: 'C:/bin', DSH_BLENDER_SECRET: 's', OPENAI_API_KEY: 'k', PYTHONPATH: 'p' },
      'win32',
    )
    expect(launch.env.PATH).toBe('C:/bin')
    expect(launch.env.DSH_BLENDER_SECRET).toBeUndefined()
    expect(launch.env.OPENAI_API_KEY).toBeUndefined()
    expect(launch.env.PYTHONPATH).toBeUndefined()
    expect(launch.env.PYTHONUTF8).toBe('1')
    expect(launch.env.PYTHONIOENCODING).toBe('utf-8')
    expect(launch.args[0]).toBe('-u')
    expect(launch.args[1].endsWith('main.py')).toBe(true)
    expect(launch.cwd).toBe(home)
  })

  it('rejects a relative vision.venv with a structured config error', () => {
    expect(() =>
      resolveVisionWorkerLaunch(home, values({ venv: 'relative/venv' }), {}, 'win32'),
    ).toThrowError(/绝对|absolute/)
    try {
      resolveVisionWorkerLaunch(home, values({ venv: 'relative/venv' }), {}, 'win32')
    } catch (e) {
      expect((e as InstanceType<typeof VisionError>).code).toBe('vision_custom_env_invalid')
    }
  })
})

describe('createVisionManager lifecycle', () => {
  it('reports status without starting a worker', async () => {
    const h = makeHarness()
    const status = h.manager.status()
    expect(status.worker.alive).toBe(false)
    expect(status.worker.state).toBe('stopped')
    expect(h.spawns).toHaveLength(0)
    expect((await h.manager.reconcile()).status).toBe('idle')
    expect(h.spawns).toHaveLength(0)
  })

  it('starts the worker lazily on the first request and reuses it', async () => {
    const h = makeHarness()
    h.behavior.ops.analyze = (frame, c) =>
      c.reply({ id: frame.id, ok: true, result: { width: 10 } })
    await expect(h.manager.request('analyze', {})).resolves.toEqual({ width: 10 })
    await expect(h.manager.request('analyze', {})).resolves.toEqual({ width: 10 })
    expect(h.workerSpawnCount()).toBe(1)
    const status = h.manager.status()
    expect(status.worker.alive).toBe(true)
    expect(status.worker.protocolVersion).toBe(PROTOCOL_VERSION)
    expect(status.worker.pid).toBe(1000)
    expect(status.inFlight).toBe(0)
  })

  it('maps worker error frames to structured VisionError', async () => {
    const h = makeHarness()
    h.behavior.ops.detect = (frame, c) =>
      c.reply({
        id: frame.id,
        ok: false,
        error: {
          code: 'dnn_no_model',
          stage: 'config',
          message: 'vision.model is empty',
          fix: 'set vision.model',
          retryable: false,
        },
      })
    await expect(h.manager.request('detect', {})).rejects.toMatchObject({
      code: 'dnn_no_model',
      stage: 'config',
      retryable: false,
      message: 'vision.model is empty',
    })
  })

  it('times out a request and stops the worker', async () => {
    const h = makeHarness()
    h.behavior.ops.track = () => {}
    await expect(h.manager.request('track', {}, { timeoutMs: 40 })).rejects.toMatchObject({
      code: 'vision_worker_timeout',
      retryable: true,
    })
    await vi.waitFor(() => expect(h.manager.status().worker.alive).toBe(false))
  })

  it('rejects every pending request when the worker crashes', async () => {
    const h = makeHarness()
    h.behavior.ops.analyze = () => {}
    h.behavior.ops.compare = () => {}
    const first = h.manager.request('analyze', {})
    const second = h.manager.request('compare', {})
    await vi.waitFor(() => expect(h.manager.status().inFlight).toBe(2))
    h.child().crash()
    await expect(first).rejects.toMatchObject({ code: 'vision_worker_crashed' })
    await expect(second).rejects.toMatchObject({ code: 'vision_worker_crashed' })
    expect(h.manager.status().inFlight).toBe(0)
  })

  it('bounds restarts within the window', async () => {
    const h = makeHarness()
    h.behavior.ops.analyze = () => {}
    for (let i = 0; i < MAX_RESTARTS_PER_WINDOW; i++) {
      const pending = h.manager.request('analyze', {})
      await vi.waitFor(() => expect(h.manager.status().inFlight).toBe(1))
      h.child().crash()
      await expect(pending).rejects.toMatchObject({ code: 'vision_worker_crashed' })
    }
    await expect(h.manager.request('analyze', {})).rejects.toMatchObject({
      code: 'vision_worker_restart_limited',
      retryable: false,
    })
    expect(h.workerSpawnCount()).toBe(MAX_RESTARTS_PER_WINDOW)
    // 预算耗尽时：预算耗尽也要写结构化启动错误，doctor/status 才能解释失败原因。
    expect(h.manager.status().worker.lastStartError?.code).toBe('vision_worker_restart_limited')
  })

  it('does not count non-worker-backed ops as in-flight', async () => {
    const h = makeHarness()
    h.behavior.ops.status = () => {}
    const pending = h.manager.request('status', {})
    await vi.waitFor(() =>
      expect(h.child().frames.some((frame) => frame.op === 'status')).toBe(true),
    )
    expect(h.manager.status().inFlight).toBe(0)
    const frame = h.child().frames.find((f) => f.op === 'status') as Record<string, unknown>
    h.child().reply({ id: frame.id, ok: true, result: {} })
    await expect(pending).resolves.toEqual({})
  })

  it('reaps an idle worker', async () => {
    const h = makeHarness({ idleReapMs: 30 })
    h.behavior.ops.analyze = (frame, c) => c.reply({ id: frame.id, ok: true, result: {} })
    await h.manager.request('analyze', {})
    expect(h.manager.status().worker.alive).toBe(true)
    await vi.waitFor(() => expect(h.manager.status().worker.alive).toBe(false))
    expect(h.workerSpawnCount()).toBe(1)
  })

  it('stops the worker on shutdown and rejects pending requests', async () => {
    const h = makeHarness()
    h.behavior.ops.analyze = () => {}
    const pending = h.manager.request('analyze', {})
    await vi.waitFor(() => expect(h.manager.status().inFlight).toBe(1))
    await h.manager.shutdown()
    await expect(pending).rejects.toMatchObject({ code: 'vision_worker_shutdown' })
    expect(h.manager.status().worker.alive).toBe(false)
  })

  it('rejects requests after shutdown instead of touching a closed manager', async () => {
    const h = makeHarness()
    h.behavior.ops.analyze = (frame, c) => c.reply({ id: frame.id, ok: true, result: {} })
    await h.manager.request('analyze', {})
    await h.manager.shutdown()
    await expect(h.manager.request('analyze', {})).rejects.toMatchObject({
      code: 'vision_worker_shutdown',
    })
    expect(h.workerSpawnCount()).toBe(1)
  })

  it('does not spawn a second worker when shutdown races the handshake', async () => {
    const h = makeHarness()
    h.behavior.onHello = () => true
    const pending = h.manager.request('analyze', {})
    await vi.waitFor(() => expect(h.workerSpawnCount()).toBe(1))
    await h.manager.shutdown()
    await expect(pending).rejects.toMatchObject({ code: 'vision_worker_shutdown' })
    expect(h.workerSpawnCount()).toBe(1)
    expect(h.manager.status().worker.state).toBe('stopped')
  })

  it('serializes an idle reap with a new request instead of running two workers', async () => {
    const h = makeHarness({ idleReapMs: 5, shutdownGraceMs: 200, forceKillWaitMs: 200 })
    h.behavior.shutdownExits = false
    h.behavior.killExits = false
    h.behavior.ops.analyze = (frame, c) => c.reply({ id: frame.id, ok: true, result: {} })
    await h.manager.request('analyze', {})
    await vi.waitFor(() => expect(h.manager.status().worker.state).toBe('stopping'))
    // 新请求必须排在 stop 后面，不能把即将被回收的 worker 盖掉。
    await expect(h.manager.request('analyze', {})).resolves.toEqual({})
    expect(h.workerSpawnCount()).toBe(2)
  })
})

describe('createVisionManager hot apply', () => {
  it('commits a model-only change without restarting the worker', async () => {
    const h = makeHarness()
    h.behavior.ops.analyze = (frame, c) => c.reply({ id: frame.id, ok: true, result: {} })
    writeSnapshot(1, { dnn: true, model: 'yolov5s.onnx' })
    expect((await h.manager.reconcile()).status).toBe('committed')
    await h.manager.request('analyze', {})
    writeSnapshot(2, { dnn: true, model: 'yolov6n.onnx' })
    const out = await h.manager.reconcile()
    expect(out.status).toBe('committed')
    expect(out.activeRevision).toBe(2)
    await h.manager.request('analyze', {})
    expect(h.workerSpawnCount()).toBe(1)
    // 热更新：热更新必须随下一个请求送到 worker（只靠 hello 永远拿不到新 model）。
    const frames = h.child().frames.filter((frame) => frame.op === 'analyze')
    expect(frames).toHaveLength(2)
    expect(frames[0].config).toEqual({ revision: 1, model: 'yolov5s.onnx' })
    expect(frames[0].configRevision).toBe(1)
    expect(frames[1].config).toEqual({ revision: 2, model: 'yolov6n.onnx' })
    expect(frames[1].configRevision).toBe(2)
  })

  it('applies a venv change that arrives while the worker is still starting )', async () => {
    const h = makeHarness()
    let heldHellos = 0
    h.behavior.onHello = () => {
      // 只扣住第一次握手（旧 worker）；切换后的新 worker 正常应答。
      if (heldHellos > 0) return false
      heldHellos += 1
      return true
    }
    h.behavior.ops.analyze = (frame, c) => c.reply({ id: frame.id, ok: true, result: {} })
    writeSnapshot(1, { dnn: true, venv: 'C:/venv-a' })
    await h.manager.reconcile()

    const first = h.manager.request('analyze', {})
    await vi.waitFor(() => expect(h.manager.status().worker.state).toBe('starting'))

    writeSnapshot(2, { dnn: true, venv: 'C:/venv-b' })
    const out = await h.manager.reconcile()
    expect(out.status).toBe('pending')
    expect(h.manager.dnnStatus().pendingRevision).toBe(2)
    expect(h.manager.dnnStatus().activeRevision).toBe(1)

    const hello = h.child().frames.find((frame) => frame.op === 'hello')
    expect(hello).toBeTruthy()
    const firstChild = h.child()
    h.child().reply({
      id: (hello as Record<string, unknown>).id,
      ok: true,
      result: { protocolVersion: PROTOCOL_VERSION },
    })
    // 握手结束后 pending 开始生效：这个请求被 correct 地挡在旧配置之外。
    await expect(first).rejects.toMatchObject({ code: 'dnn_config_switching' })

    await vi.waitFor(() => expect(h.manager.dnnStatus().activeRevision).toBe(2))
    expect(
      firstChild.exited || firstChild.killed || h.spawns.some((s) => s.command === 'taskkill.exe'),
    ).toBe(true)
    await h.manager.request('analyze', {})
    expect(h.workerSpawnCount()).toBe(2)
    expect(h.launches[1].values.venv).toBe('C:/venv-b')
  })

  it('does not restart the worker when DNN is turned off', async () => {
    const h = makeHarness()
    h.behavior.ops.analyze = (frame, c) => c.reply({ id: frame.id, ok: true, result: {} })
    writeSnapshot(1, { dnn: true })
    await h.manager.reconcile()
    await h.manager.request('analyze', {})
    writeSnapshot(2, { dnn: false })
    const out = await h.manager.reconcile()
    expect(out.status).toBe('committed')
    expect(out.reason).toBe('dnn')
    await h.manager.request('analyze', {})
    expect(h.workerSpawnCount()).toBe(1)
  })

  it('performs the two-phase switch when python/venv changes', async () => {
    const h = makeHarness()
    h.behavior.ops.analyze = (frame, c) => c.reply({ id: frame.id, ok: true, result: {} })
    writeSnapshot(1, { dnn: true, venv: 'C:/venv-a' })
    await h.manager.reconcile()
    await h.manager.request('analyze', {})
    expect(h.launches[0].values.venv).toBe('C:/venv-a')

    writeSnapshot(2, { dnn: true, venv: 'C:/venv-b' })
    const out = await h.manager.reconcile()
    expect(out.status).toBe('committed')
    expect(out.activeRevision).toBe(2)
    await h.manager.request('analyze', {})
    expect(h.workerSpawnCount()).toBe(2)
    expect(h.launches[1].values.venv).toBe('C:/venv-b')
  })

  it('waits for in-flight requests, reports dnn_config_switching, then switches', async () => {
    const h = makeHarness()
    h.behavior.ops.analyze = (frame, c) =>
      c.reply({ id: frame.id, ok: true, result: { slow: true }, configRevision: 1 })
    writeSnapshot(1, { dnn: true, venv: 'C:/venv-a' })
    await h.manager.reconcile()

    const held: Array<Record<string, unknown>> = []
    h.behavior.ops.compare = (frame) => {
      held.push(frame)
    }
    const slow = h.manager.request('compare', {})
    await vi.waitFor(() => expect(h.manager.status().inFlight).toBe(1))

    writeSnapshot(2, { dnn: true, venv: 'C:/venv-b' })
    const pending = await h.manager.reconcile()
    expect(pending.status).toBe('pending')
    const st = h.manager.dnnStatus()
    expect(st.phase).toBe('pending')
    expect(st.activeRevision).toBe(1)
    expect(st.pendingRevision).toBe(2)
    expect(st.waitingFor?.op).toBe('compare')

    await expect(h.manager.request('analyze', {})).rejects.toMatchObject({
      code: 'dnn_config_switching',
      stage: 'config',
      retryable: true,
      details: expect.objectContaining({ activeRevision: 1, pendingRevision: 2 }),
    })

    h.child().reply({ id: held[0].id, ok: true, result: { done: true } })
    await expect(slow).resolves.toEqual({ done: true })
    await vi.waitFor(() => expect(h.manager.dnnStatus().activeRevision).toBe(2))
    await h.manager.request('analyze', {})
    expect(h.workerSpawnCount()).toBe(2)
  })

  it('keeps a failed switch visible and rejects new requests until a new apply', async () => {
    const h = makeHarness({ shutdownGraceMs: 20, forceKillWaitMs: 20 })
    h.behavior.ops.analyze = (frame, c) => c.reply({ id: frame.id, ok: true, result: {} })
    h.behavior.shutdownExits = false
    h.behavior.killExits = false

    writeSnapshot(1, { dnn: true, venv: 'C:/venv-a' })
    await h.manager.reconcile()
    await h.manager.request('analyze', {})

    writeSnapshot(2, { dnn: true, venv: 'C:/venv-b' })
    const out = await h.manager.reconcile()
    expect(out.status).toBe('failed')
    const failed = h.manager.dnnStatus()
    expect(failed.phase).toBe('failed')
    expect(failed.lastSwitchError?.code).toBe('dnn_config_switch_failed')
    expect(failed.pendingRevision).toBe(2)
    // 切换失败时：失败的切换不能留下一个还在跑的孤儿 worker（Windows 上走 taskkill 进程树）。
    const stopPid = h.child().pid
    expect(
      h.child().killed ||
        h.spawns.some(
          (spawn) => spawn.command === 'taskkill.exe' && spawn.args.includes(String(stopPid)),
        ),
    ).toBe(true)

    await expect(h.manager.request('analyze', {})).rejects.toMatchObject({
      code: 'dnn_config_switch_failed',
    })

    // 显式回滚：新 revision 与 active 相同 -> 立即提交、不再卡在 failed。
    writeSnapshot(3, { dnn: true, venv: 'C:/venv-a' })
    const rolledBack = await h.manager.reconcile()
    expect(rolledBack.status).toBe('committed')
    expect(h.manager.dnnStatus().phase).toBe('idle')
    await h.manager.request('analyze', {})
  })

  it('reports the effective DNN configuration and revisions', async () => {
    const h = makeHarness()
    writeSnapshot(1, {
      dnn: true,
      model: 'yolov5s.onnx',
      backend: 'onnxruntime',
      device: 'gpu',
      venv: 'C:/venv',
    })
    await h.manager.reconcile()
    const st = h.manager.dnnStatus()
    expect(st.features).toEqual({ dnn: true })
    expect(st.dnn).toBe(true)
    expect(st.backend).toBe('onnxruntime')
    expect(st.device).toBe('gpu')
    expect(st.model).toBe('yolov5s.onnx')
    expect(st.venv).toBe('C:/venv')
    expect(st.phase).toBe('idle')
    expect(st.activeRevision).toBe(1)
    expect(st.pendingRevision).toBeNull()
    expect(st.previousRevision).toBeNull()
    expect(st.envLocked).toBe(false)
    expect(st.experimental).toBe(true)
  })
})
