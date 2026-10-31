import { SELECTION_POLL_MS, STATE_POLL_MS, TOAST_DRAIN_MS } from './constants.js'
import { footerField } from './footer.js'
import { getJson } from './http.js'
import { createSceneMentionSource } from './mentions.js'
import { selectionTick } from './selection.js'
import { bridge, wired } from './state.js'
import { SUBCOMMANDS, blenderCommand } from './commands.js'
import type {
  ChipService,
  TuiContext,
  TuiHostOptions,
  TuiServices,
  SelectionState,
} from './types.js'

function every(c: TuiServices, ms: number, fn: () => Promise<void>, label: string): void {
  c.effect(() => {
    let busy = false
    const run = () => {
      if (busy) return
      busy = true
      fn()
        .catch(() => {})
        .finally(() => {
          busy = false
        })
    }
    const t = setInterval(run, ms)
    if (t && typeof t.unref === 'function') t.unref()
    run()
    return () => clearInterval(t)
  }, label)
}

/** apply() 调这个；宿主没有对应服务的块永远不会启用。 */
export function registerTuiHost(ctx: unknown, opts: TuiHostOptions): void {
  const host = ctx as TuiContext
  if (!host || typeof host.inject !== 'function') return
  const tag = '@dsh-external/dsh-blender-plugin: tui '

  // ① 选中 → chip（toast 可选：有就提示，没有也照常挂 chip）
  //    dsh-TUI 按**当前异步上下文所属的 activation** 认调用方：toast 只能在 tuiToast 自己的 inject 里调，
  //    所以选中桥只把提示排进队列，由这里的小定时器发出去（实测：跨 inject 直接调会被拒）。
  const toastQueue: Array<[string, string]> = []
  const notify = (txt: string, color: string) => {
    if (wired.toast && toastQueue.length < 5) toastQueue.push([txt, color])
  }
  host.inject(['tuiToast'], (c) => {
    const svc = c.tuiToast
    if (!svc || typeof svc.show !== 'function') return
    c.effect(() => {
      wired.toast = true
      const t = setInterval(() => {
        while (toastQueue.length) {
          const item = toastQueue.shift()
          if (!item) break
          const [txt, color] = item
          try {
            svc.show(txt, { color: color })
          } catch {
            /* toast 只是提示 */
          }
        }
      }, TOAST_DRAIN_MS)
      if (t && typeof t.unref === 'function') t.unref()
      return () => {
        clearInterval(t)
        toastQueue.length = 0
        wired.toast = false
      }
    }, tag + 'toast')
  })
  host.inject(['tuiContextChips'], (c) => {
    const chips = c.tuiContextChips
    if (!chips || typeof chips.attachContext !== 'function') return
    const state: SelectionState = { lastSeq: 0, fails: 0 }
    c.effect(() => {
      wired.chips = true
      return () => {
        wired.chips = false
      }
    }, tag + 'selection bridge flag')
    every(
      c,
      SELECTION_POLL_MS,
      () =>
        selectionTick({
          port: opts.port,
          chips: chips as ChipService,
          notify: notify,
          state: state,
        }),
      tag + 'selection bridge',
    )
  })

  // ② 页脚字段（tuiStatus.setField 是 dsh-TUI 较新的接缝：旧版只有 set()，这里不退化成整行状态）
  host.inject(['tuiStatus'], (c) => {
    const status = c.tuiStatus
    if (!status || typeof status.setField !== 'function') return
    wired.status = true
    let last = ''
    let clear: unknown
    let misses = 0
    every(
      c,
      STATE_POLL_MS,
      async () => {
        const st = await getJson(opts.port, '/host/state')
        // 偶发一次没取到（后端正忙 / 刚重启）不立刻改成「后端未运行」；连续两次才算
        misses = st ? 0 : misses + 1
        if (!st && misses < 2 && last) return
        const f = footerField(st, opts.holder, opts.port)
        const key = f.text + '|' + f.tone + '|' + f.detail
        if (key === last) return
        last = key
        // setField 每次都回一个只清「这一次写入」的 disposer：留最新那个，卸载时用它清
        clear = status.setField('blender', f.text, {
          slot: 'right-head',
          tone: f.tone,
          maxCells: 24,
          minColumns: 60,
          detail: f.detail,
        })
      },
      tag + 'footer field',
    )
    c.effect(
      () => () => {
        wired.status = false
        try {
          if (typeof clear === 'function') clear()
        } catch {
          /* 宿主随 activation 清理 */
        }
      },
      tag + 'footer field clear',
    )
  })

  // ③ @ 提及
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

  // ④ /blender 命令（DSH 核心接缝；Web 也会有）+ dsh-TUI 子命令补全
  host.inject(['commands'], (c) => {
    const commands = c.commands
    if (!commands || typeof commands.register !== 'function') return
    c.effect(() => {
      wired.commands = true
      const dispose = commands.register({
        name: 'blender',
        description: 'Blender 通道：status / doctor / jobs',
        input: { hint: 'status | doctor | jobs' },
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
