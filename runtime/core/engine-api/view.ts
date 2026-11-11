// engine API：自定义视角捕获
import fs from 'node:fs'
import { winToWsl } from '../../config.js'
import { VIEW_PNG_WIN } from '../../paths.js'
import type { EngineApiDeps } from './index.js'
import { isPauseError } from './pause.js'

export function createViewApi(E: EngineApiDeps) {
  const { addon, ensureView, metrics } = E
  return {
    async view(spec: Record<string, unknown> = {}, instanceId = '') {
      metrics.views++
      // 未指定输出路径时用配置里的工作目录（保证 Blender 与宿主两端都能访问）
      const spec2 = Object.assign({}, spec || {})
      if (!spec2.path) spec2.path = VIEW_PNG_WIN
      // While AI operations run, keep the shared kernel injected as before.
      // During a human pause execute_code is rejected, so continue with the
      // pause-safe read-only command; if the kernel was never injected the
      // add-on returns kernel_not_loaded and we ask for one resume.
      try {
        await ensureView(instanceId || undefined)
      } catch (error) {
        if (!isPauseError(error)) throw error
      }
      const info = (await addon.send(
        'get_viewport_screenshot',
        { spec: spec2 },
        Math.max(30000, Number(spec.timeoutMs) || 120000),
        instanceId ? { instanceId } : {},
      )) as Record<string, unknown> | null
      if (!info || info.ok === false) {
        if (info && info.code === 'kernel_not_loaded')
          throw new Error(
            'view 失败：只读捕获需要共享内核；请恢复 AI 操作一次注入内核后重试（kernel_not_loaded）',
          )
        throw new Error('view 失败: ' + JSON.stringify(info).slice(0, 300))
      }
      metrics.lastView = { at: Date.now(), ms: info.ms, mode: info.mode, path: info.path }
      return { png: fs.readFileSync(winToWsl(String(info.path))), meta: info }
    },
  }
}
