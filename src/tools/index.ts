/** 15 个模型工具的注册聚合（顺序与 v2.6.2 apply 内原顺序一致；每个工具自带 ctx.effect 键）。 */
import { registerSee } from './see/index.js'
import { registerDo } from './do/index.js'
import { registerWatch } from './watch/index.js'
import { registerCmd } from './cmd.js'
import { registerCommands } from './commands.js'
import { registerLoop } from './loop/index.js'
import { registerPerf } from './perf.js'
import { registerOpt } from './opt.js'
import { registerHeadless } from './headless/index.js'
import { registerPlan } from '../plan/tool.js'
import { registerWorker } from './worker/index.js'
import { registerTxn } from './txn/index.js'
import { registerPreset } from './preset/index.js'
import { registerJob } from './job/index.js'
import { registerViewport } from './viewport/index.js'
import type { HostContext } from '../host/context.js'

export function registerTools(ctx: HostContext, port: number): void {
  // ---------- A. AI 实时交互原语 ----------
  registerSee(ctx, port)
  registerDo(ctx, port)
  registerWatch(ctx, port)
  registerCmd(ctx, port)
  registerCommands(ctx, port)
  registerLoop(ctx, port)
  // ---------- 性能 / 重活 ----------
  registerPerf(ctx, port)
  registerOpt(ctx, port)
  registerHeadless(ctx, port)
  // ---------- 契约层 / 热会话 ----------
  registerPlan(ctx, port)
  registerWorker(ctx, port)
  registerTxn(ctx, port)
  registerPreset(ctx, port)
  registerJob(ctx, port)
  // ---------- 运维 ----------
  registerViewport(ctx, port)
}
