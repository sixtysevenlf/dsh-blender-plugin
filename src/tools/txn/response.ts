/** txn 响应文本：按 op 渲染 snapshot/restore/list/mark/revert 各自的摘要。 */
import { asArray, asRecord, text } from '../../contracts/dynamic.js'

const NL = String.fromCharCode(10)

export function formatTxn(op: string, res: Record<string, unknown>): string {
  const parts: string[] = []
  parts.push('TXN ' + op + ' ok')
  const snapshot = asRecord(res.snapshot)
  if (op === 'snapshot' && res.snapshot)
    parts.push(
      '快照 ' +
        text(snapshot.label) +
        ' · ' +
        text(snapshot.bytes) +
        'B · ' +
        text(snapshot.ms) +
        'ms · ' +
        text(snapshot.objects) +
        ' 对象' +
        NL +
        '路径：' +
        text(snapshot.path),
    )
  else if (op === 'restore')
    parts.push(
      '已恢复：' + JSON.stringify(res.restored || {}).slice(0, 200) + NL + text(res.warning),
    )
  else if (op === 'list') {
    const snapshots = asArray(res.snapshots)
    parts.push(
      '快照 ' +
        String(snapshots.length) +
        ' 个：' +
        snapshots
          .map((raw) => {
            const s = asRecord(raw)
            return text(s.label) + '(' + text(s.bytes) + 'B)'
          })
          .join(' · '),
    )
    parts.push('mark：' + JSON.stringify(res.marks || []))
    parts.push('目录：' + text(res.snapshot_dir))
  } else if (op === 'mark')
    parts.push('已记 mark ' + text(res.mark) + ' · ' + text(res.objects) + ' 个对象')
  else if (op === 'revert') {
    const blocked = asArray(res.blocked)
    parts.push(
      '回滚 ' +
        text(res.reverted) +
        '：改了 ' +
        text(res.changed_count) +
        ' 个' +
        (blocked.length ? ' · 跳过 ' + JSON.stringify(blocked).slice(0, 200) : ''),
    )
  } else parts.push(JSON.stringify(res).slice(0, 800))
  return parts.join(NL)
}
