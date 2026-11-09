/** tui/register/commands：@ 提及、/blender 命令与 dsh-TUI 子命令补全三块。 */
import { createSceneMentionSource } from '../mentions.js'
import { SUBCOMMANDS, blenderCommand } from '../commands.js'
import { wired } from '../state.js'
import type { TuiContext, TuiHostOptions } from '../types.js'

/** ③ @ 提及 */
export function registerMentionsSurface(host: TuiContext, opts: TuiHostOptions, tag: string): void {
  host.inject(['tuiMentions'], (c) => {
    const mentions = c.tuiMentions
    if (!mentions || typeof mentions.register !== 'function') return
    c.effect(() => {
      let dispose: unknown
      const complete = createSceneMentionSource(opts.port)
      // register 对非法 / 重复 / 超额直接抛（与 tuiCommandTrees 同口径）：注册不上就当没有这块
      try {
        dispose = mentions.register({ id: 'blender', title: 'Blender', complete: complete })
      } catch {
        dispose = undefined
      }
      wired.mentions = typeof dispose === 'function'
      return () => {
        wired.mentions = false
        if (typeof dispose === 'function') dispose()
      }
    }, tag + 'mentions')
  })
}

/** ④ /blender 命令（DSH 核心接缝；Web 也会有）+ dsh-TUI 子命令补全 */
export function registerCommandSurface(host: TuiContext, opts: TuiHostOptions, tag: string): void {
  host.inject(['commands'], (c) => {
    const commands = c.commands
    if (!commands || typeof commands.register !== 'function') return
    c.effect(() => {
      wired.commands = true
      const dispose = commands.register({
        name: 'blender',
        description: 'Blender 通道：status / doctor / jobs / presence',
        input: { hint: 'status | doctor | jobs | presence' },
        handler: (inv) => blenderCommand(inv.rawInput ?? '', opts, inv.signal),
      })
      return () => {
        wired.commands = false
        if (typeof dispose === 'function') dispose()
      }
    }, tag + 'command')
  })
  host.inject(['tuiCommandTrees'], (c) => {
    const trees = c.tuiCommandTrees
    if (!trees || typeof trees.register !== 'function') return
    c.effect(() => {
      wired.commandTree = true
      let dispose: unknown
      try {
        dispose = trees.register({
          root: 'blender',
          children: (path: readonly string[]) => (path.length === 1 ? SUBCOMMANDS : []),
        })
      } catch {
        dispose = undefined
      }
      return () => {
        wired.commandTree = false
        if (typeof dispose === 'function') dispose()
      }
    }, tag + 'command tree')
  })
}
