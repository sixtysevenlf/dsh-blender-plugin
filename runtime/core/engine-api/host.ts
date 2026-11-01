// engine API：宿主集成数据源 / start / stop（F1.1 从 server-compat.ts 拆出）
import { extractTag } from '../output/index.js'
import { KERNEL_BOOTSTRAP } from '../kernel/index.js'
import type { EngineApiDeps } from './index.js'

export function createHostApi(E: EngineApiDeps) {
  const { addon, clearWorkerReaper, ensureView, killWorkers } = E
  return {
    async hostSceneItems(spec: Record<string, unknown> = {}) {
      await ensureView()
      const body =
        'print("HOSTITEMS " + K.dsh_view_api["host_scene_items"](' +
        JSON.stringify(JSON.stringify(spec)) +
        '))'
      const info = extractTag(
        await addon.send('execute_code', { code: KERNEL_BOOTSTRAP + '\n' + body }, 60000, {
          noMark: true,
        }),
        'HOSTITEMS',
      ) as Record<string, unknown> | null
      if (!info || info.ok === false)
        throw new Error('scene-items 失败: ' + JSON.stringify(info).slice(0, 300))
      return info
    },
    /**
     * 启动时连上常驻 socket，并**尽力**补一次 `get_addon_info`（不 await、失败即忽略 ——
     * 绝不让"addon 没起来"或"addon 正忙"拖慢后端启动）。
     */
    async start() {
      await addon.ensure()
      addon.send('get_addon_info', {}, 3000).catch(() => null) // 尽力而为，不阻塞 listen
      return true
    },
    // v0.9.4（P1-1）：worker 现在是多实例 —— 关后端时要把**所有**实例收干净（原来只杀单例）
    stop() {
      clearWorkerReaper()
      addon.close()
      killWorkers()
    },
  }
}
