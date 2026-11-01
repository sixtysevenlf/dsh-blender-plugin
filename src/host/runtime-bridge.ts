/**
 * runtime-bridge：src 侧对编译产物 lib/runtime/*.js 的唯一入口。
 * 为什么需要桥：src 是 TypeScript 插件层，runtime 是独立编译的 Blender 侧宿主层；
 * 两层的模块格式/发布布局不同（src 构建到 lib，runtime 构建到 lib/runtime）。
 * 原来 4 个 src 文件各自直 import '../../lib/runtime/*.js' —— 目录搬迁/发布布局变化
 * 会散落修改。集中到本文件后，src 内只允许通过 host/runtime-bridge.ts 触碰 runtime 层。
 */
export { probeHttp, waitHttp, decideStart } from '../../lib/runtime/backend_probe.js'
export { winKillPids, winSamePortHolders } from '../../lib/runtime/win_proc.js'
export { detectFileAlias } from '../../lib/runtime/file_guard.js'
export { serverPidsFromProcRows } from '../../lib/runtime/ports.js'
