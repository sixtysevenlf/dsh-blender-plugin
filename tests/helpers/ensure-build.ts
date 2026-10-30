import { spawnSync } from 'node:child_process'
import { PKG_ROOT, pkgPath } from './pkg'

/**
 * 同步确保 lib/ 构建产物存在（T15）。
 *
 * runtime 只有 .ts 源码；integration 测试 spawn/fixture 需要 lib/runtime 构建出的 .js。
 * Vitest 的 globalSetup 已经调用过一次，测试文件内再调用是幂等的（mtime 检查）。
 */
export function ensureBuild(): void {
  const result = spawnSync(process.execPath, [pkgPath('scripts', 'ensure-build.mjs')], {
    cwd: PKG_ROOT,
    stdio: 'inherit',
    env: process.env,
  })
  if (result.status !== 0) throw new Error('ensure-build failed with exit code ' + String(result.status))
}
