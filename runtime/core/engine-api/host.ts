// engine API：宿主集成数据源 / start / stop
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
     * 启动时连上常驻 socket。
     *
     * 修复（serial tax）：首版这里还发过一次"尽力而为"的未 await `get_addon_info`。
     * AddonClient 的请求是**串行队列**（transport/probes.ts 里 queue.then(run)），这条不等待的
     * 探测会占住队列头最多 3s 超时 —— 紧接着的 /status、/doctor 只能排在它后面，预算被迫
     * 收紧到 3.5s/3s。该探测的结果从未被使用，连接本身由 ensure() 建立；删掉它，串行队列
     * 不再替启动探测付税（与 lifecycle 的失败 diagnose 各司其职，不重复）。
     */
    async start() {
      await addon.ensure()
      return true
    },
    // v0.9.4：worker 现在是多实例 —— 关后端时要把**所有**实例收干净（原来只杀单例）
    stop() {
      clearWorkerReaper()
      addon.close()
      killWorkers()
    },
  }
}
