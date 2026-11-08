/** 模型工具面（3.1）：恰好 6 个工具，一个工具一条产品边界。 */
import type { HostContext } from '../host/context.js'
import { registerSession } from './session/index.js'
import { registerOp } from './op/index.js'
import { registerPy } from './py/index.js'
import { registerSee } from './see/index.js'
import { registerRun } from './run/index.js'
import { registerInspect } from './inspect/index.js'
import { legacyToolHint } from './migration.js'

export function registerTools(ctx: HostContext, port: number): void {
  // 会话 → 算子 → 执行 → 观察 → 作业 → 只读检查
  registerSession(ctx, port)
  registerOp(ctx, port)
  registerPy(ctx, port)
  registerSee(ctx, port)
  registerRun(ctx, port)
  registerInspect(ctx, port)
  // 旧工具名不再注册；预执行 guard 把 UNKNOWN_TOOL 变成「改用哪个工具」的提示。
  ctx.effect(
    () => ctx.tools.guard((exec) => legacyToolHint(exec.name)),
    'dsh-blender-plugin: legacy-tool-migration-hint',
  )
}
