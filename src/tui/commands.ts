import { asArray, asRecord, text } from '../contracts/dynamic.js'
import { NL } from './constants.js'
import { footerField } from './footer.js'
import { getJson } from './http.js'
import type { TuiHostOptions } from './types.js'

export const SUBCOMMANDS = [
  { name: 'status', description: 'Blender 通道状态（零 Blender 往返）' },
  { name: 'doctor', description: '体检 GUI 通道（真跑一次 bpy 往返）' },
  { name: 'jobs', description: 'headless 作业列表' },
]

function ago(ms: unknown): string {
  if (ms == null) return '从未'
  const s = Math.round(Number(ms) / 1000)
  return s < 60 ? String(s) + ' s 前' : String(Math.round(s / 60)) + ' min 前'
}

/** '/blender <sub>' → 文本。导出给自检。 */
export async function blenderCommand(
  rawInput: string,
  opts: TuiHostOptions,
  signal?: AbortSignal,
): Promise<{ kind: string; text: string }> {
  const sub =
    String(rawInput || '')
      .trim()
      .split(/\s+/)[0] || 'status'
  const port = opts.port
  if (sub === 'status') {
    const st = await getJson(port, '/host/state', signal)
    const f = footerField(st, opts.holder, port)
    if (!st || st.ok !== true) return { kind: 'success', text: f.text + NL + f.detail }
    const ch = asRecord(st.channel)
    const lease = asRecord(st.lease)
    const lines = [
      f.text + ' — ' + f.detail,
      '通道：上次成功 ' +
        ago(ch.lastOkAgeMs) +
        ' · 进行中 ' +
        String(ch.inflight || 0) +
        ' · 上次命令 ' +
        (text(ch.lastCmd) || '-'),
      '租约：' +
        (lease.active
          ? text(lease.holder) + (lease.holder === opts.holder ? '（本会话）' : '')
          : '空闲'),
      '渲染：' +
        (asRecord(st.render).busy
          ? '进行中（' + ago(asRecord(st.render).ageMs).replace(' 前', '') + '）'
          : '无'),
      '作业：' + String(asRecord(st.jobs).running || 0) + ' 个在跑',
    ]
    if (ch.lastError)
      lines.push('最近错误（' + ago(ch.lastErrorAgeMs) + '）：' + text(ch.lastError))
    return { kind: 'success', text: lines.join(NL) }
  }
  if (sub === 'doctor') {
    const d = await getJson(port, '/doctor', signal, 30000)
    if (!d)
      return {
        kind: 'error',
        text:
          '体检失败：后端不可达（127.0.0.1:' +
          String(port) +
          '）· 让模型跑 blender_viewport(op="start")',
      }
    const a = asRecord(d.addon)
    const lines = ['诊断：' + (text(a.kind) || (d.ok ? 'ok' : 'unknown'))]
    if (a.summary) lines.push('结论：' + text(a.summary))
    if (a.fix) lines.push('修法：' + text(a.fix))
    return { kind: d.ok ? 'success' : 'error', text: lines.join(NL) }
  }
  if (sub === 'jobs') {
    const j = await getJson(port, '/host/jobs?limit=10', signal)
    if (!j || j.ok !== true)
      return { kind: 'error', text: '取作业列表失败：后端不可达（127.0.0.1:' + String(port) + '）' }
    const jobs = asArray(j.jobs)
    if (!jobs.length) return { kind: 'success', text: '没有 headless 作业' }
    const lines = [
      '作业 ' + String(j.running || 0) + ' 个在跑 / 共 ' + String(j.total || jobs.length) + ' 个：',
    ]
    for (const job of jobs) {
      const rec = asRecord(job)
      lines.push(
        '  ' +
          (text(rec.id) || '?') +
          ' · ' +
          (text(rec.status) || '?') +
          ' · ' +
          String(Math.round(Number(rec.ms || 0) / 1000)) +
          ' s' +
          (rec.script ? ' · ' + (text(rec.script).split(/[\\/]/).pop() ?? '') : ''),
      )
    }
    return { kind: 'success', text: lines.join(NL) }
  }
  return {
    kind: 'error',
    text: '未知子命令「' + sub + '」：可用 ' + SUBCOMMANDS.map((s) => s.name).join(' / '),
  }
}
