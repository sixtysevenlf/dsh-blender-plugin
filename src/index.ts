/**
 * @dsh-external/dsh-blender-plugin —— host 侧入口（entry-only）。
 *
 * 只做 re-export：实现分别落在 plugin / contracts / host / tools / plan / headless / utils / ui。
 * 15 个模型工具：see / do / watch / cmd / commands / loop / perf / opt / headless / plan /
 * worker / txn / preset / job / viewport；plan catalog 34 family / 228 op（计数真源在 catalog，不在此处手写）。
 * 配合持久内核变量 K（addon 每次调用是新命名空间，K 存在 sys.modules 里 → REPL 语义）。
 *
 * 并发：写操作自动带租约 holder；别的会话持有时写路由 409（只读 op 豁免，force 可抢）。
 */
export { name, inject, Config, apply, __internals } from './plugin.js'
export { frameDedupe, resetFrameCache } from './utils/frame-cache.js'
