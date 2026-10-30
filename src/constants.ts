import { readFileSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

export const IS_WIN = process.platform === 'win32'

/**
 * v0.8.10（A0 版本自证）：工具描述统一带版本前缀。
 * description 由**已加载的那份 lib** 生成 → 会话里一眼能看出自己跑的是哪一代，
 * 不会再出现「读的是新源码、跑的是旧 lib」那种排查事故（外部反馈 j20-build 的元教训）。
 */
export const PLUGIN_VERSION = '2.6.2'

// 后端与 Blender 侧模块都放在插件包的 runtime/ 下 → 整个包可搬到任意目录（WSL 或 Windows）
export const HERE = path.dirname(fileURLToPath(import.meta.url))
export const SERVER_PATH = process.env.DSH_BLENDER_SERVER || path.join(HERE, '..', 'runtime', 'server.mjs')
export const HOSTNAME = '127.0.0.1'

/** 后端未就绪时的统一提示（不假设任何本机路径） */
export const BACKEND_HINT =
  '先确认 Blender 在运行且本仓 addon（DSH Blender Bridge v1.10+）已 Connect；然后 blender_viewport op=start 拉起后端。' +
  '端口/工作目录/blender.exe 可用 DSH_BLENDER_* 环境变量或包根 dsh-blender.config.json 配置（见 docs/配置参考.md）。'

/**
 * v0.9.2（R3）：给宿主的**静态**截止时间 —— defineTool 只收常量，不能按参数动态给。
 * 取值原则：一律 = 该工具内部最长超时 + 余量 → 内部超时永远先触发，模型拿到的是本插件
 * 的可读错误；宿主的 TOOL_TIMEOUT 只兜"连内部超时都没兜住"的最后一道。
 * 背景：宿主 0.1.6 起装配 @deepseek-ai/dsh-tool-call-timeout-policy，但**只在工具自己
 * 声明 timeoutMs 时才设截止**；不声明 = 上游一旦给默认值，Blender 还在跑而工具已被判超时。
 */
export const T = {
  see: 300000, // 内部 max(/view 180s, /frame.png 60s) + 余量
  act: 300000, // /act 180s + 回帧 60s + 余量
  watch: 600000, // /act 180s + 最多 32 帧（每帧最长 60s）
  cmd: 360000, // /cmd 内部 300s
  commands: 60000, // 只读清单
  loop: 360000, // /loop 内部 300s（内环预算在 spec.budget_ms 里另算）
  perf: 660000, // /perf 内部 600s
  opt: 660000, // /opt 内部 600s
  // v0.9.3（D1）：headless 不再"陪跑到超时" —— 客户端等待窗口（DSH_HEADLESS_WAIT_MS，默认 100 s）到点就回
  // promoted 回执（jobId），服务端子进程继续跑。静态截止留大余量即可（自定义大窗口的用户也不会被判超时）。
  headless: 1200000,
  plan: 360000, // /plan 内部 300s
  worker: 1920000, // exec timeout_ms 由调用方给；静态上限取 32min
  txn: 360000, // /txn 内部 300s
  preset: 360000, // /preset 内部 300s
  // v0.9.3（D6.2）：job 新增 op=wait（服务端单次上限 600 s）→ 静态截止要盖住它
  job: 660000,
  viewport: 60000 // status/doctor/lease + start/restart 的重试窗口
  // v1.2：等用户点选 —— 后端 TTL 默认 120 s（夹 [5,600]），静态截止留余量盖住最长 600 s + 收尾
}

/**
 * v0.9.2（R2 自证）：插件 import 到的 @deepseek-ai/dsh-tools 是哪一份、哪一版。
 * 本机解析链：插件 node_modules → ~/dsh-harness（fake checkout）→ /usr/lib/node_modules
 * 的**全局安装**。DSH 一升级，这里就是升级后那份；而全局安装位置一变，插件会在载入期
 * ERR_MODULE_NOT_FOUND（15 个工具整体消失）。doctor/status 带上它，用来一眼区分
 * "插件/宿主 API 坏了"与"Blender 没起"。
 */
function hostApiInfo(): string {
  try {
    const resolveFn = (import.meta as any).resolve
    if (typeof resolveFn !== 'function') return 'dsh-tools: import.meta.resolve 不可用（node < 20.6?）'
    const url = String(resolveFn.call(import.meta, '@deepseek-ai/dsh-tools') || '')
    let p = url
    if (p.startsWith('file://')) p = fileURLToPath(p)
    let ver = '?'
    try {
      ver = JSON.parse(readFileSync(path.join(path.dirname(path.dirname(p)), 'package.json'), 'utf8')).version
    } catch (e: any) {
      /* 版本读不到不算错 */
    }
    return 'dsh-tools@' + String(ver) + ' @ ' + p
  } catch (e: any) {
    return 'dsh-tools 解析失败：' + String((e && e.message) || e) + '（插件会在载入期就失败：检查全局安装位置是否变了）'
  }
}
export const HOST_API = hostApiInfo()

/** 宿主取消（exec.signal abort）时的统一措辞：只说"等待被取消"，不谎称任务失败 */
export const ABORT_HINT =
  '宿主取消了本次等待（exec.signal 已 abort：用户打断或 per-tool 截止）。' +
  '服务端任务可能仍在跑 —— 长任务请用 blender_rt_job 并按 runId 回收；' +
  '本插件的内部超时通常先于宿主截止触发，正常情况看到的是上面这类可读错误。'

/** 本会话在写通道上的身份（租约 holder）；跨会话共用同一个后端时用它区分 */
export const HOLDER = process.env.DSH_BLENDER_HOLDER || 'plugin-pid-' + String(process.pid)
