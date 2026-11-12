import { EventEmitter } from 'node:events'
import { PassThrough } from 'node:stream'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { PROTOCOL_VERSION } from '../../../runtime/vision/protocol.js'
import {
  DEFAULT_DNN_VALUES,
  createRuntimeConfigStore,
  writeRuntimeSnapshot,
} from '../../../runtime/vision/runtime-config.js'
import type { DnnConfigValues } from '../../../runtime/vision/runtime-config.js'
import { createVisionManager } from '../../../runtime/vision/manager.js'
import type {
  VisionChildProcess,
  VisionLaunch,
  VisionManager,
  VisionSpawn,
} from '../../../runtime/vision/manager.js'

// DNN hot apply across the real runtime-config store and manager: the worker
// must receive the active model on every request (no restart), and a config
// change that lands during the handshake must not be dropped.

let home: string

beforeEach(() => {
  home = fs.mkdtempSync(path.join(os.tmpdir(), 'dsh-vision-hot-apply-'))
})

afterEach(() => {
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

class FakeWorker extends EventEmitter implements VisionChildProcess {
  pid: number
  stdin = new PassThrough()
  stdout = new PassThrough()
  stderr = new PassThrough()
  frames: Record<string, unknown>[] = []
  exited = false

  constructor(pid: number, onFrame: (frame: Record<string, unknown>, worker: FakeWorker) => void) {
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

  kill(): boolean {
    this.exit(null)
    return true
  }
}

interface Harness {
  manager: VisionManager
  workers: FakeWorker[]
  launches: VisionLaunch[]
}

function makeHarness(holdHello: () => boolean = () => false): Harness {
  const workers: FakeWorker[] = []
  const launches: VisionLaunch[] = []
  const spawnFn: VisionSpawn = (command) => {
    if (command === 'taskkill.exe') return new FakeWorker(0, () => {})
    const worker = new FakeWorker(5000 + workers.length, (frame, self) => {
      if (frame.op === 'hello') {
        if (holdHello()) return
        self.reply({ id: frame.id, ok: true, result: { protocolVersion: PROTOCOL_VERSION } })
        return
      }
      if (frame.op === 'shutdown') {
        self.exit(0)
        return
      }
      self.reply({ id: frame.id, ok: true, result: { op: frame.op } })
    })
    workers.push(worker)
    return worker
  }
  const manager = createVisionManager({
    homeDir: home,
    store: createRuntimeConfigStore({ homeDir: home, env: {} }),
    spawn: spawnFn,
    launch: (v): VisionLaunch => {
      const launch: VisionLaunch = {
        command: 'python',
        args: ['-u', 'main.py'],
        cwd: home,
        env: {},
        python: 'python',
        pythonSource: 'managed',
      }
      launches.push(launch)
      void v
      return launch
    },
    idleReapMs: 60_000,
    shutdownGraceMs: 500,
    forceKillWaitMs: 500,
  })
  return { manager, workers, launches }
}

function requestFrames(worker: FakeWorker): Record<string, unknown>[] {
  return worker.frames.filter((frame) => frame.op !== 'hello' && frame.op !== 'shutdown')
}

describe('vision DNN hot apply', () => {
  it('commits a model-only change and sends the new model on the next request', async () => {
    const h = makeHarness()
    writeSnapshot(1, { dnn: true, model: 'yolov5s.onnx' })
    await h.manager.reconcile()
    await h.manager.request('analyze', {})
    writeSnapshot(2, { dnn: true, model: 'yolov8n.onnx' })
    const out = await h.manager.reconcile()
    expect(out.status).toBe('committed')
    expect(out.needsWorkerRestart).toBe(false)
    await h.manager.request('detect', {})
    expect(h.workers).toHaveLength(1)
    const frames = requestFrames(h.workers[0])
    expect(frames.map((frame) => (frame.config as Record<string, unknown>).model)).toEqual([
      'yolov5s.onnx',
      'yolov8n.onnx',
    ])
    expect(frames.map((frame) => frame.configRevision)).toEqual([1, 2])
    expect(h.launches).toHaveLength(1)
  })

  it('does not drop a venv apply that lands while the worker is starting', async () => {
    let held = 0
    const h = makeHarness(() => {
      if (held > 0) return false
      held += 1
      return true
    })
    writeSnapshot(1, { dnn: true, venv: 'C:/hot-apply-a' })
    await h.manager.reconcile()

    const first = h.manager.request('analyze', {})
    await vi.waitFor(() => expect(h.manager.status().worker.state).toBe('starting'))
    writeSnapshot(2, { dnn: true, venv: 'C:/hot-apply-b' })
    const applied = await h.manager.reconcile()
    // 握手期不能立刻切换：登记 pending，保留旧 active。
    expect(applied.status).toBe('pending')
    expect(h.manager.dnnStatus().activeRevision).toBe(1)
    expect(h.manager.dnnStatus().pendingRevision).toBe(2)

    const hello = h.workers[0].frames.find((frame) => frame.op === 'hello')
    h.workers[0].reply({
      id: (hello as Record<string, unknown>).id,
      ok: true,
      result: { protocolVersion: PROTOCOL_VERSION },
    })
    // 该请求是旧配置的最后一个请求，随后被 pending 挡下；切换本身必须发生。
    await expect(first).rejects.toMatchObject({ code: 'dnn_config_switching' })
    await vi.waitFor(() => expect(h.manager.dnnStatus().activeRevision).toBe(2))
    expect(h.workers[0].exited).toBe(true)

    await h.manager.request('analyze', {})
    expect(h.workers).toHaveLength(2)
    expect(h.launches).toHaveLength(2)
  })

  it('keeps serving with the previous config when the snapshot becomes invalid', async () => {
    const h = makeHarness()
    writeSnapshot(1, { dnn: true, model: 'yolov5s.onnx' })
    await h.manager.reconcile()
    await h.manager.request('analyze', {})

    const file = path.join(home, 'vision', 'runtime.json')
    fs.writeFileSync(file, '{ not json', 'utf8')
    const out = await h.manager.reconcile()
    expect(out.status).toBe('invalid')
    expect(h.manager.dnnStatus().lastSwitchError?.code).toBe('dnn_config_invalid')

    await expect(h.manager.request('analyze', {})).resolves.toEqual({ op: 'analyze' })
    const frames = requestFrames(h.workers[0])
    expect(frames[frames.length - 1].config).toEqual({ revision: 1, model: 'yolov5s.onnx' })
    expect(h.workers).toHaveLength(1)
  })
})
