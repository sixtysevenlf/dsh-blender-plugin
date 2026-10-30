/**
 * Minimal DSH host stand-in for contract tests.
 *
 * The real host gives the plugin a Cordis context with tools.register / effect /
 * inject / logger. This fake keeps just enough of that surface for `apply()`,
 * and always disposes the plugin again so no watchdog timer keeps Vitest alive.
 */
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
  const disposers: Array<() => void> = []
  const logs: string[] = []

  const ctx = {
    tools: {
      register(tool: CapturedTool) {
        tools.push(tool)
        return tool
      },
    },
    effect(fn: () => unknown) {
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
  }

  return {
    ctx,
    tools,
    logs,
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
