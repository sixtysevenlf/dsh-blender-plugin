/** Host identity shared by the caller headers and the Gateway owner record. */
import { createHash } from 'node:crypto'
import os from 'node:os'

/**
 * A stable per-machine/user identity.
 *
 * The Gateway owner record is compared against this value on attach, so it must
 * survive a plugin reload (a new plugin process must still recognise the
 * Gateway it started earlier). Process.pid is therefore not usable here.
 * DSH_HOST_ID wins when the host injects a per-instance id.
 */
export function hostIdentity(env: NodeJS.ProcessEnv = process.env): string {
  const explicit = String(env.DSH_HOST_ID || '').trim()
  if (explicit) return explicit
  let user: string
  try {
    user = os.userInfo().username
  } catch {
    user = String(env.USERNAME || env.USER || 'unknown')
  }
  const seed = [os.hostname(), user].join('|')
  return 'host-' + createHash('sha1').update(seed).digest('hex').slice(0, 12)
}
