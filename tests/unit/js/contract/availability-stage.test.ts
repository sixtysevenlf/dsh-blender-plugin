/**
 * 3.3: stage attribution is a contract, not a message. These cases are the
 * 3.0.6 failed-first-call failure classes plus the layers those cases did not reach:
 * every class maps to exactly one stage and none collapses into "unknown".
 */
import { describe, expect, it } from 'vitest'
import {
  normalizeAvailability,
  stageForFailureCode,
} from '../../../../runtime/core/availability.js'

describe('failure stage attribution', () => {
  it.each([
    ['no Blender executable', 'blender_exe_missing', 'config'],
    ['Steam path missing', 'steam_not_found', 'config'],
    ['half-open addon', 'addon_unresponsive', 'addon'],
    ['commands cannot reach the addon', 'addon_unreachable', 'transport'],
    ['Gateway is not running', 'backend_unreachable', 'transport'],
    ['occupied Gateway port', 'port_conflict', 'config'],
    ['another host owns the Gateway', 'GATEWAY_IN_USE', 'config'],
    ['old addon protocol', 'unsupported_protocol', 'handshake'],
    ['no ready GUI session', 'session_required', 'session'],
    ['spawn failed', 'spawn_failed', 'spawn'],
    ['bpy probe timed out', 'bpy_timeout', 'bpy'],
    ['queue is full', 'queue_full', 'queue'],
    ['render failed', 'render_failed', 'render'],
  ])('%s maps to %s', (_label, code, stage) => {
    expect(stageForFailureCode(code)).toBe(stage)
  })

  it('never emits a generic unknown stage from a legacy response', () => {
    for (const code of ['something_new', '', undefined]) {
      const normalized = normalizeAvailability({ ok: false, error: 'failed', code }) as Record<
        string,
        unknown
      >
      expect(normalized.stage).toBeTypeOf('string')
      expect(normalized.stage).not.toBe('unknown')
      expect(normalized.root_cause).toBe('failed')
      expect(normalized.fix).toBeTruthy()
    }
  })

  it('keeps an explicit stage over the code heuristic', () => {
    const normalized = normalizeAvailability({
      ok: false,
      error: 'busy',
      code: 'backend_error',
      stage: 'bpy',
    }) as Record<string, unknown>
    expect(normalized.stage).toBe('bpy')
  })
})
