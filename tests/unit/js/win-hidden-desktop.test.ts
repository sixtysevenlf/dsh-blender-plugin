import { EventEmitter } from 'node:events'
import { PassThrough } from 'node:stream'
import type { ChildProcess, SpawnOptions } from 'node:child_process'
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { afterEach, describe, expect, it } from 'vitest'
import { DisplayProviderError } from '../../../runtime/display/errors.js'
import { WinHiddenDesktopProvider } from '../../../runtime/display/win-hidden-desktop.js'
import { resolveHiddenDesktopHelper } from '../../../runtime/display/helper-path.js'

class FakeChild extends EventEmitter {
  pid = 4242
  stdout = new PassThrough()
  stderr = new PassThrough()
  exitCode: number | null = null
  killed = false
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

function fakeSpawn(child: FakeChild, calls: SpawnCall[]) {
  return (command: string, args: readonly string[], options: SpawnOptions): ChildProcess => {
    calls.push({ command, args, options })
    return child as unknown as ChildProcess
  }
}

const dirs: string[] = []

function tempDir(prefix: string): string {
  const dir = mkdtempSync(join(tmpdir(), prefix))
  dirs.push(dir)
  return dir
}

function tempFile(name: string, content = 'fake'): string {
  const file = join(tempDir('dsh-helper-test-'), name)
  writeFileSync(file, content)
  return file
}

afterEach(() => {
  while (dirs.length) rmSync(dirs.pop() as string, { recursive: true, force: true })
})

function helperProvider(
  child: FakeChild,
  overrides: Record<string, unknown> = {},
): { provider: WinHiddenDesktopProvider; calls: SpawnCall[]; blender: string } {
  const calls: SpawnCall[] = []
  const blender = tempFile('blender.exe')
  const provider = new WinHiddenDesktopProvider({
    helperPath: tempFile('dsh-blender-hidden-desktop.exe'),
    blenderExe: blender,
    spawnImpl: fakeSpawn(child, calls),
    readyTimeoutMs: 500,
    stopTimeoutMs: 500,
    ...overrides,
  })
  return { provider, calls, blender }
}

function readyLine(display = 'dsh-vgui-e2e-1-999'): string {
  return JSON.stringify({ event: 'ready', helper: 4242, blender: 777, display, exe: 'blender.exe' })
}

async function nextTick(): Promise<void> {
  await new Promise((resolve) => setImmediate(resolve))
}

describe('WinHiddenDesktopProvider start', () => {
  it('spawns the helper, parses ready, and keeps the relayed output', async () => {
    const child = new FakeChild()
    const lines: string[] = []
    const { provider, calls, blender } = helperProvider(child, {
      onOutput: (line: string) => lines.push(line),
    })
    const started = provider.start({ sessionId: 's 1', blenderArgs: ['--python', 'probe.py'] })
    await nextTick()
    child.stdout.write(readyLine() + '\n')
    child.stdout.write('blender relayed line\n')
    const instance = await started
    await nextTick()
    expect(instance).toMatchObject({
      display: 'dsh-vgui-e2e-1-999',
      pid: 777,
      provider: 'win-hidden-desktop',
    })
    expect(calls).toHaveLength(1)
    expect(calls[0].command).toBeTruthy()
    expect(calls[0].args.slice(0, 6)).toEqual([
      '--exe',
      blender,
      '--desktop',
      expect.stringMatching(/^dsh-vgui-s-1-/),
      '--parent-pid',
      String(process.pid),
    ])
    expect(calls[0].args.slice(-3)).toEqual(['--', '--python', 'probe.py'])
    expect(calls[0].options.windowsHide).toBe(true)
    expect(calls[0].options.env).toMatchObject({ DSHBB_SESSION_KIND: 'virtual-gui' })
    expect(lines.some((line) => line.includes('dsh-vgui-e2e-1-999'))).toBe(true)
    expect(provider.output(instance)?.stdout).toContain('blender relayed line')
    expect((await provider.health(instance)).ok).toBe(true)
  })

  it('rejects a helper error event with its stage, code, and win32 evidence', async () => {
    const child = new FakeChild()
    const { provider } = helperProvider(child)
    const started = provider.start({ sessionId: 's1' })
    await nextTick()
    child.stdout.write(
      JSON.stringify({
        event: 'error',
        stage: 'spawn',
        code: 'desktop_label_failed',
        message: 'cannot lower the desktop label',
        win32: 5,
      }) + '\n',
    )
    await expect(started).rejects.toMatchObject({
      name: 'DisplayProviderError',
      stage: 'spawn',
      code: 'desktop_label_failed',
    })
    expect(child.killed).toBe(true)
  })

  it('fails closed when the helper exits before ready', async () => {
    const child = new FakeChild()
    const { provider } = helperProvider(child)
    const started = provider.start({ sessionId: 's1' })
    await nextTick()
    child.exit(3)
    await expect(started).rejects.toBeInstanceOf(DisplayProviderError)
    await expect(started).rejects.toMatchObject({ code: 'helper_exited' })
  })

  it('times out without ready and terminates the helper', async () => {
    const child = new FakeChild()
    const { provider } = helperProvider(child, { readyTimeoutMs: 30 })
    await expect(provider.start({ sessionId: 's1' })).rejects.toMatchObject({
      code: 'provider_timeout',
    })
    expect(child.killed).toBe(true)
  })
})

describe('WinHiddenDesktopProvider pre-spawn gates', () => {
  it('refuses a missing helper without spawning', async () => {
    const child = new FakeChild()
    const calls: SpawnCall[] = []
    const provider = new WinHiddenDesktopProvider({
      helperPath: null,
      blenderExe: tempFile('blender.exe'),
      spawnImpl: fakeSpawn(child, calls),
    })
    await expect(provider.start({ sessionId: 's1' })).rejects.toMatchObject({
      code: 'helper_missing',
      stage: 'config',
    })
    expect(calls).toHaveLength(0)
  })

  it('refuses a missing Blender executable without spawning', async () => {
    const child = new FakeChild()
    const calls: SpawnCall[] = []
    const provider = new WinHiddenDesktopProvider({
      helperPath: tempFile('dsh-blender-hidden-desktop.exe'),
      blenderExe: join(tempDir('dsh-missing-blender-'), 'blender.exe'),
      spawnImpl: fakeSpawn(child, calls),
    })
    await expect(provider.start({ sessionId: 's1' })).rejects.toMatchObject({
      code: 'blender_exe_missing',
      stage: 'config',
    })
    expect(calls).toHaveLength(0)
  })

  it('reports provider capabilities from the shared prerequisite table', () => {
    const child = new FakeChild()
    const { provider } = helperProvider(child)
    const capabilities = provider.capabilities()
    expect(capabilities.platform).toBe('win32')
    expect(capabilities.provider).toBe('win-hidden-desktop')
    expect(capabilities.provider_implemented).toBe(true)
  })
})

describe('WinHiddenDesktopProvider stop and health', () => {
  it('stops a live helper, is idempotent, and reports the released handle', async () => {
    const child = new FakeChild()
    const { provider } = helperProvider(child)
    const started = provider.start({ sessionId: 's1' })
    await nextTick()
    child.stdout.write(readyLine() + '\n')
    const instance = await started
    await provider.stop(instance)
    expect(child.killed).toBe(true)
    await expect(provider.stop(instance)).resolves.toBeUndefined()
    expect((await provider.health(instance)).ok).toBe(false)
    expect(provider.output(instance)).toBeNull()
  })

  it('reports a helper that exits after ready as unhealthy', async () => {
    const child = new FakeChild()
    const { provider } = helperProvider(child)
    const started = provider.start({ sessionId: 's1' })
    await nextTick()
    child.stdout.write(readyLine() + '\n')
    const instance = await started
    child.exit(0)
    await nextTick()
    const health = await provider.health(instance)
    expect(health.ok).toBe(false)
    expect(health.detail).toContain('exited with code 0')
  })
})

describe('resolveHiddenDesktopHelper', () => {
  it('prefers an existing env override and fails closed on a missing one', () => {
    const file = tempFile('dsh-blender-hidden-desktop.exe')
    const found = resolveHiddenDesktopHelper({
      env: { DSH_HIDDEN_DESKTOP_HELPER: file },
      pkgRoot: join(tempDir('dsh-no-root-'), 'missing'),
    })
    expect(found).toMatchObject({ path: file, source: 'env:DSH_HIDDEN_DESKTOP_HELPER' })
    const missing = resolveHiddenDesktopHelper({
      env: { DSH_HIDDEN_DESKTOP_HELPER: file + '.missing' },
      pkgRoot: join(tempDir('dsh-no-root-'), 'missing'),
    })
    expect(missing.path).toBeNull()
    expect(missing.detail).toContain('missing file')
  })

  it('falls back to vendor before tools and reports every checked path', () => {
    const root = tempDir('dsh-helper-root-')
    const toolsDir = join(root, 'tools', 'win-hidden-desktop', 'out')
    mkdirSync(toolsDir, { recursive: true })
    const tools = join(toolsDir, 'dsh-blender-hidden-desktop.exe')
    writeFileSync(tools, 'fake')
    const toolsFound = resolveHiddenDesktopHelper({ env: {}, pkgRoot: root })
    expect(toolsFound).toMatchObject({
      path: tools,
      source: 'tools/win-hidden-desktop/out',
    })
    expect(toolsFound.checked).toHaveLength(2)
    const vendorDir = join(root, 'vendor', 'win-hidden-desktop')
    mkdirSync(vendorDir, { recursive: true })
    const vendor = join(vendorDir, 'dsh-blender-hidden-desktop.exe')
    writeFileSync(vendor, 'fake')
    const vendorFound = resolveHiddenDesktopHelper({ env: {}, pkgRoot: root })
    expect(vendorFound).toMatchObject({ path: vendor, source: 'vendor/win-hidden-desktop' })
    expect(vendorFound.checked).toHaveLength(1)
  })
})
