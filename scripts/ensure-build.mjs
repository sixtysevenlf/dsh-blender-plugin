#!/usr/bin/env node
/**
 * scripts/ensure-build.mjs —— T15：保证 lib/ 构建产物可用（tests/selftests/E2E 共用）。
 *
 * 为什么需要：runtime 现在只有 .ts 源码，Node 进程无法直接 import。
 *   · Vitest unit/integration 直接 import runtime TS（Vite 转译）；
 *   · integration 测试 spawn 的是 lib/runtime/server.js；
 *   · tests 下的 selftest / e2e 脚本 import lib/runtime 构建出的 .js；
 *   · scripts/check-*.mjs 是普通 Node 脚本，只能读 lib/runtime 构建产物。
 * 这些入口都先调用本模块：lib/ 缺失或比源码旧才重建，并发调用用原子锁目录串行化。
 *
 * 用法：
 *   node scripts/ensure-build.mjs          # 需要时重建
 *   node scripts/ensure-build.mjs --force  # 强制重建
 */
import fs from 'node:fs'
import path from 'node:path'
import { spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const ROOT = path.resolve(HERE, '..')
const LIB = path.join(ROOT, 'lib')
const MARKER = path.join(LIB, '.build-ok')
const LOCK = path.join(LIB, '.build.lock')
const NEED = [
  'lib/index.js',
  'lib/client.js',
  'lib/runtime/server.js',
  'lib/runtime/engine.js',
  'lib/runtime/catalog.js',
  'lib/runtime/config.js',
  'lib/runtime/file_guard.js',
  'lib/runtime/backend_probe.js'
]
const WATCH_DIRS = ['src', 'runtime', 'scripts', 'tests/helpers']
const WATCH_FILES = ['package.json', 'tsconfig.json', 'tsconfig.runtime.json', 'tsconfig.test.json']

function collectTimes(dir, out) {
  if (!fs.existsSync(dir)) return
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    if (entry.name === 'node_modules' || entry.name === '__pycache__' || entry.name === 'venv') continue
    const full = path.join(dir, entry.name)
    if (entry.isDirectory()) collectTimes(full, out)
    else if (/\.(?:ts|mjs|json)$/.test(entry.name)) {
      try {
        out.push(fs.statSync(full).mtimeMs)
      } catch {
        /* 竞态删除可忽略 */
      }
    }
  }
}

function newestSource() {
  const times = []
  for (const rel of WATCH_DIRS) collectTimes(path.join(ROOT, rel), times)
  for (const rel of WATCH_FILES) {
    const full = path.join(ROOT, rel)
    if (fs.existsSync(full)) times.push(fs.statSync(full).mtimeMs)
  }
  return times.length ? Math.max(...times) : 0
}

export function artifactsExist() {
  return NEED.every((rel) => fs.existsSync(path.join(ROOT, rel)))
}

function fresh() {
  if (!artifactsExist() || !fs.existsSync(MARKER)) return false
  try {
    return fs.statSync(MARKER).mtimeMs >= newestSource()
  } catch {
    return false
  }
}

function sleep(ms) {
  Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, ms)
}

function build(log) {
  if (log) console.log('ensure-build: lib/ 缺失或过期，正在运行 node scripts/build.mjs all ...')
  const result = spawnSync(process.execPath, [path.join(HERE, 'build.mjs'), 'all'], {
    cwd: ROOT,
    stdio: 'inherit',
    env: process.env
  })
  if (result.status !== 0) return false
  fs.writeFileSync(MARKER, JSON.stringify({ builtAt: new Date().toISOString() }, null, 2) + '\n')
  return true
}

/**
 * 同步确保 lib/ 可用。返回 true 表示产物齐全。
 * @param {{ force?: boolean, log?: boolean }} [options]
 */
export function ensureBuild(options = {}) {
  const force = Boolean(options.force)
  const log = options.log !== false
  if (!force && fresh()) return true
  fs.mkdirSync(LIB, { recursive: true })
  const deadline = Date.now() + 240000
  let acquired = false
  while (Date.now() < deadline) {
    try {
      fs.mkdirSync(LOCK)
      acquired = true
      break
    } catch (error) {
      if (!error || error.code !== 'EEXIST') throw error
      try {
        if (Date.now() - fs.statSync(LOCK).mtimeMs > 180000) {
          fs.rmSync(LOCK, { recursive: true, force: true })
          continue
        }
      } catch {
        /* 锁刚刚被释放：重试 */
      }
      sleep(250)
    }
  }
  if (!acquired) {
    if (log) console.log('ensure-build: 等待构建锁超时，直接构建')
    return build(log)
  }
  try {
    if (!force && fresh()) return true
    return build(log)
  } finally {
    try {
      fs.rmSync(LOCK, { recursive: true, force: true })
    } catch {
      /* 清理失败不影响结论 */
    }
  }
}

const invokedDirectly = process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)
if (invokedDirectly) process.exit(ensureBuild({ force: process.argv.includes('--force') }) ? 0 : 1)
