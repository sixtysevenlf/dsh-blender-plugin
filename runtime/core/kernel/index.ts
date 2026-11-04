// 持久内核模块入口：bootstrap 常量 + 注入/ensure 链
export { KERNEL_BOOTSTRAP } from './bootstrap.js'
export type { KernelDeps } from './api.js'
import type { KernelDeps } from './api.js'
import { createKernelInternals } from './api.js'

export function createKernel(E: KernelDeps) {
  return createKernelInternals(E)
}
