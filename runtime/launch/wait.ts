// 启动等待：addon 端口轮询（F1.1 从 launch.ts 拆出）
import { probeTcp } from '../backend_probe.js'

/** 等 addon 端口监听；started 为起始时间（ms） */
export async function waitForAddonPort(
  started: number,
  waitMs: number,
  addonPort: number,
): Promise<boolean> {
  while (Date.now() - started < waitMs) {
    if (await probeTcp('127.0.0.1', addonPort, 700)) return true
    await new Promise((r) => setTimeout(r, 500))
  }
  return false
}
