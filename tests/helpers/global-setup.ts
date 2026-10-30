import { execFileSync } from 'node:child_process'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..')

/**
 * Vitest globalSetup（T15）：测试启动前确保 lib/ 构建产物可用。
 *
 * 部分 integration 测试 spawn `lib/runtime/server.js`，contract 测试在干净 Node
 * 进程 import `lib/runtime/config.js`，都不经过 Vite 的 TS 转译。
 */
export default function setup(): void {
  execFileSync(process.execPath, [path.join(ROOT, 'scripts', 'ensure-build.mjs')], {
    cwd: ROOT,
    stdio: 'inherit',
    env: process.env,
  })
}
