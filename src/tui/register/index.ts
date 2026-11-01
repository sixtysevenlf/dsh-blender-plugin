/** tui/register：按宿主 inject 到的服务块挂载各 TUI 半边；宿主没有对应服务的块永远不会启用。 */
import { bridge, wired } from '../state.js'
import type { TuiContext, TuiHostOptions } from '../types.js'
import { registerSelectionSurface, registerStatusSurface } from './lifecycle.js'
import { registerCommandSurface, registerMentionsSurface } from './commands.js'

/** apply() 调这个；宿主没有对应服务的块永远不会启用。 */
export function registerTuiHost(ctx: unknown, opts: TuiHostOptions): void {
  const host = ctx as TuiContext
  if (!host || typeof host.inject !== 'function') return
  const tag = '@dsh-external/dsh-blender-plugin: tui '
  // ①② 选中 chip / 页脚（持续轮询类接缝）
  registerSelectionSurface(host, opts, tag)
  registerStatusSurface(host, opts, tag)
  // ③④ @ 提及 / /blender 命令与子命令补全
  registerMentionsSurface(host, opts, tag)
  registerCommandSurface(host, opts, tag)
}

/** doctor 的一行：TUI 宿主半边接上了哪些接缝（非 dsh-TUI 宿主时说明为什么是空的） */
export function tuiHostSummary(): string {
  const on = (b: boolean) => (b ? '✅' : '—')
  if (!wired.chips && !wired.status && !wired.mentions && !wired.commandTree) {
    return (
      'TUI 宿主半边：未启用（不是 dsh-TUI 宿主，或 dsh-TUI 版本没有这些接缝；模型工具不受影响）' +
      (wired.commands ? ' · /blender 命令 ✅' : '')
    )
  }
  let s =
    'TUI 宿主半边：选中 chip ' +
    on(wired.chips) +
    ' · 页脚 ' +
    on(wired.status) +
    ' · @ 提及 ' +
    on(wired.mentions) +
    ' · /blender ' +
    on(wired.commands)
  if (bridge.lastAt)
    s +=
      ' · 最近一次选中 seq=' +
      String(bridge.lastSeq) +
      (bridge.lastOk ? ' 已挂上' : ' 未挂上（' + String(bridge.lastReason || '') + '）')
  return s
}
