import { afterEach, describe, expect, it, vi } from 'vitest'

const h = vi.hoisted(() => ({
  holderResponsive: vi.fn(async () => false),
  killHolders: vi.fn(() => [4242]),
}))

vi.mock('../../../runtime/ports.js', () => ({
  holderResponsive: h.holderResponsive,
  killHolders: h.killHolders,
}))
vi.mock('../../../runtime/config.js', () => ({
  CFG: { workDirWsl: 'D:/tmp/f4-wiring' },
}))

import { startServer } from '../../../runtime/http/lifecycle.js'

afterEach(() => {
  vi.restoreAllMocks()
})

describe('http lifecycle port wiring', () => {
  it('reclaims EADDRINUSE through the shared holder primitives', async () => {
    const handlers: Record<string, (...args: unknown[]) => unknown> = {}
    const server = {
      on: vi.fn((event: string, handler: (...args: unknown[]) => unknown) => {
        handlers[event] = handler
      }),
      listen: vi.fn(),
      close: vi.fn(),
    }
    const exit = vi.spyOn(process, 'exit').mockImplementation((() => undefined) as never)
    vi.spyOn(process, 'on').mockImplementation((() => process) as never)

    startServer({
      server: server as never,
      engine: { stop: vi.fn() },
      port: 19877,
      host: '127.0.0.1',
      routes: ['/health'],
    })
    expect(server.listen).toHaveBeenCalledTimes(1)

    await handlers.error({ code: 'EADDRINUSE' })

    expect(h.holderResponsive).toHaveBeenCalledWith(19877)
    expect(h.killHolders).toHaveBeenNthCalledWith(1, 19877, 'SIGTERM')
    expect(h.killHolders).toHaveBeenNthCalledWith(2, 19877, 'SIGKILL')
    expect(server.listen).toHaveBeenCalledTimes(2)
    expect(exit).not.toHaveBeenCalled()
  }, 10000)
})
