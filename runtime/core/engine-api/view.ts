// engine API：自定义视角捕获
import fs from 'node:fs'
import { winToWsl } from '../../config.js'
import { VIEW_PNG_WIN } from '../../paths.js'
import { extractTag } from '../output/index.js'
import { KERNEL_BOOTSTRAP } from '../kernel/index.js'
import type { EngineApiDeps } from './index.js'

export function createViewApi(E: EngineApiDeps) {
  const { addon, ensureView, metrics } = E
  return {
    async view(spec: Record<string, unknown> = {}) {
      await ensureView()
      metrics.views++
      // 未指定输出路径时用配置里的工作目录（保证 Blender 与宿主两端都能访问）
      const spec2 = Object.assign({}, spec || {})
      if (!spec2.path) spec2.path = VIEW_PNG_WIN
      const j = JSON.stringify(spec2)
      const body =
        'import json as _json' +
        '\n' +
        'print("VIEW " + K.dsh_view_api["capture"](_json.dumps(_json.loads(' +
        JSON.stringify(j) +
        '))))'
      const info = extractTag(
        await addon.send(
          'execute_code',
          { code: KERNEL_BOOTSTRAP + '\n' + body },
          Math.max(30000, Number(spec.timeoutMs) || 120000),
        ),
        'VIEW',
      ) as Record<string, unknown> | null
      if (!info || info.ok === false)
        throw new Error('view 失败: ' + JSON.stringify(info).slice(0, 300))
      metrics.lastView = { at: Date.now(), ms: info.ms, mode: info.mode, path: info.path }
      return { png: fs.readFileSync(winToWsl(info.path)), meta: info }
    },
    /**
     * 宿主集成（/host/view/capture，契约 §1.4）：抓**当前 GUI 视口**那一帧。
     * 与 view() 的分工：view() 是"从任意位置看一眼"（自建矩阵、不动用户视口）；
     * 本方法是"用户现在看到的就是这一帧"（矩阵直接取 region_3d）→ 面板显示 + 点击反投影。
     * 零场景/零视口改动；拿不到矩阵时 view.py 回 ok=false（上层据此把 pickable 置 false，不撒谎）。
     */
    /** 宿主集成（/host/scene-items，契约 §1.6）：`@` 提及的数据源（objects/materials/collections）。 */
  }
}
