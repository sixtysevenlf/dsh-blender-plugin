/** tui/register/lifecycle：选中 chip、页脚状态两块"持续轮询"接缝（toast 由选中桥排队后在此发送）。 */
import { SELECTION_POLL_MS, STATE_POLL_MS, TOAST_DRAIN_MS } from '../constants.js'
import { footerField } from '../footer.js'
import { getJson } from '../http.js'
import { selectionTick } from '../selection.js'
import { wired } from '../state.js'
import type {
  ChipService,
  TuiContext,
  TuiHostOptions,
  TuiServices,
  SelectionState,
} from '../types.js'

/** 注册一个"宿主 effect 生命周期内按固定间隔跑、不重入"的轮询；返回的清理函数由 c.effect 管理。 */
export function every(c: TuiServices, ms: number, fn: () => Promise<void>, label: string): void {
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

/** ① 选中 → chip（toast 可选：有就提示，没有也照常挂 chip） */
export function registerSelectionSurface(
  host: TuiContext,
  opts: TuiHostOptions,
  tag: string,
): void {
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
}

/** ② 页脚字段（tuiStatus.setField 是 dsh-TUI 较新的接缝：旧版只有 set()，这里不退化成整行状态） */
export function registerStatusSurface(host: TuiContext, opts: TuiHostOptions, tag: string): void {
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
}
