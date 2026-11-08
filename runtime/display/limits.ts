// virtual-gui instance limits (3.1 task 4.9).
import os from 'node:os'

//
// The limit is configurable; the low-resource ceiling is a placeholder that the
// V0 report (5.10) will confirm. Nothing here spawns or waits: the session
// registry checks the limit before it asks a provider to start, so an over-limit
// start never leaves a process behind.

export const VIRTUAL_GUI_DEFAULT_MAX = 4
export const VIRTUAL_GUI_LOW_RESOURCE_MAX = 2
export const VIRTUAL_GUI_MAX_CEILING = 16

/** Below either threshold the Gateway admits at most the low-resource ceiling. */
export const LOW_RESOURCE_TOTAL_MEM_BYTES = 16 * 1024 ** 3
export const LOW_RESOURCE_FREE_MEM_BYTES = 4 * 1024 ** 3

export interface VirtualGuiLimitsInput {
  configured?: number
  env?: NodeJS.ProcessEnv
  totalMemBytes?: number
  freeMemBytes?: number
}

export interface VirtualGuiLimits {
  limit: number
  configured: number
  low_resource: boolean
  reason: 'configured' | 'low_memory'
  total_mem_bytes: number
  free_mem_bytes: number
}

export function clampVirtualGuiLimit(value: unknown, fallback: number): number {
  const n = Number(value)
  if (!Number.isFinite(n) || n < 1) return fallback
  return Math.min(VIRTUAL_GUI_MAX_CEILING, Math.max(1, Math.floor(n)))
}

/** Resolve the effective limit; an explicit env value wins over the config file. */
export function resolveVirtualGuiLimits(input: VirtualGuiLimitsInput = {}): VirtualGuiLimits {
  const configured = clampVirtualGuiLimit(
    input.env?.DSH_BLENDER_VIRTUAL_GUI_MAX,
    clampVirtualGuiLimit(input.configured, VIRTUAL_GUI_DEFAULT_MAX),
  )
  const total = Number(input.totalMemBytes ?? 0) || 0
  const free = Number(input.freeMemBytes ?? 0) || 0
  const lowMemory =
    (total > 0 && total < LOW_RESOURCE_TOTAL_MEM_BYTES) ||
    (free > 0 && free < LOW_RESOURCE_FREE_MEM_BYTES)
  return {
    limit: lowMemory ? Math.min(configured, VIRTUAL_GUI_LOW_RESOURCE_MAX) : configured,
    configured,
    low_resource: lowMemory,
    reason: lowMemory ? 'low_memory' : 'configured',
    total_mem_bytes: total,
    free_mem_bytes: free,
  }
}
/** Limits for this machine: configured value plus live memory pressure. */
export function systemVirtualGuiLimits(
  configured?: number,
  env: NodeJS.ProcessEnv = process.env,
): VirtualGuiLimits {
  return resolveVirtualGuiLimits({
    configured,
    env,
    totalMemBytes: os.totalmem(),
    freeMemBytes: os.freemem(),
  })
}
