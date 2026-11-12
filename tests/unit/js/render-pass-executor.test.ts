import { afterAll, describe, expect, it, vi } from 'vitest'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { SpawnJobExecutor, buildJobArgs } from '../../../runtime/core/queue/spawn-executor.js'
import type { JobRecord } from '../../../runtime/core/queue/types.js'

// Home resolves once per process: point it at a temp dir before any import runs.
const HOME_DIR = vi.hoisted(() => {
  const base = process.env.TEMP || process.env.TMPDIR || os.tmpdir()
  const dir = String(base).replace(/[\\/]+$/, '') + '/dsh-pass-home-test'
  process.env.DSH_BLENDER_HOME = dir
  return dir
})

afterAll(() => {
  fs.rmSync(HOME_DIR, { recursive: true, force: true })
})

function tempDir(): string {
  return fs.mkdtempSync(path.join(os.tmpdir(), 'dsh-pass-exec-'))
}

function job(params: Record<string, unknown>): JobRecord {
  return {
    id: 'job-passes',
    kind: 'render',
    params: { kind: 'render', ...params },
    state: 'assigned',
    priority: 0,
    slot: 'gpu',
    owner: { hostId: 'h', dshSessionId: 'ds', agentId: 'main' },
    createdAt: 1,
    updatedAt: 1,
    assignedAt: 1,
    startedAt: null,
    finishedAt: null,
    pid: null,
    decision: null,
    result: null,
    artifacts: [],
    logs: { stdout: '', stderr: '' },
    error: null,
    events: [],
  }
}

describe('render passes: fixed executor entry (5.2)', () => {
  it('writes render_passes.py + render-config.json and runs only that entry', () => {
    const dir = tempDir()
    const outdir = path.join(dir, 'out')
    const built = buildJobArgs(
      job({
        file: 'C:/scenes/room.blend',
        passes: ['beauty', 'depth'],
        frames: [1, 2],
        resolution: [64, 48],
        samples: 8,
        engine: 'CYCLES',
        format: 'PNG',
        outdir,
      }),
      dir,
    )
    const scriptPath = path.join(dir, 'render_passes.py')
    const configPath = path.join(dir, 'render-config.json')
    expect(fs.existsSync(scriptPath)).toBe(true)
    expect(fs.existsSync(configPath)).toBe(true)
    const script = fs.readFileSync(scriptPath, 'utf8')
    expect(script).toContain('PASSES_DONE')
    expect(script).toContain('PASSES_ERROR')
    const config = JSON.parse(fs.readFileSync(configPath, 'utf8'))
    expect(config).toMatchObject({
      passes: ['beauty', 'depth'],
      frames: [1, 2],
      outdir,
      resolution: [64, 48],
      samples: 8,
      engine: 'CYCLES',
      format: 'PNG',
    })
    expect(config.manifestVersion).toBeGreaterThanOrEqual(1)
    // -b --factory-startup <file> --python <fixed script> -- <config>
    expect(built.args.slice(0, 3)).toEqual(['-b', '--factory-startup', 'C:/scenes/room.blend'])
    expect(built.args).toContain('--python')
    expect(built.args[built.args.indexOf('--python') + 1]).toBe(scriptPath)
    expect(built.args[built.args.indexOf('--python') + 2]).toBe('--')
    expect(built.args[built.args.length - 1]).toBe(configPath)
    expect(built.args).not.toContain('--python-expr')
    // A Python crash must fail the job, and only the bundled entry may run.
    expect(built.args).toContain('--python-exit-code')
    expect(built.args[built.args.indexOf('--python-exit-code') + 1]).toBe('2')
    expect(built.args).toContain('--disable-autoexec')
    // The pass script owns the frame loop; -a would render without manifests.
    expect(built.args).not.toContain('-a')
    expect(built.outdir).toBe(outdir)
  })

  it('never forwards caller Python into the pass entry', () => {
    const dir = tempDir()
    const built = buildJobArgs(
      job({
        file: 'C:/scenes/room.blend',
        passes: ['depth'],
        outdir: path.join(dir, 'out'),
        script: 'print("caller code")',
        script_file: 'C:/evil/caller.py',
      }),
      dir,
    )
    const script = fs.readFileSync(path.join(dir, 'render_passes.py'), 'utf8')
    expect(script).not.toContain('caller code')
    expect(script).not.toContain('evil')
    expect(built.args).not.toContain('C:/evil/caller.py')
    expect(fs.existsSync(path.join(dir, 'job.py'))).toBe(false)
  })

  it('keeps the legacy render command when passes is absent', () => {
    const dir = tempDir()
    const built = buildJobArgs(
      job({ file: 'C:/scenes/room.blend', frames: [1, 2], outdir: path.join(dir, 'out') }),
      dir,
    )
    expect(built.args).toContain('--python-expr')
    expect(built.args).not.toContain('--python')
    expect(built.args.slice(-5)).toEqual(['-s', '1', '-e', '2', '-a'])
    expect(fs.existsSync(path.join(dir, 'render_passes.py'))).toBe(false)
    expect(fs.existsSync(path.join(dir, 'render-config.json'))).toBe(false)
  })

  it('rejects an empty or malformed pass list instead of silently rendering legacy', async () => {
    const executor = new SpawnJobExecutor({ blenderExe: 'C:/blender.exe', tempRoot: tempDir() })
    for (const passes of [[], ['beauty', 3], ['  ']]) {
      const handle = executor.start(job({ file: 'C:/scenes/room.blend', passes }))
      expect(handle.pid).toBeNull()
      const result = await handle.done
      expect(result.error?.code).toBe('render_config_invalid')
      expect(result.error?.stage).toBe('config')
      expect(result.exitCode).toBeNull()
    }
  })

  it('falls back to a per-job render dir when no outdir or output is given', () => {
    const dir = tempDir()
    const built = buildJobArgs(job({ passes: ['alpha'] }), dir)
    expect(built.outdir).toBe(path.join(HOME_DIR, 'queue', 'staging', 'renders', 'job-passes'))
    expect(fs.existsSync(built.outdir as string)).toBe(true)
    const config = JSON.parse(fs.readFileSync(path.join(dir, 'render-config.json'), 'utf8'))
    expect(config.outdir).toBe(built.outdir)
    fs.rmSync(built.outdir as string, { recursive: true, force: true })
  })
})
