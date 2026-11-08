/**
 * 3.1 legacy tool-name migration hints.
 *
 * The six model tools are the only registered tools; a 3.0.x name (or an
 * unprefixed internal name) is rejected by the host with UNKNOWN_TOOL. A
 * pre-execute guard turns that dead end into a hint naming the owning tool, so
 * an agent that learned the old surface can recover in one step.
 */

const LEGACY_TOOLS: Record<string, string> = {
  blender_rt_see: 'blender_see',
  blender_rt_watch: 'blender_see',
  blender_rt_do: 'blender_py',
  blender_rt_plan: 'blender_op',
  blender_rt_loop: 'blender_op',
  blender_rt_opt: 'blender_op',
  blender_rt_preset: 'blender_op',
  blender_rt_perf: 'blender_run',
  blender_rt_headless: 'blender_run',
  blender_rt_job: 'blender_run',
  blender_rt_cmd: 'blender_inspect',
  blender_rt_commands: 'blender_inspect',
  blender_rt_txn: 'blender_session',
  blender_rt_worker: 'blender_run',
  blender_viewport: 'blender_session',
}

const UNPREFIXED_TOOLS: Record<string, string> = {
  session: 'blender_session',
  op: 'blender_op',
  py: 'blender_py',
  see: 'blender_see',
  run: 'blender_run',
  inspect: 'blender_inspect',
  viewport: 'blender_session',
  do: 'blender_py',
  watch: 'blender_see',
  plan: 'blender_op',
  loop: 'blender_op',
  opt: 'blender_op',
  preset: 'blender_op',
  perf: 'blender_run',
  headless: 'blender_run',
  job: 'blender_run',
  cmd: 'blender_inspect',
  commands: 'blender_inspect',
  txn: 'blender_session',
  worker: 'blender_run',
}

const SURFACE =
  'blender_session / blender_op / blender_py / blender_see / blender_run / blender_inspect'

/**
 * Return the migration hint for a removed tool name, or undefined for a name
 * this plugin does not own (the host keeps its normal UNKNOWN_TOOL error).
 */
export function legacyToolHint(name: unknown): string | undefined {
  const key = String(name ?? '')
  const target = LEGACY_TOOLS[key] || UNPREFIXED_TOOLS[key]
  if (!target) return undefined
  return (
    key +
    ' is not a 3.1 model tool: use ' +
    target +
    ' instead. The model surface is exactly six tools (' +
    SURFACE +
    ').'
  )
}

/** Old -> new table used by docs/tests. */
export const LEGACY_TOOL_MAP: Readonly<Record<string, string>> = {
  ...LEGACY_TOOLS,
  ...UNPREFIXED_TOOLS,
}
