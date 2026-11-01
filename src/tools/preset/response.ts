/** preset 响应文本：list 与 apply 的摘要渲染。 */
import { asArray, asRecord, text } from '../../contracts/dynamic.js'

interface PresetRow {
  name: string
  kind: string
  keys: number
}

const NL = String.fromCharCode(10)

export function formatPreset(op: string, res: Record<string, unknown>): string {
  const parts: string[] = []
  parts.push('PRESET ' + op + ' ok')
  if (op === 'list') {
    const presets: PresetRow[] = asArray(res.presets).map((row) => {
      const p = asRecord(row)
      return { name: text(p.name), kind: text(p.kind), keys: Number(p.keys ?? 0) }
    })
    parts.push(
      '共 ' +
        String(res.count ?? presets.length) +
        ' 个：' +
        presets.map((p) => p.name + '(' + p.kind + ',' + String(p.keys) + '键)').join(' · '),
    )
    parts.push('目录：' + text(res.dir))
  } else if (op === 'apply') {
    const rr = asRecord(res.report)
    const applied = asArray(rr.applied)
    const skipped = asArray(rr.skipped)
    const errors = asArray(rr.errors)
    parts.push(
      '配方 ' +
        text(res.preset) +
        ' · 目标 ' +
        JSON.stringify(res.targets) +
        ' · 写入 ' +
        String(applied.length) +
        ' 项',
    )
    if (skipped.length) parts.push('跳过：' + JSON.stringify(skipped).slice(0, 300))
    if (errors.length) parts.push('错误：' + JSON.stringify(errors).slice(0, 300))
  } else parts.push(JSON.stringify(res).slice(0, 900))
  return parts.join(NL)
}
