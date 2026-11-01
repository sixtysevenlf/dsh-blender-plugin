/** 插件对外接缝装配：dsh-TUI 宿主半边与 15 个模型工具。 */
import { HOLDER } from './constants.js'
import type { HostContext } from './host/context.js'
import { registerTools } from './tools/index.js'
import { registerTuiHost } from './tui/index.js'

export function registerTuiSurface(
  ctx: HostContext,
  port: number,
  cfgIn: Record<string, unknown>,
): void {
  // v2.6.1：dsh-TUI 宿主半边（契约 v2.3 增补 §S6）。各块按接缝是否存在启用；Web / headless 上什么都不做。
  if (cfgIn.tui !== false) registerTuiHost(ctx, { port: port, holder: HOLDER })
}

export function registerToolSurface(ctx: HostContext, port: number): void {
  // ---------- 工具面：15 个工具按 v2.6.2 原顺序注册（各工具的 ctx.effect 键不变） ----------
  registerTools(ctx, port)
}
