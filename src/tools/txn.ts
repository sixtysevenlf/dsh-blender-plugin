import { vTool } from '../contracts/tool.js'
import { ANY_SCHEMA } from './schema-diagnostic.js'
import { T } from '../constants.js'
import { ensureBackend } from '../host/backend.js'
import { backendPost, leasedText } from '../host/http.js'
import { renderOne } from '../ui/cards.js'

export function registerTxn(ctx: any, port: number): void {
  ctx.effect(
    () =>
      ctx.tools.register(
        vTool({
          name: 'blender_rt_txn',
          description:
            '【事务 / 回滚】两级快照：**文件级** snapshot/restore（整场景回退；写 .blend 副本，用 copy=True 所以不改当前 filepath；大场景有 MB 级成本，本机 300 对象工程实测约 97MB / 0.8s）+ **对象级** mark/revert（只记 transform / 材质槽 / 可见性 / 修改器开关，毫秒级、就地回滚）。' +
            '用途："整个场景推倒重来之前先留个点"（snapshot）、"只改一处再对比"（mark 再 revert）。' +
            'restore 默认 into="auto"：载入快照后把会话放回**快照的出处文件**（保存落回自己的工程，不写进快照目录），并在载入前自动留 pre_restore 备份；into="snapshot" 非破坏（只看一眼、不写原文件）。' +
            '⚠️ 边界：对象级**不含拓扑/UV/顶点级改动** —— Boolean、合并、删面之后回不去（revert 会跳过并报告），那种回滚请用文件级 snapshot/restore。',
          parameters: {
            op: { type: 'string', required: true, description: 'snapshot | restore | list | prune | mark | revert | marks | drop | help' },
            label: { type: 'string', description: '快照 / mark 的名字（snapshot 省略则用时间戳）' },
            objects: { type: 'string', description: 'mark 时限定对象（逗号分隔；省略 = 全场景）' },
            keep: { type: 'integer', description: 'prune 只保留最近 N 个快照，默认 5' },
            note: { type: 'string', description: 'snapshot 备注' },
            into: {
              type: 'string',
              description:
                'restore 回写口径：auto（默认，回到快照的出处文件）| snapshot（非破坏，不写原文件）| original（必须能回写，否则失败）'
            },
            backup: { type: 'boolean', description: 'restore 前是否自动留 pre_restore 备份（默认 true）' }
          },
          output: { schema: ANY_SCHEMA, render: renderOne },
          isConcurrencySafe: () => false,
          timeoutMs: T.txn,
          async execute(args: any) {
            if (!(await ensureBackend(port))) return { text: '后端不可用（127.0.0.1:' + String(port) + '）' }
            const op = String((args && args.op) || 'list')
            const a: any = {}
            if (args && args.label) a.label = String(args.label)
            if (args && args.note) a.note = String(args.note)
            if (args && args.keep) a.keep = Number(args.keep)
            if (args && args.into) a.into = String(args.into)
            if (args && typeof args.backup === 'boolean') a.backup = args.backup
            if (args && args.objects)
              a.objects = String(args.objects)
                .split(',')
                .map((s) => s.trim())
                .filter(Boolean)
            const r = await backendPost(port, '/txn', { op: op, args: a }, 300000)
            const lt = leasedText(r)
            if (lt) return { text: lt }
            if (!r || r.ok !== true) return { text: 'TXN ' + op + ' 失败 · ' + String((r && (r.error || r.raw)) || 'unknown') }
            const res = (r && r.result) || {}
            const parts: string[] = []
            parts.push('TXN ' + op + ' ok')
            if (op === 'snapshot' && res.snapshot)
              parts.push(
                '快照 ' +
                  String(res.snapshot.label) +
                  ' · ' +
                  String(res.snapshot.bytes) +
                  'B · ' +
                  String(res.snapshot.ms) +
                  'ms · ' +
                  String(res.snapshot.objects) +
                  ' 对象' +
                  String.fromCharCode(10) +
                  '路径：' +
                  String(res.snapshot.path)
              )
            else if (op === 'restore')
              parts.push(
                '已恢复：' + JSON.stringify(res.restored || {}).slice(0, 200) + String.fromCharCode(10) + String(res.warning || '')
              )
            else if (op === 'list') {
              parts.push(
                '快照 ' +
                  String((res.snapshots || []).length) +
                  ' 个：' +
                  (res.snapshots || []).map((s: any) => s.label + '(' + s.bytes + 'B)').join(' · ')
              )
              parts.push('mark：' + JSON.stringify(res.marks || []))
              parts.push('目录：' + String(res.snapshot_dir || ''))
            } else if (op === 'mark') parts.push('已记 mark ' + String(res.mark) + ' · ' + String(res.objects) + ' 个对象')
            else if (op === 'revert')
              parts.push(
                '回滚 ' +
                  String(res.reverted) +
                  '：改了 ' +
                  String(res.changed_count) +
                  ' 个' +
                  (res.blocked && res.blocked.length ? ' · 跳过 ' + JSON.stringify(res.blocked).slice(0, 200) : '')
              )
            else parts.push(JSON.stringify(res).slice(0, 800))
            return { text: parts.join(String.fromCharCode(10)) }
          }
        })
      ),
    '@dsh-external/dsh-blender-plugin: rt-txn'
  )
}
