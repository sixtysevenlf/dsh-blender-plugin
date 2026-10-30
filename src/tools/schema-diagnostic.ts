import { vTool } from '../contracts/tool.js'
import { schemaResolve } from '../contracts/tool.js'

export const ANY_SCHEMA: any = { type: 'json' }
export const HINT_KERNEL = '持久内核：K.x = 1 这次写，下次调用还能读到；预置 bpy / math / mathutils / Vector。'

/**
 * v1.0.1+S11（issue #10）：schemastery 解析失败时的**降级出口**。
 * 目的不是"还能干活"，而是**让失败可读、可定位**：占位工具照样出现在工具列表里，
 * 一调它就直接给出试过的名字、每条通道的真实错误与修法 —— 而不是旧版那样
 * "模块载入期 throw → 15 个工具整体消失 + 宿主只回一句 failed to import"。
 */
export function registerSchemaDiagnostic(ctx: any): void {
  const text = [
    '⛔ @dsh-external/dsh-blender-plugin 以**降级模式**加载：schemastery 未能解析，15 个 blender_rt_* 工具都未注册（这是**可读的失败**，不是插件消失）。',
    '原因：' + String(schemaResolve.error || '(未知)'),
    '试过的名字与错误：' + (schemaResolve.tried.length ? schemaResolve.tried.join(' / ') : '(空)'),
    '修法：让宿主能解析到 @deepseek-ai/schemastery 或 schemastery 任一（同一份包的两种发布形态）；若用的是只提供带 scope 名的宿主，请升级到含本修复的版本。',
    '口径：解析顺序 = ESM `await import()` 优先 → createRequire 兜底；顶层不再 throw。'
  ].join(String.fromCharCode(10))
  ctx.effect(
    () =>
      ctx.tools.register(
        vTool({
          name: 'blender_viewport',
          description: '仅诊断：本插件降级加载（schemastery 未解析），工具未注册。调用它只回报原因与修法。',
          parameters: { op: { type: 'string', description: '任意值；只用于触发诊断输出' } },
          output: { schema: { type: 'string' }, render: (_a: unknown, v: unknown) => [{ type: 'text', text: String(v) }] },
          isConcurrencySafe: () => true,
          timeoutMs: 10000,
          async execute() {
            return text
          }
        })
      ),
    '@dsh-external/dsh-blender-plugin: degraded-schema'
  )
}
