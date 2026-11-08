import { asArray, asRecord, text } from '../contracts/dynamic.js'
import { NL } from './constants.js'
import { footerField } from './footer.js'
import { getJson } from './http.js'
import type { Json } from './http.js'
import { runHostInstall } from './install.js'
import type { HostInstallPlan } from './install.js'
import type { TuiHostOptions } from './types.js'

export const SUBCOMMANDS = [
  { name: 'status', description: 'Blender 通道状态（零 Blender 往返）' },
  {
    name: 'doctor',
    description: '体检 GUI 通道（真跑一次 bpy 往返；--fix 引导安装 virtual-gui 前提）',
  },
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
  const parts = String(rawInput || '')
    .trim()
    .split(/\s+/)
    .filter(Boolean)
  const sub = parts[0] || 'status'
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
          '）· 让模型跑 blender_session action=status',
      }
    if (parts.includes('--fix')) return await doctorFix(port, d, opts, signal)
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

function normalizeInstallPlan(raw: Record<string, unknown>): HostInstallPlan {
  const argv = asArray(raw.oneClick)
    .map((value) => text(value))
    .filter(Boolean)
  return {
    id: text(raw.id) || 'unknown',
    label: text(raw.label) || text(raw.id) || 'unknown',
    ok: raw.ok === true,
    needsRoot: raw.needsRoot === true,
    supported: raw.supported === true,
    oneClick: argv.length ? argv : null,
    command: text(raw.command) || null,
    reason: text(raw.reason) || undefined,
  }
}

/** '/blender doctor --fix': show the shared install plans and run supported one-click ones. */
async function doctorFix(
  port: number,
  doctor: Json,
  opts: TuiHostOptions,
  signal?: AbortSignal,
): Promise<{ kind: string; text: string }> {
  const virtualGui = asRecord(asRecord(doctor).virtualGui)
  const plans = asArray(asRecord(virtualGui).installPlans).map((raw) =>
    normalizeInstallPlan(asRecord(raw)),
  )
  if (plans.length === 0) {
    const fix = text(asRecord(virtualGui).fix)
    return {
      kind: 'error',
      text:
        '没有安装计划：先确认 virtual-gui provider 已实现并报告 prerequisites。' +
        (fix ? NL + '修法：' + fix : ''),
    }
  }
  const missing = plans.filter((plan) => !plan.ok)
  if (missing.length === 0) return { kind: 'success', text: 'virtual-gui 前提已就绪，无需安装。' }
  const lines = ['virtual-gui 前提（缺失 ' + String(missing.length) + ' 项）：']
  const runner = opts.installRunner || runHostInstall
  let ran = false
  for (const plan of missing) {
    lines.push('- ' + plan.label + '（' + plan.id + '）')
    if (plan.command) lines.push('  命令：' + plan.command)
    if (plan.supported && plan.oneClick) {
      lines.push('  需要提权：' + (plan.needsRoot ? '是' : '否') + ' · 一键安装中…')
      const result = await runner(plan)
      ran = true
      lines.push('  结果：' + (result.ok ? '成功' : '失败') + ' · ' + result.detail)
    } else {
      lines.push(
        '  需手动执行' +
          (plan.needsRoot ? '（需要管理员/root）' : '') +
          (plan.reason ? '：' + plan.reason : ''),
      )
    }
  }
  if (!ran) {
    lines.push('没有可一键安装的前提；按上面的命令安装后重新运行 /blender doctor --fix。')
    return { kind: 'error', text: lines.join(NL) }
  }
  const after = await getJson(port, '/doctor', signal, 30000)
  const vg = asRecord(asRecord(after).virtualGui)
  const ready = asRecord(vg).ready === true
  const stillMissing = asArray(asRecord(vg).missing).map((value) => text(value))
  lines.push(
    '复检：' +
      (ready ? 'ready' : '仍未就绪') +
      (stillMissing.length ? ' · missing=' + stillMissing.join(', ') : ''),
  )
  if (!ready) lines.push('安装未完成或未生效；保留原始 stderr，不要报告成功。')
  return { kind: ready ? 'success' : 'error', text: lines.join(NL) }
}
