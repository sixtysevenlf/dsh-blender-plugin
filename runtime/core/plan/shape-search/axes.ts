// shape-search 参数轴
export interface AxisSpec {
  kind: 'enum' | 'range'
  values?: unknown[]
  min?: number
  max?: number
  step?: number
  integer?: boolean
}

/** Python 字面量（JSON 的 true/false/null 在 Python 里不是这个写法 —— 直接 JSON.stringify 会 NameError） */
export function pyLit(v: unknown): string {
  if (v === null || v === undefined) return 'None'
  if (typeof v === 'boolean') return v ? 'True' : 'False'
  if (typeof v === 'number') return isFinite(v) ? String(v) : 'None'
  if (typeof v === 'string') return JSON.stringify(v)
  if (Array.isArray(v)) return '[' + (v as unknown[]).map(pyLit).join(', ') + ']'
  if (typeof v === 'object') {
    const obj = v as Record<string, unknown>
    return (
      '{' +
      Object.keys(obj)
        .map((k) => JSON.stringify(k) + ': ' + pyLit(obj[k]))
        .join(', ') +
      '}'
    )
  }
  return JSON.stringify(String(v))
}
export const pyIndent = (src: unknown, pad: string): string =>
  String(src)
    .split('\n')
    .map((l) => (l.length ? pad + l : l))
    .join('\n')

/** 一个轴（参数维度）的候选值：enum 直接取 values；range 按 step 切（没给 step 就等分 10 档） */
export function searchAxisValues(spec0: AxisSpec, cap: number): unknown[] {
  if (spec0.kind === 'enum') return (spec0.values || []).slice(0, cap)
  const out: unknown[] = []
  const lo = Number(spec0.min) || 0
  const hi = Number(spec0.max) || 0
  const stepN = Number(spec0.step)
  const step = stepN > 0 ? stepN : (hi - lo) / 9
  if (!(step > 0)) return [lo]
  for (let v = lo; v <= hi + 1e-12 && out.length < cap; v += step) {
    out.push(spec0.integer ? Math.round(v) : Number(v.toPrecision(12)))
  }
  if (out.length < cap && out[out.length - 1] !== hi) out.push(hi)
  return out.length ? out : [lo]
}
