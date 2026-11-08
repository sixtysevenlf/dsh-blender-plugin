//拉起 GUI Blender（组装：解析 → 代理/干跑 → spawn → 等 Gateway 实例）
import path from 'node:path'
import fs from 'node:fs'
import { CFG, winToWsl, wslToWin } from '../config.js'
import { WIN_TMP, BLENDER_EXE } from '../paths.js'
import { readJsonSafe } from '../proc_env.js'
import { tryLaunchAgent } from './resolve.js'
import type { LaunchOpts } from './resolve.js'
import { buildLaunchPlan, spawnDetachedBlender } from './spawn.js'
import type { LaunchPlan } from './spawn.js'

export type { LaunchOpts } from './resolve.js'
export { ADDON_NAME_HINTS, LAUNCH_BOOT_PY, addonNameHit } from './resolve.js'

/** 3.1 launch deps: readiness is a Gateway instance count, never an addon TCP port. */
export interface LaunchDeps {
  instanceCount?: () => number
}

interface LaunchContext {
  started: number
  waitMs: number
  exe: string
  steps: string[]
  statusWsl: string
  base: Record<string, unknown>
  instanceCount?: () => number
}

function resolveLaunchContext(opts: LaunchOpts, deps: LaunchDeps): LaunchContext {
  const started = Date.now()
  const waitMs = Math.max(
    2000,
    Math.min(600000, Number(opts.waitMs || process.env.DSH_BLENDER_LAUNCH_WAIT_MS) || 90000),
  )
  const exe = String(opts.exe || BLENDER_EXE || '')
  const steps: string[] = []
  const statusWsl = path.join(winToWsl(WIN_TMP), 'launch-status.json')
  const base = {
    exe: exe || null,
    waitMs: waitMs,
    statusFile: statusWsl,
    statusFileWin: wslToWin(statusWsl),
    configFrom: CFG.source ? CFG.source.blenderExe : null,
  }
  return { started, waitMs, exe, steps, statusWsl, base, instanceCount: deps.instanceCount }
}

function readyInstanceCount(ctx: LaunchContext): number {
  try {
    return Math.max(0, Number(ctx.instanceCount ? ctx.instanceCount() : 0) || 0)
  } catch {
    return 0
  }
}

/** Wait until the Gateway reports at least one ready addon instance. */
async function waitForGatewayInstance(ctx: LaunchContext): Promise<boolean> {
  if (!ctx.instanceCount) return false
  while (Date.now() - ctx.started < ctx.waitMs) {
    if (readyInstanceCount(ctx) > 0) return true
    await new Promise((resolve) => setTimeout(resolve, 250))
  }
  return false
}

/** ① Gateway 已看到 addon 实例 → 什么都不做（幂等：agent 反复调用不会堆出第二个 Blender） */
function alreadyLaunchedResult(ctx: LaunchContext): Record<string, unknown> | null {
  const count = readyInstanceCount(ctx)
  if (count === 0) return null
  return Object.assign({}, ctx.base, {
    ok: true,
    already: true,
    launched: false,
    instances: count,
    waitedMs: Date.now() - ctx.started,
    steps: [],
    hint:
      'Gateway 已有 ' +
      String(count) +
      ' 个 ready addon 实例，无需启动（要重启先关掉那个 Blender，或用 force 另起一个）',
  })
}

/** 1.5 优先走 Windows 侧启动代理：原生拉起 ⇒ 0 次互操作调用；就绪由 Gateway 实例表判定 */
async function agentLaunchResult(
  ctx: LaunchContext,
  agentRes: Record<string, unknown>,
): Promise<Record<string, unknown>> {
  const up = await waitForGatewayInstance(ctx)
  const count = readyInstanceCount(ctx)
  const st = ctx.steps.concat([
    up
      ? 'Gateway 已看到 ' +
        String(count) +
        ' 个 ready addon 实例（' +
        String(Date.now() - ctx.started) +
        'ms）'
      : '等待 ' + String(ctx.waitMs) + 'ms Gateway 仍未看到 addon 实例',
  ])
  return Object.assign({}, agentRes, ctx.base, {
    ok: up,
    launched: agentRes.launched !== false,
    already: up,
    listening: up,
    instances: count,
    waitedMs: Date.now() - ctx.started,
    steps: st,
    hint: up
      ? 'GUI Blender 已起来，addon 已连接 Gateway（' +
        String(count) +
        ' 个实例）—— 现在可以用 blender_py / blender_see 了'
      : 'Windows 代理已派发启动，但 Gateway 在 ' +
        String(ctx.waitMs) +
        'ms 内未看到 addon 实例；看 ' +
        String(ctx.base.statusFileWin || '') +
        '（Blender 启动状态）和 /doctor',
  })
}

function missingExeResult(ctx: LaunchContext): Record<string, unknown> {
  return Object.assign({}, ctx.base, {
    ok: false,
    launched: false,
    already: false,
    waitedMs: Date.now() - ctx.started,
    steps: ctx.steps,
    error: '没找到 blender.exe',
    hint: '设 DSH_BLENDER_EXE 或 dsh-blender.config.json 的 blenderExe（见 docs/配置参考.md）',
  })
}

function dryRunResult(ctx: LaunchContext, plan: LaunchPlan): Record<string, unknown> {
  return Object.assign({}, ctx.base, {
    ok: true,
    dryRun: true,
    launched: false,
    already: false,
    args: plan.args,
    bootScript: plan.sp.win,
    steps: ['dryRun：只给出将要执行的命令'],
    hint: '去掉 dry_run 即真正启动',
  })
}

function missingBlenderPathResult(ctx: LaunchContext, exe: string): Record<string, unknown> {
  return Object.assign({}, ctx.base, {
    ok: false,
    launched: false,
    already: false,
    waitedMs: Date.now() - ctx.started,
    steps: ctx.steps,
    error: 'blender.exe 不存在：' + exe,
    hint:
      '设 DSH_BLENDER_EXE 或 dsh-blender.config.json 的 blenderExe（当前值来自 ' +
      String(ctx.base.configFrom) +
      '）',
  })
}

function bootAddonIssue(boot: unknown): Record<string, unknown> | null {
  if (!boot || typeof boot !== 'object') return null
  const issue = (boot as { addon_issue?: unknown }).addon_issue
  if (!issue || typeof issue !== 'object') return null
  return issue as Record<string, unknown>
}

function spawnErrorText(spawnErr: unknown): string {
  return String(
    spawnErr !== null && typeof spawnErr === 'object' && 'message' in spawnErr
      ? (spawnErr as { message?: unknown }).message
      : spawnErr,
  )
}

async function runSpawnedBlender(
  ctx: LaunchContext,
  plan: LaunchPlan,
  exe: string,
): Promise<Record<string, unknown>> {
  const { child, spawnErrRef } = spawnDetachedBlender(exe, plan.args, plan.childEnv)
  let spawnErr = spawnErrRef.value
  const listening = spawnErr ? false : await waitForGatewayInstance(ctx)
  // spawn 的异步 'error' 可能在等待期间才到
  if (!spawnErr) spawnErr = spawnErrRef.value
  if (spawnErr) {
    return Object.assign({}, ctx.base, {
      ok: false,
      launched: false,
      already: false,
      waitedMs: Date.now() - ctx.started,
      steps: ctx.steps,
      pid: child ? child.pid : null,
      error: '起不来 Blender 进程（' + exe + '）：' + spawnErrorText(spawnErr),
      hint: '可能是路径不存在 / 不可执行 / 不是 Windows 可执行文件。设 DSH_BLENDER_EXE 指到真的 blender.exe（WSL 形如 /mnt/d/.../blender.exe）',
    })
  }
  ctx.steps.push(
    '已 spawn ' + exe + ' pid=' + String(child ? child.pid : null) + '（detached，与后端进程解耦）',
  )
  const boot = readJsonSafe(ctx.statusWsl)
  const addonIssue = bootAddonIssue(boot)
  if (listening)
    ctx.steps.push(
      'Gateway 已看到 ' +
        String(readyInstanceCount(ctx)) +
        ' 个 ready addon 实例（' +
        String(Date.now() - ctx.started) +
        'ms）',
    )
  return Object.assign({}, ctx.base, {
    ok: listening,
    launched: true,
    already: false,
    listening: listening,
    instances: readyInstanceCount(ctx),
    pid: child ? child.pid : null,
    waitedMs: Date.now() - ctx.started,
    steps: ctx.steps,
    boot: boot,
    addonIssue: addonIssue,
    bootScript: plan.sp.win,
    args: plan.args,
    hint: listening
      ? 'GUI Blender 已起来，addon 已连接 Gateway —— 现在可以用 blender_py / blender_see 了（写操作记得 blender_session op=lease 拿租约）'
      : addonIssue && addonIssue.fix
        ? 'GUI Blender 已启动，但 addon 不可用（' +
          String(addonIssue.code || 'unknown') +
          '）：' +
          String(addonIssue.fix)
        : '等待 ' +
          String(ctx.waitMs) +
          'ms Gateway 仍未看到 addon 实例：看 statusFile 里的 boot 结论（addon 没被扫到 / 不是 GUI 模式 / exe 起了但崩了）和 /doctor',
  })
}

export async function launchBlender(
  opts: LaunchOpts = {},
  deps: LaunchDeps = {},
): Promise<Record<string, unknown>> {
  const ctx = resolveLaunchContext(opts, deps)
  const already = alreadyLaunchedResult(ctx)
  if (already) return already
  const agentRes = await tryLaunchAgent(opts, ctx.steps, ctx.base)
  if (agentRes) return agentLaunchResult(ctx, agentRes)
  if (!ctx.exe) return missingExeResult(ctx)
  const plan = buildLaunchPlan(opts, ctx.statusWsl)
  if (opts.dryRun) return dryRunResult(ctx, plan)
  // 先做一次能做的存在性检查（/mnt/... 这类 WSL 可见路径；Windows 原生路径查不了，交给下面的异步 error）
  if (ctx.exe.charAt(0) === '/' && !fs.existsSync(ctx.exe))
    return missingBlenderPathResult(ctx, ctx.exe)
  return runSpawnedBlender(ctx, plan, ctx.exe)
}
