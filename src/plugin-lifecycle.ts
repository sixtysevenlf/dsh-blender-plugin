/** 插件生命周期装配：自动拉起、15s 常驻看护、卸载清句柄（与工具面解耦）。 */
import {
  ensureBackend,
  isPaused,
  killBackendByCmd,
  killChildHandle,
  probe,
  sleep,
} from './host/backend/index.js'
import { backendPost } from './host/http.js'
import {
  canRestartGateway,
  noteGatewayRestartFailure,
  noteGatewayRestartSuccess,
} from './host/backend/restart.js'
import type { HostContext } from './host/context.js'

export function registerLifecycle(ctx: HostContext, port: number, autoStart: boolean): void {
  void (async () => {
    if (autoStart && !isPaused()) {
      if (await ensureBackend(port)) noteGatewayRestartSuccess()
      else noteGatewayRestartFailure()
    }
  })()

  // 常驻看护：每 15s 探活一次，掉线自动拉起（宿主重启 / 进程被杀 / 端口被回收都能自愈）。
  // 显式 op=stop 会置 paused，看护尊重它，不会跟用户对着干。
  // ★ v1.0.1（探活失败时**先清尸再重生**）：否则"僵死后端占着端口"时，重生进程必然撞
  //   EADDRINUSE 退出 → 每 15 s 空转一次、永不自愈（现场：后端事件循环被长时间同步操作占死，
  //   /health 不应答、连 SIGTERM 都排不上队）。probe 超时只有 1500ms，所以连续 2 次（≈30s）
  //   失败才认定卡死，避免把"只是忙"的后端杀掉。
  let probeMisses = 0
  ctx.effect(() => {
    const wd = setInterval(() => {
      void (async () => {
        try {
          if (isPaused()) return
          if (await probe(port)) {
            probeMisses = 0
            noteGatewayRestartSuccess()
            // 租约心跳：只在"本来就持有"时续期，空闲时不抢占别的会话
            try {
              await backendPost(port, '/lease', { renewOnly: true }, 5000)
            } catch {
              /* 后端可能刚挂，下一轮再试 */
            }
            return
          }
          probeMisses += 1
          if (probeMisses >= 2) {
            probeMisses = 0
            // SIGTERM → 1.2s → SIGKILL：事件循环被同步操作占死的进程只认 SIGKILL
            try {
              killBackendByCmd(port, 'SIGTERM')
            } catch {
              /* 忽略 */
            }
            await sleep(1200)
            try {
              killBackendByCmd(port, 'SIGKILL')
            } catch {
              /* 忽略 */
            }
          }
          // Bounded auto-restart: exponential backoff up to 30 s, five failures.
          // An explicit op=start resets the budget (see viewport/execute.ts).
          if (!canRestartGateway()) return
          if (await ensureBackend(port)) noteGatewayRestartSuccess()
          else noteGatewayRestartFailure()
        } catch {
          /* 看护失败静默，下一轮再试 */
        }
      })()
    }, 15000)
    return () => clearInterval(wd)
  }, 'dsh-blender-plugin: watchdog')

  ctx.effect(
    () => () => {
      killChildHandle()
    },
    'dsh-blender-plugin: backend',
  )
}
