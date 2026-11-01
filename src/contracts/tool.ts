import { createRequire } from 'node:module'
import { defineTool } from '@deepseek-ai/dsh-tools'
import type { ToolDefinition } from '@deepseek-ai/dsh-tools'
import type zt from '@deepseek-ai/schemastery'
import { PLUGIN_VERSION } from '../constants.js'
import { EXEC_CTX } from '../host/exec-context.js'
import { isRecord } from './dynamic.js'
import { losslessSanitize } from './lossless.js'
import { cardPresenters } from '../ui/cards.js'

type DynamicToolSpec = Record<string, unknown>

/**
 * Wrap one tool definition with the plugin's shared behaviour.
 *
 * Tool definitions are authored as object literals (and share schemastery's
 * dynamically resolved schema), so the boundary is typed as a dynamic record
 * and the final options are narrowed once at the defineTool call. The wrapper
 * keeps the public contract of v2.x: card presenters, version prefix and the
 * lossless sanitize gate on every return value.
 */
export const vTool = (spec: DynamicToolSpec): ToolDefinition => {
  const execute = spec.execute
  if (typeof execute !== 'function') throw new TypeError('tool spec.execute must be a function')
  const presenters = cardPresenters(spec) as DynamicToolSpec
  const options: DynamicToolSpec = {
    ...spec,
    ...presenters,
    description: '[v' + PLUGIN_VERSION + '] ' + String(spec.description || ''),
    // v0.9.4（P0 返回通道）：所有工具**唯一出口**都过一遍 losslessSanitize ——
    // 一个字段写错（NaN / undefined / -0）不该让整条工具通道变成不可用。
    execute: async (args: unknown, exec: unknown) => {
      const signal = isRecord(exec) && exec.signal instanceof AbortSignal ? exec.signal : undefined
      const raw = await EXEC_CTX.run({ signal }, () =>
        (execute as (args: unknown, exec: unknown) => unknown)(args, exec),
      )
      const { value, fixes } = losslessSanitize(raw)
      if (fixes.length) {
        const note =
          '⚠️ 回执已消毒 ' +
          String(fixes.length) +
          ' 处非 lossless-JSON 值（宿主门会拒收 undefined/NaN/Infinity/-0）：' +
          fixes.slice(0, 6).join(' · ') +
          (fixes.length > 6 ? ' …等' : '')
        try {
          if (isRecord(value) && typeof value.text === 'string') {
            value.text += String.fromCharCode(10) + note
          } else if (isRecord(value)) {
            value.__losslessNote = note
          }
          console.warn('[dsh-blender] ' + note)
        } catch {
          /* 提示失败不影响返回 */
        }
      }
      return value
    },
  }
  // The dynamic options record is validated by defineTool at runtime; the broad
  // generic instantiation is only needed to keep the public wrapper ergonomic.
  return defineTool(options as never)
}

/**
 * schemastery 的包名在不同 DSH 版本间是**有 scope / 无 scope 两种形态**（schemastery ↔
 * @deepseek-ai/schemastery；cordis 同理），静态 import 无法条件化，只能运行期解析。
 *
 * v1.0.1+S11（issue #10 修复）—— 解析顺序与失败语义都改了：
 *   ① **顺序**：ESM await import() 优先，失败再退回 createRequire。
 *      旧版只走 createRequire：在"宿主只提供带 scope 的名字"的 profile 里，该 require 在
 *      多插件共存时的 DSH module-resolution 拦截下解析失败（现场 3/3 复现，见 issue #10 的
 *      bundle 二分表），而 ESM loader 正是宿主自己走的那条路 —— 所以先试它。
 *   ② **失败语义**：顶层不再 throw。旧版在模块载入期抛错 ⇒ 宿主只回一句 failed to import
 *      （真因被吞）、entry 的 fiber 始终 undefined ⇒ **15 个工具整体消失、后端也不起**，
 *      且现象与真因不在同一层，极难定位。现在改为**降级加载**：注册一个只回报原因的占位工具
 *      （见下面 DEGRADED 分支），其余工具不注册，但报错可读、插件不会消失。
 *   ③ **诊断**：走了哪条通道、试过哪些名字，进 __internals.schemaResolve，并在 op=status /
 *      op=doctor 里可见（这类"静默整体消失"必须留下可读的现场）。
 */
const SM_NAMES: string[] = ['@deepseek-ai/schemastery', 'schemastery']
export const schemaResolve: { z?: typeof zt; via?: string; error?: string; tried: string[] } = {
  tried: [],
}
/** 只取错误的首行 + 截断：诊断要能一行说完，不要把整个 stack 塞进状态输出 */
const smErrLine = (e: unknown): string =>
  String((isRecord(e) && (e.code || e.message)) || e)
    .split('\n')[0]
    .slice(0, 120)
/** 形态判定：能拿到带 .object() 的那一层才算可用（拿不到就是不认，不赌） */
const smPick = (m: unknown): typeof zt | undefined => {
  const candidate = isRecord(m) ? (m.default ?? m) : m
  if (
    (isRecord(candidate) || typeof candidate === 'function') &&
    typeof (candidate as { object?: unknown }).object === 'function'
  )
    return candidate as unknown as typeof zt
  return undefined
}
for (const id of SM_NAMES) {
  try {
    const m: unknown = await import(/* webpackIgnore: true */ id)
    const hit = smPick(m)
    if (hit) {
      schemaResolve.z = hit
      schemaResolve.via = 'esm:' + id
      break
    }
    schemaResolve.tried.push(id + '(esm:no-object)')
  } catch (e) {
    schemaResolve.tried.push(id + '(esm:' + smErrLine(e) + ')')
  }
}
if (!schemaResolve.z) {
  try {
    const req = createRequire(import.meta.url)
    for (const id of SM_NAMES) {
      try {
        const m: unknown = req(id)
        const hit = smPick(m)
        if (hit) {
          schemaResolve.z = hit
          schemaResolve.via = 'cjs:' + id
          break
        }
        schemaResolve.tried.push(id + '(cjs:no-object)')
      } catch (e) {
        schemaResolve.tried.push(id + '(cjs:' + smErrLine(e) + ')')
      }
    }
  } catch (e) {
    schemaResolve.tried.push('createRequire(' + smErrLine(e) + ')')
  }
}
if (!schemaResolve.z) {
  schemaResolve.error =
    'schemastery 解析失败，已尝试：' +
    schemaResolve.tried.join(' / ') +
    '。宿主需提供 schemastery —— 有 scope 的 @deepseek-ai/schemastery 或无 scope 的 schemastery 任一即可。'
  console.error(
    '[dsh-blender] ' +
      schemaResolve.error +
      '（降级加载：只注册一个诊断占位工具，不会再出现"15 个工具整体消失且真因被吞"）',
  )
}
export const z = schemaResolve.z
