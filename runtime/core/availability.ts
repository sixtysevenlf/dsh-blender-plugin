/**
 * Gateway-side mirrors of the shared availability contract.
 *
 * The Gateway runs in a separate process from the DSH host, so it carries its
 * own copy of the constructor and validator while both sides generate their
 * enum/budget constants from config/availability.json.
 */
import {
  AVAILABILITY_BUDGETS_MS,
  AVAILABILITY_FALLBACK_KINDS,
  AVAILABILITY_REQUIRED_FIELDS,
  AVAILABILITY_STAGES,
} from './availability-schema.generated.js'
import type {
  AvailabilityFallbackKind,
  AvailabilityStage,
} from './availability-schema.generated.js'
import { isRecord } from './json.js'

export {
  AVAILABILITY_BUDGETS_MS,
  AVAILABILITY_FALLBACK_KINDS,
  AVAILABILITY_REQUIRED_FIELDS,
  AVAILABILITY_STAGES,
}
export type { AvailabilityFallbackKind, AvailabilityStage }

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
  fallback: AvailabilityFallback | string
  evidence?: Record<string, unknown>
}

function str(value: unknown): string {
  return value === undefined || value === null ? '' : String(value)
}

export function availabilityFailure(input: AvailabilityInput): AvailabilityFailure {
  const fallback =
    typeof input.fallback === 'string'
      ? { kind: AVAILABILITY_FALLBACK_KINDS[0], summary: input.fallback }
      : input.fallback
  return {
    ok: false,
    code: str(input.code) || input.stage + '_failure',
    stage: input.stage,
    root_cause: str(input.rootCause),
    fix: str(input.fix),
    fallback: {
      kind: fallback.kind,
      summary: str(fallback.summary),
    },
    evidence: input.evidence ? { ...input.evidence } : {},
  }
}

function isNonEmptyString(value: unknown): value is string {
  return typeof value === 'string' && value.trim().length > 0
}

export function validateAvailabilityEnvelope(value: unknown): string[] {
  if (!isRecord(value)) return ['envelope must be an object']
  const violations: string[] = []
  if (!isNonEmptyString(value.stage)) violations.push('stage is missing')
  else if (!(AVAILABILITY_STAGES as readonly string[]).includes(value.stage))
    violations.push('stage is not in config/availability.json: ' + value.stage)
  if (!isNonEmptyString(value.root_cause)) violations.push('root_cause must be a non-empty string')
  if (!isNonEmptyString(value.fix)) violations.push('fix must be a non-empty string')
  const fallback = value.fallback
  if (!isRecord(fallback)) {
    violations.push('fallback must be an object')
  } else {
    if (!isNonEmptyString(fallback.kind)) violations.push('fallback.kind is missing')
    else if (!(AVAILABILITY_FALLBACK_KINDS as readonly string[]).includes(fallback.kind))
      violations.push('fallback.kind is not in config/availability.json: ' + fallback.kind)
    if (!isNonEmptyString(fallback.summary)) violations.push('fallback.summary is missing')
  }
  if (!isRecord(value.evidence)) violations.push('evidence must be an object')
  return violations
}

export function availabilityBudgetMs(stage: AvailabilityStage): number {
  return AVAILABILITY_BUDGETS_MS[stage]
}
const LEGACY_STAGE_ALIASES: Record<string, AvailabilityStage> = {
  execute_code: 'addon',
  'execute-code': 'addon',
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
  const value = str(code)
  for (const [pattern, stage] of CODE_STAGES) if (pattern.test(value)) return stage
  return 'config'
}

function legacyStage(value: unknown): AvailabilityStage | null {
  const candidate = str(value)
  if (!candidate) return null
  if ((AVAILABILITY_STAGES as readonly string[]).includes(candidate))
    return candidate as AvailabilityStage
  return LEGACY_STAGE_ALIASES[candidate] || null
}

/**
 * Normalize a legacy failure response at the HTTP edge.
 *
 * Routes still carry historical fields (error, code, diagnosis); adding the four
 * question envelope here proves every failed response crosses the contract even
 * before each route is rewritten. An explicit stage always wins; a diagnosis
 * stage is preferred over the code heuristic.
 */
export function normalizeAvailability(value: unknown): unknown {
  if (!isRecord(value) || value.ok !== false) return value
  if (isNonEmptyString(value.stage)) return value
  const nested = isRecord(value.error) ? value.error : {}
  const diagnosis = isRecord(value.diagnosis) ? value.diagnosis : {}
  const addon = isRecord(value.addon) ? value.addon : {}
  const stage =
    legacyStage(nested.stage) ||
    legacyStage(diagnosis.stage) ||
    legacyStage(addon.stage) ||
    stageForFailureCode(nested.code || value.code || value.errorCode)
  const rootCause = str(
    nested.root_cause ||
      value.root_cause ||
      nested.summary ||
      diagnosis.summary ||
      addon.summary ||
      nested.message ||
      nested.error ||
      value.error ||
      value.message ||
      diagnosis.error ||
      addon.error ||
      'request failed',
  )
  const fix = str(
    nested.fix ||
      value.fix ||
      diagnosis.fix ||
      addon.fix ||
      value.hint ||
      diagnosis.hint ||
      addon.hint ||
      'inspect the evidence, apply the documented configuration, then retry',
  )
  const fallback = isRecord(nested.fallback)
    ? nested.fallback
    : isRecord(value.fallback)
      ? value.fallback
      : {
          kind: AVAILABILITY_FALLBACK_KINDS[0],
          summary: str(nested.fallback || value.fallback) || 'operation unavailable',
        }
  const evidence = isRecord(nested.evidence)
    ? nested.evidence
    : isRecord(value.evidence)
      ? value.evidence
      : { code: value.code || value.errorCode || null }
  return {
    ...value,
    stage,
    root_cause: rootCause,
    fix,
    fallback,
    evidence,
  }
}
