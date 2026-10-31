import type { Context } from '@deepseek-ai/cordis'
import type { ToolRegistry } from '@deepseek-ai/dsh-tools'

/** Minimal shape of the host skill service used by the optional bundled-skill mount. */
export interface HostSkillService {
  registerProvider(provider: () => unknown): unknown
}

/**
 * The Cordis context plus the only optional service this plugin reads directly.
 *
 * Cordis augments Context with tools when @deepseek-ai/dsh-tools is in the
 * program; spelling that out here keeps the registration surface typed without
 * falling back to any. Hosts without the skill service simply omit skills.
 */
export type HostContext = Context & {
  tools: ToolRegistry
  skills?: HostSkillService
}
