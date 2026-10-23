/**
 * F-14 · 无头进程硬退出（issue：Blender 5.2.3 Steam 版退出期 AV 0xC0000005）。
 *
 * 现场：glTF 导出成功后（`DSH_STAGE {"name":"done"}` 已打出、产物完整），Blender 在**退出拆解阶段**
 * 因 .blend 加载材质带 Value→Material Output.Thickness 连线命中导出器崩溃路径 —— 崩溃发生在
 * 所有输出冲刷之后，结果其实已经全部拿到，却被 exitCode≠0 顶成"失败"。
 *
 * 对策：done 标记之后显式冲刷 stdout/stderr，然后 `os._exit(0)` —— 跳过 Blender 自身的退出拆解。
 * 默认开启（opts.hardExit !== false）；依赖正常 teardown 的调用方（bpy.app.handlers 落盘、
 * 延迟自动保存）显式传 hardExit=false 回到旧行为。
 *
 * 两条装配路径共用本模块（Python 行与 issue 定稿逐字一致）：
 *  - 作业路径（engine.jobStart，parts.push 数组形态）→ hardExitJobParts(opts)
 *  - 同步路径（engine.headless，body += NL + 字符串拼接形态）→ hardExitSyncTail(opts, NL)
 * 纯函数，tests/headless_hard_exit_selftest.mjs 离线断言顺序与开关语义。
 */

/** done 之后的硬退出 Python 行（顺序即契约：先冲刷、后 _exit —— 反过来就是丢输出）。 */
export function hardExitJobParts(opts) {
  if (opts && opts.hardExit === false) return [];
  return [
    'import sys as _dsh_sys',
    '_dsh_sys.stdout.flush(); _dsh_sys.stderr.flush()',
    'import os as _dsh_os; _dsh_os._exit(0)',
  ];
}

/** 同步路径的字符串拼接形态：前导 NL + 三行；关闭时返回空串（body 一字不动）。 */
export function hardExitSyncTail(opts, NL) {
  const parts = hardExitJobParts(opts);
  return parts.length ? (NL + parts.join(NL)) : '';
}
