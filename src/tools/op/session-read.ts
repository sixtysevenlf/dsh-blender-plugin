/**
 * Session-read ops: they need a ready GUI session but never a write grant.
 *
 * The catalog's PLAN_SESSION_READ_OPS is the source of truth (animation_report,
 * animation_help): blender_op must resolve a session even for dry_run, while the
 * Gateway /plan route sends them through sessionReadGate (leaseGate readOnly).
 * The contract test animation-session-read.test.ts fails when the two lists drift.
 */
export const SESSION_READ_OPS: string[] = ['animation_report', 'animation_help']
export const SESSION_READ = new Set<string>(SESSION_READ_OPS)

export function isSessionRead(op: unknown): boolean {
  return SESSION_READ.has(String(op ?? ''))
}

/** How blender_op should gate one catalog op (pure, so tests can pin it). */
export interface OpGateMode {
  /** Catalog read-only op: no session, no write gate (host-side shortcut). */
  readOnly: boolean
  /** Session-read op: session required, still no write grant. */
  sessionRead: boolean
  /** Resolve a ready session before dispatch. */
  needsSession: boolean
}

export function opGateMode(
  op: unknown,
  dryRun: boolean,
  readOnlyOps: ReadonlySet<string>,
): OpGateMode {
  const name = String(op ?? '')
  const readOnly = readOnlyOps.has(name)
  const sessionRead = !readOnly && isSessionRead(name)
  return {
    readOnly,
    sessionRead,
    needsSession: readOnly ? false : sessionRead ? true : !dryRun,
  }
}
