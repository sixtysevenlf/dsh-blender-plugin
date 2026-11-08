// Structured display-provider failures shared by every platform provider.
//
// The session layer turns the `view()` object into the same availability
// envelope as the rest of the Gateway, so a provider must fail with stage,
// code, root cause, fix and evidence instead of a bare Error.
export class DisplayProviderError extends Error {
  readonly stage: string
  readonly code: string
  readonly fix: string
  readonly evidence: Record<string, unknown>

  constructor(
    stage: string,
    code: string,
    message: string,
    fix: string,
    evidence: Record<string, unknown> = {},
  ) {
    super(message)
    this.name = 'DisplayProviderError'
    this.stage = stage
    this.code = code
    this.fix = fix
    this.evidence = evidence
  }

  /** The availability-envelope-shaped view used by callers and tests. */
  view(): Record<string, unknown> {
    return {
      ok: false,
      stage: this.stage,
      code: this.code,
      root_cause: this.message,
      fix: this.fix,
      evidence: this.evidence,
    }
  }
}
