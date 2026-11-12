// Job-argument helpers kept out of spawn-executor.ts (file budget): frame range
// parsing, headless CLI assembly, and the string coercion they share.
import fs from 'node:fs'
import path from 'node:path'

/** Preload tokens must be plain dotted module names; keywords would build invalid code. */
const PY_KEYWORDS = new Set([
  'False',
  'None',
  'True',
  'and',
  'as',
  'assert',
  'async',
  'await',
  'break',
  'class',
  'continue',
  'def',
  'del',
  'elif',
  'else',
  'except',
  'finally',
  'for',
  'from',
  'global',
  'if',
  'import',
  'in',
  'is',
  'lambda',
  'nonlocal',
  'not',
  'or',
  'pass',
  'raise',
  'return',
  'try',
  'while',
  'with',
  'yield',
])

export function asString(value: unknown): string {
  return value === undefined || value === null ? '' : String(value)
}

/**
 * frames accepts a single number, "N", "A-B", or [A, B], with A <= B.
 * The old parser split on '-' and read "1-2-3" as 1..2, "3-" as 3..0 and
 * "-5--1" as 0..5, silently rendering the wrong range.
 */
/** A fixed internal Python entry for render parameters: no caller script is ever accepted. */
export function frameRange(params: Record<string, unknown>): { start: number; end: number } | null {
  const raw = params.frames
  const span = (a: unknown, b: unknown): { start: number; end: number } | null => {
    const start = Number(a)
    const end = Number(b)
    if (!Number.isFinite(start) || !Number.isFinite(end) || start > end) return null
    return { start, end }
  }
  if (Array.isArray(raw)) return raw.length === 2 ? span(raw[0], raw[1]) : null
  if (typeof raw === 'string') {
    const text = raw.trim()
    const m = /^(-?\d+)\s*-\s*(-?\d+)$/.exec(text)
    if (m) return span(m[1], m[2])
    if (/^-?\d+$/.test(text)) return span(text, text)
    return null
  }
  if (typeof raw === 'number') return span(raw, raw)
  return null
}

export function headlessArgs(params: Record<string, unknown>, tempDir: string): string[] {
  const args: string[] = []
  const file = asString(params.file)
  if (file) args.push(file)
  args.push('-b', '--factory-startup')
  const scriptPath = path.join(tempDir, 'job.py')
  const script = asString(params.script)
  if (script) fs.writeFileSync(scriptPath, script, 'utf8')
  // preload: import modules inside the same fresh Blender before the script runs.
  // Only dotted identifiers are accepted so a param can never inject code.
  const preload = asString(params.preload)
    .split(/[,\s]+/)
    .map((m) => m.trim())
    .filter((m) => /^[A-Za-z_][A-Za-z0-9_.]*$/.test(m) && !PY_KEYWORDS.has(m))
  if (preload.length) args.push('--python-expr', 'import ' + preload.join(', '))
  const scriptFile = asString(params.script_file)
  if (scriptFile) args.push('--python', scriptFile)
  else if (script) args.push('--python', scriptPath)
  const extra = params.args
  if (Array.isArray(extra)) args.push(...extra.map(asString))
  return args
}
