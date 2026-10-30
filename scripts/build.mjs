#!/usr/bin/env node
/**
 * scripts/build.mjs —— Phase R 双编译入口（T15a）。
 *
 * 单项目编译：`node scripts/build.mjs host`（src → lib）或 `runtime`（runtime → lib/runtime）。
 * tsc 解析与 `npm run typecheck` 完全同源（scripts/typecheck.mjs --print）——构建/类型检查
 * 不会因为找编译器的方式不同而出现"两个真相"。
 *
 * build.sh 负责顺序（runtime → host → client，宿主侧 import 构建后的 lib/runtime）与可选的
 * DSH_CHECKOUT 依赖链接；本脚本只做一步。
 */
import fs from 'node:fs'
import { spawnSync } from 'node:child_process'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const ROOT = path.resolve(HERE, '..')

const TARGETS = {
  host: { config: 'tsconfig.json', label: 'host tsc (src → lib)' },
  runtime: { config: 'tsconfig.runtime.json', label: 'runtime tsc (runtime → lib/runtime)' }
}

const target = (process.argv[2] || '').toLowerCase()
if (target !== 'all' && !TARGETS[target]) {
  console.error('build: usage: node scripts/build.mjs <host|runtime|all>')
  process.exit(2)
}

const printed = spawnSync(process.execPath, [path.join(HERE, 'typecheck.mjs'), '--print'], {
  cwd: ROOT,
  encoding: 'utf8',
  env: process.env
})
if (printed.status !== 0) {
  console.error(String(printed.stderr || printed.stdout || '').trim())
  process.exit(2)
}
const tsc = (printed.stdout || '').trim()

function compile(name) {
  const spec = TARGETS[name]
  console.log('build: ' + spec.label + ' · tsc=' + tsc)
  const r = spawnSync(process.execPath, [tsc, '-p', path.join(ROOT, spec.config)], {
    cwd: ROOT,
    stdio: 'inherit',
    env: process.env
  })
  return typeof r.status === 'number' ? r.status : 1
}

function buildClient() {
  const script = path.join(HERE, 'build-client.mjs')
  console.log('build: client half (src/client → lib/client.js)')
  if (!fs.existsSync(script)) {
    console.log('build: scripts/build-client.mjs 不存在 —— 跳过客户端半边（lib/client.js 不会更新）')
    return 0
  }
  const r = spawnSync(process.execPath, [script], { cwd: ROOT, stdio: 'inherit', env: process.env })
  return typeof r.status === 'number' ? r.status : 1
}

if (target === 'all') {
  // 顺序固定：runtime → host → client；任一步失败立即退出。
  // 为什么 runtime 在前：src/host/backend.ts 与 src/tools/{headless,job,viewport}.ts 的边界 import 指向
  // 构建产物 lib/runtime/*.js（生产布局的真实相对路径），host tsc 需要先有 lib/runtime/*.d.ts 才能解析。
  const steps = [() => compile('runtime'), () => compile('host'), () => buildClient()]
  for (const step of steps) {
    const code = step()
    if (code !== 0) process.exit(code)
  }
  console.log('build: all targets OK')
  process.exit(0)
}

const code = compile(target)
if (code !== 0) process.exit(code)
if (target === 'host') {
  const clientCode = buildClient()
  if (clientCode !== 0) process.exit(clientCode)
}
process.exit(0)
