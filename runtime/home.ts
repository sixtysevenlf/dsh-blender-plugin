/**
 * Persistent data root ("home") for the plug-in.
 *
 * Resolution order is fixed and resolved once per process:
 *   DSH_BLENDER_HOME  ->  $DSH_HOME/dsh-blender  ->  ~/.dsh/dsh-blender
 *
 * Everything the plug-in persists for the user (config, queue, job records,
 * sessions, logs, caches, the vision venv and models) lives under this root.
 * The npm package directory, the Blender install and the OS temp dir are never
 * fallbacks: ensureHomeLayout() reports `home_unwritable` instead of degrading
 * silently, so a read-only disk cannot look like a working install.
 */
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

/** Directory name under $DSH_HOME (or ~/.dsh). */
export const HOME_DIR = 'dsh-blender'
/** Subdirectories created by ensureHomeLayout(). */
export const HOME_LAYOUT_DIRS = [
  'logs',
  'state',
  'jobs',
  'sessions',
  'queue',
  'cache',
  'vision',
] as const
export type HomeLayoutDir = (typeof HOME_LAYOUT_DIRS)[number]
export type HomeSource = 'env' | 'dsh_home' | 'default'

export interface HomeResolution {
  home: string
  source: HomeSource
  /** What produced the value, for doctor output. Never contains a secret. */
  requestedFrom: string
}

export interface HomeUnwritable {
  code: 'home_unwritable'
  path: string
  reason: string
  fix: string
}

export interface HomeLayout {
  ok: true
  home: string
  dirs: Record<HomeLayoutDir, string>
}

export interface HomeLayoutFailure {
  ok: false
  error: HomeUnwritable
}

export type HomeLayoutResult = HomeLayout | HomeLayoutFailure

function cleanEnv(value: string | undefined): string {
  return typeof value === 'string' ? value.trim() : ''
}

/**
 * Pure resolver (testable on any platform). An explicit DSH_BLENDER_HOME wins;
 * otherwise the home hangs off DSH_HOME, whose default is ~/.dsh.
 */
export function resolveHome(
  env: NodeJS.ProcessEnv = process.env,
  homedir: string = os.homedir(),
): HomeResolution {
  const explicit = cleanEnv(env.DSH_BLENDER_HOME)
  if (explicit) {
    return {
      home: path.resolve(explicit),
      source: 'env',
      requestedFrom: 'DSH_BLENDER_HOME=' + explicit,
    }
  }
  const dshHome = cleanEnv(env.DSH_HOME)
  if (dshHome) {
    return {
      home: path.resolve(dshHome, HOME_DIR),
      source: 'dsh_home',
      requestedFrom: 'DSH_HOME=' + dshHome,
    }
  }
  return {
    home: path.join(homedir, '.dsh', HOME_DIR),
    source: 'default',
    requestedFrom: '~/.dsh',
  }
}

let cached: HomeResolution | null = null

/** Resolve once per process; later env changes are ignored by design. */
export function getHomeResolution(): HomeResolution {
  if (!cached) cached = resolveHome()
  return cached
}

export function getHome(): string {
  return getHomeResolution().home
}

/** Test-only seam: drop the process-wide resolution. */
export function resetHomeCache(): void {
  cached = null
}

/** Join a path below home: homePath('vision', 'runtime.json'). */
export function homePath(...segments: string[]): string {
  return path.join(getHome(), ...segments)
}

export function homeConfigFile(): string {
  return homePath('dsh-blender.config.json')
}

export function homeVisionDir(): string {
  return homePath('vision')
}

export function homeVisionModelsDir(): string {
  return homePath('vision', 'models')
}

function writeProbe(dir: string): void {
  const probe = path.join(
    dir,
    '.dsh-blender-write-probe-' + String(process.pid) + '-' + String(Date.now()),
  )
  try {
    fs.writeFileSync(probe, '')
  } finally {
    try {
      fs.unlinkSync(probe)
    } catch {
      /* probe cleanup is best effort */
    }
  }
}

/**
 * Create home and its fixed subdirectories. Idempotent: existing directories
 * and user files are left untouched. Returns a structured home_unwritable
 * failure (never throws, never falls back) when the layout cannot be written.
 */
export function ensureHomeLayout(home: string = getHome()): HomeLayoutResult {
  const target = path.resolve(home)
  try {
    fs.mkdirSync(target, { recursive: true })
    const dirs = {} as Record<HomeLayoutDir, string>
    for (const name of HOME_LAYOUT_DIRS) {
      const dir = path.join(target, name)
      fs.mkdirSync(dir, { recursive: true })
      dirs[name] = dir
    }
    // mkdir on an existing but read-only directory succeeds on POSIX, so the
    // only reliable probe is an actual write in the root.
    writeProbe(target)
    return { ok: true, home: target, dirs }
  } catch (e) {
    const reason = e instanceof Error ? e.message : String(e)
    return {
      ok: false,
      error: {
        code: 'home_unwritable',
        path: target,
        reason,
        fix:
          '把 DSH_BLENDER_HOME（或 DSH_HOME）指向一个可写目录，或修复该目录的写权限后重启插件；' +
          '插件不会把数据写进包目录或系统临时目录。',
      },
    }
  }
}

export interface LegacyMigration {
  from: string
  to: string
  result: 'copied' | 'skipped' | 'missing' | 'error'
  reason?: string
}

/**
 * Copy one legacy state file into home when the target does not exist yet.
 * The legacy file is only read: the old directory is never modified or
 * deleted, so a downgrade keeps working.
 */
export function migrateLegacyFile(from: string, to: string): LegacyMigration {
  try {
    if (fs.existsSync(to)) return { from, to, result: 'skipped' }
    if (!fs.existsSync(from)) return { from, to, result: 'missing' }
    fs.mkdirSync(path.dirname(to), { recursive: true })
    const tmp = to + '.migrating-' + String(process.pid)
    fs.copyFileSync(from, tmp)
    fs.renameSync(tmp, to)
    return { from, to, result: 'copied' }
  } catch (e) {
    return { from, to, result: 'error', reason: e instanceof Error ? e.message : String(e) }
  }
}
