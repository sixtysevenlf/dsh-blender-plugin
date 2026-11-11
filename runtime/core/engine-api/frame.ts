// engine API：截图/渲染性能/对象精简
import fs from 'node:fs'
import { LIVE_PNG_WIN, LIVE_PNG_WSL } from '../../paths.js'
import type { EngineApiDeps } from './index.js'

export function createFrameApi(E: EngineApiDeps) {
  const { addon } = E
  return {
    async frameArea(areaIndex = 0, instanceId = '') {
      // Full-window (see full=true) goes through the pause-safe internal
      // command as well: execute_code is rejected while a human pauses AI.
      const res = (await addon.send(
        'get_viewport_screenshot',
        { area: Number(areaIndex) || 0, filepath: LIVE_PNG_WIN, format: 'png' },
        120000,
        instanceId ? { instanceId } : {},
      )) as Record<string, unknown> | null
      if (res && res.error) throw new Error('区域截图失败: ' + String(res.error))
      const png = fs.readFileSync(LIVE_PNG_WSL)
      return { png: png, meta: res }
    },
    // 渲染性能 perf / 对象精简 opt 是 perfCall 的 HTTP/脚本别名；原先这里只有一层纯转发，
    // 4.4 收口到 core/engine/assemble.ts 的 surface 装配处（本文件只保留 frame/frameArea）。
    /** 取一帧视口 PNG（覆盖写固定文件） */
    async frame(maxSize = 560, instanceId = '') {
      const res = await addon.send(
        'get_viewport_screenshot',
        {
          max_size: maxSize,
          filepath: LIVE_PNG_WIN,
          format: 'png',
        },
        120000,
        instanceId ? { instanceId } : {},
      )
      const resRec = (res || null) as Record<string, unknown> | null
      if (resRec && resRec.error) throw new Error(String(resRec.error))
      const png = fs.readFileSync(LIVE_PNG_WSL)
      return { png: png, meta: res }
    },
    /** 自定义视角捕获：from/look_at/lens/ortho/...（view.py）——不动物体、不动用户视口 */
  }
}
