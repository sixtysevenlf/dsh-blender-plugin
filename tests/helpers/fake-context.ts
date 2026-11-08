/**
 * Minimal DSH host stand-in for contract tests.
 *
 * The real host gives the plugin a Cordis context with tools.register / effect /
 * inject / logger. This fake keeps just enough of that surface for `apply()`,
 * and always disposes the plugin again so no watchdog timer keeps Vitest alive.
 *
 * The object is cast to HostContext: a fake only implements the four surfaces
 * apply() touches, and the cast keeps every call site honest about the real
 * contract without spreading any through the tests.
 */
import type { HostContext } from '../../src/host/context'

export interface CapturedTool {
  name: string
  description?: string
  parameters?: unknown
  output?: unknown
  timeoutMs?: number
  execute?: (...args: never[]) => unknown
  presentCall?: unknown
  presentResult?: unknown
  isConcurrencySafe?: boolean | (() => boolean)
}

export function createFakeContext() {
  const tools: CapturedTool[] = []
  const guards: Array<(exec: { name?: unknown }) => string | undefined> = []
  const disposers: Array<() => void> = []
  const logs: string[] = []
  /** Ordered effect keys and tool names, for registration-order contracts. */
  const events: string[] = []

  const ctx = {
    tools: {
      register(tool: CapturedTool) {
        tools.push(tool)
        events.push('tool:' + tool.name)
        return tool
      },
      guard(fn: (exec: { name?: unknown }) => string | undefined) {
        guards.push(fn)
        events.push('guard')
        return () => {
          const at = guards.indexOf(fn)
          if (at >= 0) guards.splice(at, 1)
        }
      },
    },
    effect(fn: () => unknown, key?: string) {
      events.push('effect:' + String(key || ''))
      const dispose = fn()
      if (typeof dispose === 'function') disposers.push(dispose as () => void)
      return dispose
    },
    inject() {
      return () => {}
    },
    on() {
      return () => {}
    },
    get() {
      return undefined
    },
    logger: {
      info: (msg: unknown) => logs.push(String(msg)),
      warn: (msg: unknown) => logs.push(String(msg)),
      error: (msg: unknown) => logs.push(String(msg)),
      debug: () => {},
    },
  } as unknown as HostContext

  return {
    ctx,
    tools,
    guards,
    logs,
    events,
    dispose() {
      while (disposers.length) {
        const fn = disposers.pop()
        try {
          fn?.()
        } catch {
          // disposal must never mask the assertion that follows
        }
      }
    },
  }
}
