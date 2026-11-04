import fs from 'node:fs'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { pkgPath } from '../../helpers/pkg'

// regression: spawn(null, ...) throws Node's
// `The "file" argument must be of type string. Received null` synchronously,
// before child.on('error') / assertWorkerReady() hints can ever run. workerStart
// must precheck BLENDER_EXE and throw a structured blender_exe_missing error
// with an executable hint (config keys + detection source + Steam path example
// + restart/retry step), and it must do so before any side effect.

const h = vi.hoisted(() => ({
  exe: null as string | null,
  source: 'not-found' as string | null,
  spawnCalls: [] as any[],
}))

vi.mock('node:child_process', async () => {
  const { EventEmitter } = await import('node:events')
  const { PassThrough } = await import('node:stream')
  const spawn = vi.fn((exe: unknown, args: unknown, opts: unknown) => {
    // Mirror Node's own validation: real child_process.spawn(null, ...) throws
    // this TypeError synchronously, before a ChildProcess is ever returned.
    if (typeof exe !== 'string' || !exe) {
      throw new TypeError(
        'The "file" argument must be of type string. Received ' +
          (exe === null ? 'null' : String(typeof exe)),
      )
    }
    const child: any = new EventEmitter()
    child.pid = 5100 + h.spawnCalls.length
    child.exitCode = null
    child.signalCode = null
    child.stdout = new PassThrough()
    child.stderr = new PassThrough()
    child.exe = exe
    child.args = args
    child.spawnOpts = opts
    child.kill = vi.fn()
    h.spawnCalls.push(child)
    return child
  })
  return { spawn, default: { spawn } }
})

vi.mock('../../../runtime/config.js', () => ({
  CFG: {
    workerPort: 9879,
    get source() {
      return { blenderExe: h.source }
    },
  },
  winToWsl: (p: unknown) => String(p),
  wslToWin: (p: unknown) => String(p),
}))

vi.mock('../../../runtime/paths.js', () => ({
  get BLENDER_EXE() {
    return h.exe
  },
  WIN_TMP: 'C:/fake/tmp',
  WORKER_PATH: 'C:/fake/worker.py',
  USER_CONFIG_WIN: null,
  USER_SCRIPTS_WIN: null,
}))

vi.mock('../../../runtime/core/kernel/index.js', () => ({
  KERNEL_BOOTSTRAP: '# kernel bootstrap',
}))

async function loadWorkerStart(
  exe: string | null,
  source: string | null = 'not-found',
): Promise<typeof import('../../../runtime/execution/workers/worker-start.js')> {
  h.exe = exe
  h.source = source
  h.spawnCalls.length = 0
  vi.resetModules()
  return await import('../../../runtime/execution/workers/worker-start.js')
}

function workCtx(): any {
  return {
    metrics: { workerStarts: 0, workerExecs: 0 },
    preloadChunk: (name: string) => '# preload ' + name,
    WORKERS: new Map(),
    WORKER_PORT: 9879,
  }
}

async function flush(n = 40): Promise<void> {
  for (let i = 0; i < n; i += 1) await Promise.resolve()
}

afterEach(() => {
  h.exe = null
  h.source = 'not-found'
  h.spawnCalls.length = 0
})

describe('assertBlenderExe precheck', () => {
  it('rejects a null exe with blender_exe_missing and an executable hint', async () => {
    const { assertBlenderExe } = await loadWorkerStart(null, 'not-found')
    let err: any = null
    try {
      assertBlenderExe(null, 'not-found')
    } catch (e) {
      err = e
    }
    expect(err).toBeInstanceOf(Error)
    expect(err.code).toBe('blender_exe_missing')
    const msg = String(err.message)
    expect(msg).toContain('not-found')
    expect(msg).not.toContain('The "file" argument')
    const hint = String(err.hint)
    expect(hint).toContain('DSH_BLENDER_EXE')
    expect(hint).toContain('dsh-blender.config.json')
    expect(hint).toContain('blender.exe')
    expect(hint).toMatch(/steamapps/)
    expect(hint).toContain('op=start')
  })

  it('treats blank/undefined exe as missing and accepts a configured path', async () => {
    const { assertBlenderExe } = await loadWorkerStart(null)
    expect(() => assertBlenderExe(undefined, 'not-found')).toThrow(/Blender/)
    expect(() => assertBlenderExe('   ', 'not-found')).toThrow(/Blender/)
    expect(() =>
      assertBlenderExe('C:/Program Files/Blender Foundation/Blender/blender.exe', 'config'),
    ).not.toThrow()
  })
})

describe('workerStart exe guard', () => {
  it('fails fast with the structured code and never calls spawn when BLENDER_EXE is null', async () => {
    const { workerStart } = await loadWorkerStart(null, 'not-found')
    const ctx = workCtx()
    let err: any = null
    try {
      await workerStart(ctx, { warmup: false })
    } catch (e) {
      err = e
    }
    expect(err && err.code).toBe('blender_exe_missing')
    expect(String(err.message)).toContain('not-found')
    expect(String(err.message)).not.toContain('The "file" argument')
    expect(h.spawnCalls).toHaveLength(0)
    expect(ctx.WORKERS.size).toBe(1)
    expect(ctx.WORKERS.get('default')?.child).toBeNull()
  })

  it('still spawns normally when BLENDER_EXE is configured', async () => {
    const { workerStart } = await loadWorkerStart('C:/fake/blender.exe', 'config')
    const ctx = workCtx()
    const started: Promise<any> = workerStart(ctx, { warmup: false })
    expect(h.spawnCalls).toHaveLength(1)
    const child = h.spawnCalls[0]
    expect(child.exe).toBe('C:/fake/blender.exe')
    expect(child.args).toEqual(
      expect.arrayContaining([
        '-b',
        '--factory-startup',
        '--python',
        'C:/fake/worker.py',
        '--port',
        '9879',
      ]),
    )
    child.stdout.write('DSH_WORKER ' + JSON.stringify({ ok: true, pid: 4242 }) + '\n')
    await flush()
    const snap: any = await started
    expect(snap.ready).toBe(true)
    expect(snap.pid).toBe(4242)
  })

  it('guards after the live-worker early return and before boot-file writing / spawn', () => {
    // 源码顺序是防御性断言（行为断言在上面用了 mock spawn）；用正则容忍格式/参数变化，
    // 不要绑死某一行原文——否则重命名局部变量就会误报。
    const src = fs.readFileSync(pkgPath('runtime/execution/workers/worker-start.ts'), 'utf8')
    const earlyReturn =
      /if\s*\(\s*workerAlive\(w\)\s*&&\s*w\.ready\s*\)\s*return\s+workerSnapshot\(w\)/.exec(src)
    const guard = /assertBlenderExe\(\s*BLENDER_EXE\s*,\s*blenderExeSource\(\)\s*\)/.exec(src)
    const bootFile = /const\s+bootWin\s*=\s*prepareBootFile\(/.exec(src)
    const spawn = /spawn\(\s*BLENDER_EXE\s+as\s+string/.exec(src)
    for (const m of [earlyReturn, guard, bootFile, spawn]) expect(m).toBeTruthy()
    expect(guard!.index).toBeGreaterThan(earlyReturn!.index)
    expect(bootFile!.index).toBeGreaterThan(guard!.index)
    expect(spawn!.index).toBeGreaterThan(bootFile!.index)
  })
})
