/** 15 个模型工具的注册聚合（顺序与 v2.6.2 apply 内原顺序一致；每个工具自带 ctx.effect 键）。 */
import { registerSee } from './see.js'
import { registerDo } from './do.js'
import { registerWatch } from './watch.js'
import { registerCmd } from './cmd.js'
import { registerCommands } from './commands.js'
import { registerLoop } from './loop.js'
import { registerPerf } from './perf.js'
import { registerOpt } from './opt.js'
import { registerHeadless } from './headless.js'
import { registerPlan } from '../plan/tool.js'
import { registerWorker } from './worker.js'
import { registerTxn } from './txn.js'
import { registerPreset } from './preset.js'
import { registerJob } from './job.js'
import { registerViewport } from './viewport.js'

export function registerTools(ctx: any, port: number): void {
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
