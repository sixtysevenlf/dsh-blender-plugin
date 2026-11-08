// 共享的 blender.exe 预检（首调 job 通道）：
// worker 与 job 两条 spawn 路径都必须在 spawn 前失败，否则 Node 会同步抛 TypeError，
// child.on('error') / 就绪判断里的 hint 根本走不到。
import { CFG } from '../config.js'

/**
 * blenderExe 为空/空白时抛结构化 blender_exe_missing（code + 可执行的配置 hint）。
 * retryTool 用于 hint 末尾的重试动作（worker 与 job 不同）。
 */
export function assertBlenderExe(
  exe: unknown,
  detectedFrom?: unknown,
  retryTool = 'blender_run op=start',
): void {
  if (typeof exe === 'string' && exe.trim()) return
  const from =
    detectedFrom === undefined || detectedFrom === null || !String(detectedFrom).trim()
      ? blenderExeSource()
      : String(detectedFrom)
  const err = new Error(
    '没找到 Blender 可执行文件（blenderExe 为空；探测来源: ' + from + '）',
  ) as Error & { code: string; hint: string }
  err.code = 'blender_exe_missing'
  err.hint =
    '设置 DSH_BLENDER_EXE 或 dsh-blender.config.json 的 blenderExe（当前探测来源: ' +
    from +
    '）；Steam 版路径形如 <Steam>\\steamapps\\common\\Blender\\blender.exe（如 D:\\Program Files\\Steam\\steamapps\\common\\Blender\\blender.exe）；改完重启 DSH，再 ' +
    retryTool
  throw err
}

export function blenderExeSource(): string {
  try {
    const cfg = CFG as unknown as { source?: { blenderExe?: unknown } }
    const from = cfg.source ? cfg.source.blenderExe : undefined
    if (from !== undefined && from !== null && String(from).trim()) return String(from)
  } catch (e) {}
  return 'unknown'
}
