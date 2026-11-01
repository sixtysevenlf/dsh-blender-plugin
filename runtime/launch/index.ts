// S3-c / F1.1：拉起 GUI Blender（组装：解析 → 代理/干跑 → spawn → 等端口）
import path from 'node:path'
import fs from 'node:fs'
import { CFG, winToWsl, wslToWin } from '../config.js'
import { WIN_TMP, BLENDER_EXE } from '../paths.js'
import { probeTcp } from '../backend_probe.js'
import { readJsonSafe } from '../proc_env.js'
import { tryLaunchAgent } from './resolve.js'
import type { LaunchOpts } from './resolve.js'
import { buildLaunchPlan, spawnDetachedBlender } from './spawn.js'
import type { LaunchPlan } from './spawn.js'
import { waitForAddonPort } from './wait.js'

export type { LaunchOpts } from './resolve.js'
export { ADDON_NAME_HINTS, LAUNCH_BOOT_PY, addonNameHit } from './resolve.js'

interface LaunchContext {
  started: number
  addonPort: number
  waitMs: number
  exe: string
  steps: string[]
  statusWsl: string
  base: Record<string, unknown>
}

function resolveLaunchContext(opts: LaunchOpts): LaunchContext {
  const started = Date.now()
  const addonPort = Math.max(1, Math.min(65535, Number(opts.addonPort || CFG.addonPort) || 9876))
  const waitMs = Math.max(
    2000,
    Math.min(600000, Number(opts.waitMs || process.env.DSH_BLENDER_LAUNCH_WAIT_MS) || 90000),
  )
  const exe = String(opts.exe || BLENDER_EXE || '')
  const steps: string[] = []
  const statusWsl = path.join(winToWsl(WIN_TMP), 'launch-status.json')
  const base = {
    exe: exe || null,
    addonPort: addonPort,
    waitMs: waitMs,
    statusFile: statusWsl,
    statusFileWin: wslToWin(statusWsl),
    configFrom: CFG.source ? CFG.source.blenderExe : null,
  }
  return { started, addonPort, waitMs, exe, steps, statusWsl, base }
}

/** ① 已经在监听 → 什么都不做（幂等：agent 反复调用不会堆出第二个 Blender） */
async function alreadyLaunchedResult(ctx: LaunchContext): Promise<Record<string, unknown> | null> {
  if (!(await probeTcp('127.0.0.1', ctx.addonPort, 800))) return null
  const boot0 = readJsonSafe(ctx.statusWsl)
  return Object.assign({}, ctx.base, {
    ok: true,
    already: true,
    launched: false,
    waitedMs: Date.now() - ctx.started,
    steps: ctx.steps,
    boot: boot0,
    hint:
      'addon 已在本机 ' +
      ctx.addonPort +
      ' 监听，无需启动（要重启先关掉那个 Blender，或用 force 另起一个）',
  })
}

/** 1.5 优先走 Windows 侧启动代理：原生拉起 ⇒ 0 次互操作调用；端口等待由本函数完成 */
async function agentLaunchResult(
  ctx: LaunchContext,
  agentRes: Record<string, unknown>,
): Promise<Record<string, unknown>> {
  let up = !!agentRes.listening
  while (!up && Date.now() - ctx.started < ctx.waitMs) {
    if (await probeTcp('127.0.0.1', ctx.addonPort, 700)) {
      up = true
      break
    }
    await new Promise((r) => setTimeout(r, 500))
  }
  const st = ctx.steps.concat([
    up
      ? 'addon 端口 ' + ctx.addonPort + ' 已监听（' + String(Date.now() - ctx.started) + 'ms）'
      : '等待 ' + String(ctx.waitMs) + 'ms 端口仍未开',
  ])
  return Object.assign({}, agentRes, {
    ok: up,
    listening: up,
    already: up,
    launched: agentRes.launched !== false,
    waitedMs: Date.now() - ctx.started,
    steps: st,
    hint: up
      ? 'GUI Blender 已起来且 addon 已 Connect（端口 ' +
        ctx.addonPort +
        '）—— 现在可以用 rt_do / rt_see 了（Windows 代理拉起，0 次互操作调用）'
      : 'Windows 代理已派发启动，但 addon 端口 ' +
        ctx.addonPort +
        ' 在 ' +
        String(ctx.waitMs) +
        'ms 内未监听；看 ' +
        String(ctx.base.statusFileWin || '') +
        '（Blender 启动状态）',
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
  const listening = spawnErr
    ? false
    : await waitForAddonPort(ctx.started, ctx.waitMs, ctx.addonPort)
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
  if (listening)
    ctx.steps.push(
      'addon 端口 ' + ctx.addonPort + ' 已监听（' + String(Date.now() - ctx.started) + 'ms）',
    )
  return Object.assign({}, ctx.base, {
    ok: listening,
    launched: true,
    already: false,
    listening: listening,
    pid: child ? child.pid : null,
    waitedMs: Date.now() - ctx.started,
    steps: ctx.steps,
    boot: boot,
    bootScript: plan.sp.win,
    args: plan.args,
    hint: listening
      ? 'GUI Blender 已起来且 addon 已 Connect（端口 ' +
        ctx.addonPort +
        '）—— 现在可以用 rt_do / rt_see 了（写操作记得 blender_viewport op=lease 拿租约）'
      : '等待 ' +
        String(ctx.waitMs) +
        'ms 端口仍未开：看 statusFile 里的 boot 结论（addon 没被扫到 / 不是 GUI 模式 / exe 起了但崩了）',
  })
}

export async function launchBlender(opts: LaunchOpts = {}): Promise<Record<string, unknown>> {
  const ctx = resolveLaunchContext(opts)
  const already = await alreadyLaunchedResult(ctx)
  if (already) return already
  const agentRes = await tryLaunchAgent(opts, ctx.addonPort, ctx.steps, ctx.base)
  if (agentRes) return agentLaunchResult(ctx, agentRes)
  if (!ctx.exe) return missingExeResult(ctx)
  const plan = buildLaunchPlan(opts, ctx.statusWsl)
  if (opts.dryRun) return dryRunResult(ctx, plan)
  // 先做一次能做的存在性检查（/mnt/... 这类 WSL 可见路径；Windows 原生路径查不了，交给下面的异步 error）
  if (ctx.exe.charAt(0) === '/' && !fs.existsSync(ctx.exe))
    return missingBlenderPathResult(ctx, ctx.exe)
  return runSpawnedBlender(ctx, plan, ctx.exe)
}
