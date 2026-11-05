// engine API：截图/渲染性能/对象精简
import fs from 'node:fs'
import { LIVE_PNG_WIN, LIVE_PNG_WSL } from '../../paths.js'
import { KERNEL_BOOTSTRAP } from '../kernel/index.js'
import type { EngineApiDeps } from './index.js'

export function createFrameApi(E: EngineApiDeps) {
  const { addon } = E
  return {
    async frameArea(areaIndex = 0) {
      const code = [
        'import bpy',
        'p = r"' + LIVE_PNG_WIN + '"',
        'scr = bpy.context.screen',
        'areas = list(scr.areas)',
        'idx = min(max(0, int(' + String(Number(areaIndex) || 0) + ')), len(areas) - 1)',
        'a = areas[idx]',
        'win = next(w for w in bpy.context.window_manager.windows if w.screen == scr)',
        'with bpy.context.temp_override(window=win, area=a):',
        '    bpy.ops.screen.screenshot_area(filepath=p)',
        'print("AREA_SHOT " + a.type + " " + p)',
      ].join('\n')
      const out = await addon.send('execute_code', { code: KERNEL_BOOTSTRAP + '\n' + code }, 60000)
      const outRec = (out || null) as Record<string, unknown> | null
      const txt = outRec && typeof outRec.result === 'string' ? outRec.result : ''
      if (!txt.includes('AREA_SHOT')) throw new Error('区域截图失败: ' + txt.slice(0, 200))
      const png = fs.readFileSync(LIVE_PNG_WSL)
      return { png: png, meta: { method: 'screenshot_area', info: txt.trim() } }
    },
    // 渲染性能 perf / 对象精简 opt 是 perfCall 的 HTTP/脚本别名；原先这里只有一层纯转发，
    // 4.4 收口到 core/engine/assemble.ts 的 surface 装配处（本文件只保留 frame/frameArea）。
    /** 取一帧视口 PNG（覆盖写固定文件） */
    async frame(maxSize = 560) {
      const res = await addon.send('get_viewport_screenshot', {
        max_size: maxSize,
        filepath: LIVE_PNG_WIN,
        format: 'png',
      })
      const resRec = (res || null) as Record<string, unknown> | null
      if (resRec && resRec.error) throw new Error(String(resRec.error))
      const png = fs.readFileSync(LIVE_PNG_WSL)
      return { png: png, meta: res }
    },
    /** 自定义视角捕获：from/look_at/lens/ortho/...（view.py）——不动物体、不动用户视口 */
  }
}
