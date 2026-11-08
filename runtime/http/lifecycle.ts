// HTTP 生命周期：端口占用自愈、优雅退出、启动日志。
// 与拆分前一致：EADDRINUSE 先探活（能应答就让位），死进程 SIGTERM→600ms→SIGKILL→300ms 重试，最多 2 次。
import type { Server } from 'node:http'
import fs from 'node:fs'
import path from 'node:path'
import { CFG } from '../config.js'
import { errorMessage } from '../core/json.js'
import { holderResponsive, killHolders } from '../ports.js'

// 永远排不上队、看护每 15 s 重生一个实例却撞 EADDRINUSE 退出 ⇒ 无限循环，只能人工 kill -9。
// 两层兜底：① 信号处理不再可能被"吞"（强制退出定时器）；② EADDRINUSE 时先判定占用者是死是活，
// 死的直接 SIGKILL 夺回端口并重试 listen（不依赖人工、不依赖看护）。
const sleepMs = (ms: number): Promise<void> => new Promise((r) => setTimeout(r, ms))

export interface StartServerOpts {
  server: Server
  engine: { stop: () => unknown }
  port: number
  host: string
  routes: string[]
}

export function startServer({ server, engine, port, host, routes }: StartServerOpts): void {
  const onListening = () =>
    console.log(
      '[blender-rt] http://' + host + ':' + String(port) + '/  (routes: ' + routes.join(' ') + ')',
    )
  let reclaimTries = 0
  server.on('error', async (e: NodeJS.ErrnoException) => {
    const code = e && e.code
    if (code === 'EADDRINUSE' && reclaimTries < 2) {
      reclaimTries += 1
      if (await holderResponsive(port)) {
        console.error(
          '[dsh-blender-backend] 端口 ' +
            String(port) +
            ' 被**能应答**的后端占着 —— 不夺，本次退出（交给看护/op=start）。',
        )
        process.exit(0)
      }
      const k1 = killHolders(port, 'SIGTERM')
      await sleepMs(600)
      const k2 = killHolders(port, 'SIGKILL')
      console.error(
        '[dsh-blender-backend] 僵死后端占着 ' +
          String(port) +
          '：SIGTERM=[' +
          k1.join(',') +
          '] SIGKILL=[' +
          k2.join(',') +
          '] → 重试 listen。',
      )
      await sleepMs(300)
      try {
        server.listen(port, host, onListening)
        return
      } catch (e2) {
        /* 落到下面的通用报错 */
      }
    }
    const msg =
      code === 'EADDRINUSE'
        ? '端口 ' +
          port +
          ' 已被占用（EADDRINUSE）且自动夺回失败：多半是上一次的后端僵死。' +
          '处置：blender_session op=restart，或 kill -9 掉 /proc 里 cmdline 含 "gateway.js --port ' +
          String(port) +
          '" 的进程。'
        : '监听失败：' + errorMessage(e)
    console.error('[dsh-blender-backend] ' + msg)
    try {
      fs.writeFileSync(path.join(CFG.workDirWsl, 'backend-listen-error.txt'), msg, 'utf8')
    } catch (e2) {
      /* workDir 都写不了就算了 */
    }
    process.exit(1)
  })
  server.listen(port, host, onListening)
  const shutdown = () => {
    try {
      engine.stop()
    } catch (e) {
      /* 忽略 */
    }
    try {
      server.close()
    } catch (e) {
      /* 忽略 */
    }
    setTimeout(() => process.exit(0), 400)
  }
  process.on('SIGINT', shutdown)
  process.on('SIGTERM', shutdown)
}
