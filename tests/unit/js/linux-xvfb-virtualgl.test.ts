import { EventEmitter } from 'node:events'
import { PassThrough } from 'node:stream'
import type { ChildProcess, SpawnOptions } from 'node:child_process'
import { describe, expect, it } from 'vitest'
import { DisplayProviderError } from '../../../runtime/display/errors.js'
import { LinuxXvfbVirtualGlProvider } from '../../../runtime/display/linux-xvfb-virtualgl.js'

class FakeChild extends EventEmitter {
  pid = 4100
  stdout = new PassThrough()
  stderr = new PassThrough()
  exitCode: number | null = null
  killed = false
  constructor(pid: number) {
    super()
    this.pid = pid
  }
  kill(): boolean {
    this.killed = true
    this.exit(0)
    return true
  }
  exit(code: number | null): void {
    if (this.exitCode !== null) return
    this.exitCode = code
    this.emit('exit', code, null)
  }
}

interface SpawnCall {
  command: string
  args: readonly string[]
  options: SpawnOptions
}

function spawnQueue(children: FakeChild[], calls: SpawnCall[]) {
  return (command: string, args: readonly string[], options: SpawnOptions): ChildProcess => {
    calls.push({ command, args, options })
    const child = children[calls.length - 1]
    if (!child) throw new Error('unexpected spawn: ' + command)
    return child as unknown as ChildProcess
  }
}

const BLENDER = '/opt/blender/blender'
const XVFB = '/usr/bin/Xvfb'
const VGLRUN = '/usr/bin/vglrun'

function providerWith(
  children: FakeChild[],
  overrides: Record<string, unknown> = {},
): { provider: LinuxXvfbVirtualGlProvider; calls: SpawnCall[] } {
  const calls: SpawnCall[] = []
  const provider = new LinuxXvfbVirtualGlProvider({
    platform: 'linux',
    env: { PATH: '/usr/bin' },
    feature: 'on',
    featureSource: 'file:test',
    xvfbPath: XVFB,
    blenderExe: BLENDER,
    vglrunPath: null,
    fileExists: (file) => file === BLENDER,
    displayReady: async () => true,
    readyGraceMs: 0,
    stopTimeoutMs: 300,
    signalGroup: (child, signal) => {
      ;(child as unknown as FakeChild).kill()
      void signal
    },
    spawnImpl: spawnQueue(children, calls),
    ...overrides,
  })
  return { provider, calls }
}

describe('LinuxXvfbVirtualGlProvider capabilities', () => {
  it('is ready as a preview when only Xvfb and Blender are present', () => {
    const { provider } = providerWith([], {
      checks: { xvfb: true, virtualgl: false, 'gpu-device': false, blender: true },
    })
    const cap = provider.capabilities()
    expect(cap.platform).toBe('linux')
    expect(cap.provider).toBe('linux-xvfb-virtualgl')
    expect(cap.provider_implemented).toBe(true)
    expect(cap.preview).toBe(true)
    expect(cap.hardware_gl).toBe(false)
    expect(cap.ready).toBe(true)
    expect(cap.reason).toBeNull()
    expect(cap.missing).toEqual(expect.arrayContaining(['virtualgl', 'gpu-device']))
    expect(cap.fix).toContain('preview-only')
    expect(cap.fix).toContain('official VirtualGL APT repository')
  })

  it('reports hardware GL only when VirtualGL and /dev/dri are present', () => {
    const { provider } = providerWith([], {
      checks: { xvfb: true, virtualgl: true, 'gpu-device': true, blender: true },
    })
    const cap = provider.capabilities()
    expect(cap.ready).toBe(true)
    expect(cap.hardware_gl).toBe(true)
    expect(cap.missing).toEqual([])
    expect(cap.fix).toBe('')
  })

  it('still fails closed without Xvfb or Blender', () => {
    const { provider } = providerWith([], {
      checks: { xvfb: false, virtualgl: true, 'gpu-device': true, blender: false },
    })
    const cap = provider.capabilities()
    expect(cap.ready).toBe(false)
    expect(cap.reason).toBe('prerequisites_missing')
    expect(cap.missing).toEqual(expect.arrayContaining(['xvfb', 'blender']))
  })
})

describe('LinuxXvfbVirtualGlProvider start', () => {
  it('refuses before spawning when the Linux preview gate is off', async () => {
    const children = [new FakeChild(4101), new FakeChild(4102)]
    const { provider, calls } = providerWith(children, { feature: 'off' })
    const started = provider.start({ sessionId: 's1', instanceId: 'vgui-1' })
    await expect(started).rejects.toBeInstanceOf(DisplayProviderError)
    await expect(started).rejects.toMatchObject({ code: 'feature_disabled', stage: 'config' })
    expect(calls).toEqual([])
  })

  it('refuses before spawning when Xvfb is missing', async () => {
    const children = [new FakeChild(4103)]
    const { provider, calls } = providerWith(children, {
      xvfbPath: undefined,
      env: { PATH: '' },
    })
    await expect(provider.start({ sessionId: 's1' })).rejects.toMatchObject({
      code: 'xvfb_missing',
      stage: 'config',
    })
    expect(calls).toEqual([])
  })

  it('refuses before spawning when Blender is missing', async () => {
    const children = [new FakeChild(4104)]
    const { provider, calls } = providerWith(children, {
      fileExists: () => false,
    })
    await expect(provider.start({ sessionId: 's1' })).rejects.toMatchObject({
      code: 'blender_exe_missing',
      stage: 'config',
    })
    expect(calls).toEqual([])
  })

  it('starts Xvfb and Blender directly in software preview mode', async () => {
    const xvfb = new FakeChild(4110)
    const blender = new FakeChild(4111)
    const { provider, calls } = providerWith([xvfb, blender])
    const instance = await provider.start({ sessionId: 's1', instanceId: 'vgui-abc' })
    expect(instance).toMatchObject({
      display: ':90',
      pid: 4111,
      provider: 'linux-xvfb-virtualgl',
    })
    expect(calls).toHaveLength(2)
    expect(calls[0].command).toBe(XVFB)
    expect(calls[0].args[0]).toBe(':90')
    expect(calls[0].args).toEqual(
      expect.arrayContaining(['-screen', '0', '1280x720x24', '-nolisten', 'tcp', '-ac']),
    )
    expect(calls[1].command).toBe(BLENDER)
    expect(calls[1].args).toEqual([])
    const env = calls[1].options.env as NodeJS.ProcessEnv
    expect(env.DISPLAY).toBe(':90')
    expect(env.XDG_SESSION_TYPE).toBe('x11')
    expect(env.GDK_BACKEND).toBe('x11')
    expect(env.DSHBB_SESSION_KIND).toBe('virtual-gui')
    expect(env.DSHBB_INSTANCE_ID).toBe('vgui-abc')
    expect(env.DSHBB_GATEWAY_PORT).toBeTruthy()
    expect('WAYLAND_DISPLAY' in env).toBe(false)
    expect(provider.output(instance)).toMatchObject({
      hardware_gl: false,
      gl_transport: 'xvfb software GL (preview-only)',
    })
    expect((await provider.health(instance)).ok).toBe(true)
    await provider.stop(instance)
  })

  it('uses vglrun -d egl when VirtualGL and the GPU node are present', async () => {
    const xvfb = new FakeChild(4120)
    const blender = new FakeChild(4121)
    const { provider, calls } = providerWith([xvfb, blender], {
      vglrunPath: VGLRUN,
      gpuReadable: () => true,
    })
    const instance = await provider.start({ sessionId: 's1', blenderArgs: ['--factory-startup'] })
    expect(calls[1].command).toBe(VGLRUN)
    expect(calls[1].args).toEqual(['-d', 'egl', BLENDER, '--factory-startup'])
    expect(provider.output(instance)).toMatchObject({
      hardware_gl: true,
      gl_transport: 'vglrun -d egl',
    })
    await provider.stop(instance)
  })

  it('gives every instance its own display and reuses numbers after stop', async () => {
    const children = [
      new FakeChild(4130),
      new FakeChild(4131),
      new FakeChild(4132),
      new FakeChild(4133),
      new FakeChild(4134),
      new FakeChild(4135),
    ]
    const { provider } = providerWith(children)
    const first = await provider.start({ sessionId: 's1' })
    const second = await provider.start({ sessionId: 's2' })
    expect(first.display).toBe(':90')
    expect(second.display).toBe(':91')
    await provider.stop(first)
    await provider.stop(first)
    const third = await provider.start({ sessionId: 's3' })
    expect(third.display).toBe(':90')
  })

  it('stops both children and reports the handle as unowned afterwards', async () => {
    const xvfb = new FakeChild(4140)
    const blender = new FakeChild(4141)
    const { provider } = providerWith([xvfb, blender])
    const instance = await provider.start({ sessionId: 's1' })
    await provider.stop(instance)
    expect(xvfb.killed).toBe(true)
    expect(blender.killed).toBe(true)
    expect(provider.output(instance)).toBeNull()
    expect((await provider.health(instance)).ok).toBe(false)
    await provider.stop(instance)
  })

  it('reserves a distinct display for concurrent provider instances', async () => {
    const children = [
      new FakeChild(4150),
      new FakeChild(4151),
      new FakeChild(4152),
      new FakeChild(4153),
    ]
    const first = providerWith(children.slice(0, 2), { displayBase: 140 })
    const second = providerWith(children.slice(2, 4), { displayBase: 140 })
    const [a, b] = await Promise.all([
      first.provider.start({ sessionId: 's1' }),
      second.provider.start({ sessionId: 's2' }),
    ])
    expect(a.display).toBe(':140')
    expect(b.display).toBe(':141')
    await Promise.all([first.provider.stop(a), second.provider.stop(b)])
  })

  it('releases the reservation when startup fails before the display is ready', async () => {
    const children = [new FakeChild(4160), new FakeChild(4161), new FakeChild(4162)]
    const failing = providerWith(children.slice(0, 1), {
      displayBase: 150,
      displayReady: async () => {
        throw new Error('display readiness failed')
      },
    })
    await expect(failing.provider.start({ sessionId: 's1' })).rejects.toThrow(
      'display readiness failed',
    )
    const retry = providerWith(children.slice(1), { displayBase: 150 })
    const instance = await retry.provider.start({ sessionId: 's2' })
    expect(instance.display).toBe(':150')
    await retry.provider.stop(instance)
  })
})
