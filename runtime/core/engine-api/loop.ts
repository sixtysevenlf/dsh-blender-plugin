// engine API：内环 loop_*
import { extractLoop } from '../output/index.js'
import { KERNEL_BOOTSTRAP } from '../kernel/index.js'
import type { EngineApiDeps } from './index.js'

export function createLoopApi(E: EngineApiDeps) {
  const { addon, ensureRunner } = E
  return {
    async loopStart(spec: unknown) {
      await ensureRunner()
      const j = JSON.stringify(spec || {})
      const body =
        'import json as _json' +
        '\n' +
        'print("LOOP " + K.dsh_loop_api["start"](_json.loads(' +
        JSON.stringify(j) +
        ')))'
      return extractLoop(
        await addon.send('execute_code', { code: KERNEL_BOOTSTRAP + '\n' + body }, 60000),
      )
    },
    async loopStatus(history = 8) {
      await ensureRunner()
      const body = 'print("LOOP " + K.dsh_loop_api["status"](' + String(Number(history) || 8) + '))'
      return extractLoop(
        await addon.send('execute_code', { code: KERNEL_BOOTSTRAP + '\n' + body }, 60000),
      )
    },
    async loopStop() {
      await ensureRunner()
      const body = 'print("LOOP " + K.dsh_loop_api["stop"]())'
      return extractLoop(
        await addon.send('execute_code', { code: KERNEL_BOOTSTRAP + '\n' + body }, 60000),
      )
    },
    async loopBench(iterations = 2000) {
      await ensureRunner()
      const body =
        'print("LOOP " + K.dsh_loop_api["bench"](' + String(Number(iterations) || 2000) + '))'
      return extractLoop(
        await addon.send('execute_code', { code: KERNEL_BOOTSTRAP + '\n' + body }, 60000),
      )
    },
    /** 候选表：top_k / 分组（跨对象批量）结果 */
    async loopBoard(limit = 10, groups = true) {
      await ensureRunner()
      const body =
        'print("LOOP " + K.dsh_loop_api["board"](' +
        String(Number(limit) || 10) +
        ', ' +
        (groups === false ? 'False' : 'True') +
        '))'
      return extractLoop(
        await addon.send('execute_code', { code: KERNEL_BOOTSTRAP + '\n' + body }, 60000),
      )
    },
    /** 导出可复用脚本（内嵌 setup 源码 + 最优参数） */
    async loopExport(opts: Record<string, unknown> = {}) {
      await ensureRunner()
      const pathLit = opts.path ? JSON.stringify(String(opts.path)) : 'None'
      const top = String(Number(opts.top) || 1)
      const variants = opts.includeVariants ? 'True' : 'False'
      const note = JSON.stringify(String(opts.note || ''))
      const body =
        'print("LOOP " + K.dsh_loop_api["export"](path=' +
        pathLit +
        ', top=' +
        top +
        ', include_variants=' +
        variants +
        ', note=' +
        note +
        '))'
      return extractLoop(
        await addon.send('execute_code', { code: KERNEL_BOOTSTRAP + '\n' + body }, 120000),
      )
    },
    /** 内环契约速查（spec 字段 + ns 预置 + 辅助函数） */
    async loopHelp() {
      await ensureRunner()
      const body = 'print("LOOP " + K.dsh_loop_api["help"]())'
      return extractLoop(
        await addon.send('execute_code', { code: KERNEL_BOOTSTRAP + '\n' + body }, 60000),
      )
    },
    /** 整窗口/区域截图（screenshot_area 路径，保留原 CLI 通道的 screenshot_area 能力） */
  }
}
