/**
 * Host-side mirror of runtime/home.ts.
 *
 * src/ must not import runtime/ (the build keeps the two layers separate), so
 * the host resolves the same data root with the same rules here:
 *   DSH_BLENDER_HOME  ->  $DSH_HOME/dsh-blender  ->  ~/.dsh/dsh-blender
 * tests/unit/js/dnn-home.test.ts imports both resolvers and asserts they agree
 * for the same env matrix; keep the two files in lockstep.
 *
 * Everything below home/vision (runtime.json, runtime-state.json, models/,
 * GPU-DIY.md) is shared with the Gateway; the host only writes runtime.json and
 * the model files, never runtime-state.json.
 */
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

export const HOME_DIR = 'dsh-blender'
export const GPU_DIY_FILENAME = 'GPU-DIY.md'
/** Same subdirectories as runtime/home.ts HOME_LAYOUT_DIRS; keep the two lists in lockstep. */
export const HOST_HOME_LAYOUT_DIRS = [
  'logs',
  'state',
  'jobs',
  'sessions',
  'queue',
  'cache',
  'vision',
] as const
export type HostHomeLayoutDir = (typeof HOST_HOME_LAYOUT_DIRS)[number]
export const DNN_DOCS_URL =
  'https://github.com/sixtysevenlf/dsh-blender-plugin/blob/main/docs/vision-custom-backend.md'

export type HomeSource = 'env' | 'dsh_home' | 'default'

export interface DnnHomeResolution {
  home: string
  source: HomeSource
  requestedFrom: string
}

function cleanEnv(value: string | undefined): string {
  return typeof value === 'string' ? value.trim() : ''
}

/** Same rules as runtime/home.ts resolveHome(). */
export function resolveDnnHome(
  env: NodeJS.ProcessEnv = process.env,
  homedir: string = os.homedir(),
): DnnHomeResolution {
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

let cached: DnnHomeResolution | null = null

export function getDnnHomeResolution(): DnnHomeResolution {
  if (!cached) cached = resolveDnnHome()
  return cached
}

export function getDnnHome(): string {
  return getDnnHomeResolution().home
}

export function resetDnnHomeCache(): void {
  cached = null
}

export interface HostHomeUnwritable {
  code: 'home_unwritable'
  path: string
  reason: string
  fix: string
}

export interface HostHomeLayout {
  ok: true
  home: string
  dirs: Record<HostHomeLayoutDir, string>
}

export interface HostHomeLayoutFailure {
  ok: false
  error: HostHomeUnwritable
}

export type HostHomeLayoutResult = HostHomeLayout | HostHomeLayoutFailure

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
 * Host-side mirror of runtime/home.ts ensureHomeLayout(). Called from the
 * plugin apply() path so a read-only home surfaces {code:'home_unwritable'}
 * instead of every later writer failing one by one. Idempotent: existing
 * directories and user files are left untouched; never throws.
 */
export function ensureHostHomeLayout(home: string = getDnnHome()): HostHomeLayoutResult {
  const target = path.resolve(home)
  try {
    fs.mkdirSync(target, { recursive: true })
    const dirs = {} as Record<HostHomeLayoutDir, string>
    for (const name of HOST_HOME_LAYOUT_DIRS) {
      const dir = path.join(target, name)
      fs.mkdirSync(dir, { recursive: true })
      dirs[name] = dir
    }
    // mkdir on an existing read-only directory succeeds on POSIX; only a real
    // write proves the root is writable.
    writeProbe(target)
    return { ok: true, home: target, dirs }
  } catch (error) {
    return {
      ok: false,
      error: {
        code: 'home_unwritable',
        path: target,
        reason: error instanceof Error ? error.message : String(error),
        fix:
          '把 DSH_BLENDER_HOME（或 DSH_HOME）指向一个可写目录，或修复该目录的写权限后重启插件；' +
          '插件不会把数据写进包目录或系统临时目录。',
      },
    }
  }
}

export interface DnnPaths {
  home: string
  visionDir: string
  modelsDir: string
  runtimeJson: string
  runtimeState: string
  assetPath: string
  docsUrl: string
  /** Relative location of probe.py inside the package (resolved separately). */
  probeScriptSuffix: string
}

export function dnnPaths(home: string = getDnnHome()): DnnPaths {
  const root = path.resolve(home)
  const visionDir = path.join(root, 'vision')
  const modelsDir = path.join(visionDir, 'models')
  return {
    home: root,
    visionDir,
    modelsDir,
    runtimeJson: path.join(visionDir, 'runtime.json'),
    runtimeState: path.join(visionDir, 'runtime-state.json'),
    assetPath: path.join(modelsDir, GPU_DIY_FILENAME),
    docsUrl: DNN_DOCS_URL,
    probeScriptSuffix: path.join('runtime', 'vision', 'worker', 'probe.py'),
  }
}

/** Absolute probe.py path both in the source tree and in lib/ (both ../.. = package root). */
export function probeScriptPath(): string {
  return fileURLToPath(new URL('../../runtime/vision/worker/probe.py', import.meta.url))
}

export function ensureDnnModelsDir(home: string = getDnnHome()): string {
  const dir = dnnPaths(home).modelsDir
  fs.mkdirSync(dir, { recursive: true })
  return dir
}

export interface GpuDiyResult {
  ok: boolean
  path: string
  created: boolean
  code?: string
  message?: string
}

export interface GpuDiyDeps {
  /** Injected asset text (tests). */
  assetText?: string
  /** Injected asset reader (tests); takes precedence over assetText. */
  readAsset?: () => string
}

function readBundledGpuDiy(): string {
  return fs.readFileSync(new URL('../../runtime/vision/assets/GPU-DIY.md', import.meta.url), 'utf8')
}

/**
 * Copy the bilingual quick-start into <home>/vision/models/GPU-DIY.md when it
 * is missing. An existing file — ours or the user's — is never overwritten.
 */
export function ensureGpuDiyAsset(
  home: string = getDnnHome(),
  deps: GpuDiyDeps = {},
): GpuDiyResult {
  const target = dnnPaths(home).assetPath
  try {
    let body: string
    if (deps.readAsset) body = deps.readAsset()
    else if (typeof deps.assetText === 'string') body = deps.assetText
    else body = readBundledGpuDiy()
    fs.mkdirSync(path.dirname(target), { recursive: true })
    try {
      fs.writeFileSync(target, body, { flag: 'wx' })
      return { ok: true, path: target, created: true }
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === 'EEXIST') {
        return { ok: true, path: target, created: false }
      }
      throw error
    }
  } catch (error) {
    return {
      ok: false,
      path: target,
      created: false,
      code: 'gpu_diy_asset_unavailable',
      message: error instanceof Error ? error.message : String(error),
    }
  }
}
