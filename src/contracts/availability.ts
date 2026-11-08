/**
 * Failure envelope shared by every model-facing tool and Gateway route.
 *
 * An error is only useful to an agent when it names the failing layer, the
 * concrete cause, an executable fix, a safe fallback and the evidence that
 * supports the verdict. config/availability.json is the single source for the
 * stage enum, required fields, fallback kinds and diagnostic budgets; the
 * constants below are generated from it.
 */
import {
  AVAILABILITY_BUDGETS_MS,
  AVAILABILITY_FALLBACK_KINDS,
  AVAILABILITY_REQUIRED_FIELDS,
  AVAILABILITY_STAGES,
} from './availability.generated.js'
import type {
  AvailabilityFallbackKind,
  AvailabilityRequiredField,
  AvailabilityStage,
} from './availability.generated.js'
import { isRecord, text } from './dynamic.js'

export {
  AVAILABILITY_BUDGETS_MS,
  AVAILABILITY_FALLBACK_KINDS,
  AVAILABILITY_REQUIRED_FIELDS,
  AVAILABILITY_STAGES,
}
export type { AvailabilityFallbackKind, AvailabilityRequiredField, AvailabilityStage }

export interface AvailabilityFallback {
  kind: AvailabilityFallbackKind
  summary: string
}

export interface AvailabilityFailure {
  ok: false
  code: string
  stage: AvailabilityStage
  root_cause: string
  fix: string
  fallback: AvailabilityFallback
  evidence: Record<string, unknown>
}

export interface AvailabilityInput {
  stage: AvailabilityStage
  code?: string
  rootCause: string
  fix: string
  fallback: AvailabilityFallback | { kind: AvailabilityFallbackKind; summary: string } | string
  evidence?: Record<string, unknown>
}

function fallbackView(
  fallback: AvailabilityInput['fallback'],
): AvailabilityFallback | { kind: string; summary: string } {
  if (typeof fallback === 'string')
    return { kind: AVAILABILITY_FALLBACK_KINDS[0], summary: fallback }
  const kind = text(fallback && 'kind' in fallback ? fallback.kind : '')
  const summary = text(fallback && 'summary' in fallback ? fallback.summary : '')
  return {
    kind: (AVAILABILITY_FALLBACK_KINDS as readonly string[]).includes(kind)
      ? (kind as AvailabilityFallbackKind)
      : kind || AVAILABILITY_FALLBACK_KINDS[0],
    summary,
  }
}

/** Build the canonical failure envelope. Callers may add evidence but not drop fields. */
export function availabilityFailure(input: AvailabilityInput): AvailabilityFailure {
  const fallback = fallbackView(input.fallback)
  return {
    ok: false,
    code: text(input.code) || input.stage + '_failure',
    stage: input.stage,
    root_cause: text(input.rootCause),
    fix: text(input.fix),
    fallback: { kind: fallback.kind as AvailabilityFallbackKind, summary: fallback.summary },
    evidence: input.evidence ? { ...input.evidence } : {},
  }
}

function isNonEmptyString(value: unknown): value is string {
  return typeof value === 'string' && value.trim().length > 0
}

/**
 * Contract check for a response that claims to be a failure envelope.
 * Returns every violation (empty array = valid); never repairs silently.
 */
export function validateAvailabilityEnvelope(value: unknown): string[] {
  if (!isRecord(value)) return ['envelope must be an object']
  const violations: string[] = []
  const stage = value.stage
  if (!isNonEmptyString(stage)) violations.push('stage is missing')
  else if (!(AVAILABILITY_STAGES as readonly string[]).includes(stage))
    violations.push('stage is not in config/availability.json: ' + stage)
  for (const field of AVAILABILITY_REQUIRED_FIELDS) {
    if (field === 'fallback' || field === 'evidence') continue
    if (!isNonEmptyString(value[field])) violations.push(field + ' must be a non-empty string')
  }
  const fallback = value.fallback
  if (!isRecord(fallback)) {
    violations.push('fallback must be an object')
  } else {
    const kind = fallback.kind
    if (!isNonEmptyString(kind)) violations.push('fallback.kind is missing')
    else if (!(AVAILABILITY_FALLBACK_KINDS as readonly string[]).includes(kind))
      violations.push('fallback.kind is not in config/availability.json: ' + kind)
    if (!isNonEmptyString(fallback.summary)) violations.push('fallback.summary is missing')
  }
  if (!isRecord(value.evidence)) violations.push('evidence must be an object')
  return violations
}

/** Budget for one diagnostic stage; config/availability.json is the source. */
export function availabilityBudgetMs(stage: AvailabilityStage): number {
  return AVAILABILITY_BUDGETS_MS[stage]
}
const CODE_STAGES: Array<[RegExp, AvailabilityStage]> = [
  [/^port_conflict$|^GATEWAY_IN_USE$|^blender_exe_missing$|^invalid_/i, 'config'],
  [/^session_/i, 'session'],
  [/^spawn_/i, 'spawn'],
  [/^(backend_unreachable|addon_unreachable|gateway_unreachable)$/i, 'transport'],
  [/^(handshake_|unsupported_protocol|protocol_)/i, 'handshake'],
  [/^(addon_|backend_error)/i, 'addon'],
  [/^bpy_/i, 'bpy'],
  [/^queue_/i, 'queue'],
  [/^render_/i, 'render'],
]

/** Map a legacy failure code to exactly one stage; never returns a generic unknown. */
export function stageForFailureCode(code: unknown): AvailabilityStage {
  const value = text(code)
  for (const [pattern, stage] of CODE_STAGES) if (pattern.test(value)) return stage
  return 'config'
}
