/** blender_session 的呈现层：会话列表、动作回执、status/doctor 摘要。 */
import { asRecord, isRecord, text } from '../../contracts/dynamic.js'
import type { SessionRow } from '../shared/session.js'

const LF = String.fromCharCode(10)
const CLIP = 6000

function clip(v: unknown, max = CLIP): string {
  const s = typeof v === 'string' ? v : JSON.stringify(v)
  return s.length > max ? s.slice(0, max) + ' …' : s
}

export function formatSessionList(sessions: SessionRow[]): string {
  if (!sessions.length)
    return (
      'SESSION list · 0 个会话' +
      LF +
      '没有可见会话：在 Blender N 面板点 Connect 后 blender_session action=attach（多实例必须带 instance_id），' +
      '或经用户同意 action=start kind=virtual-gui。'
    )
  const lines = ['SESSION list · ' + String(sessions.length) + ' 个会话']
  for (const s of sessions) {
    const agents = Array.isArray(s.agents) ? s.agents.length : 0
    lines.push(
      '· ' +
        text(s.id) +
        ' · ' +
        text(s.kind) +
        ' · ' +
        text(s.status) +
        ' · instance=' +
        text(s.instanceId || '-') +
        ' · owner=' +
        text(asRecord(s.owner).agentId || '-') +
        ' · agents=' +
        String(agents) +
        (s.paused ? ' · PAUSED(human)' : '') +
        (s.humanModified ? ' · human_modified' : ''),
    )
  }
  return lines.join(LF)
}

export function formatSessionResult(action: string, body: Record<string, unknown>): string {
  const s = asRecord(body.session)
  const head = 'SESSION ' + action + ' ok'
  if (action === 'grants') return head + LF + clip(body.grants ?? [])
  if (action === 'presence') return head + LF + clip(body.presence ?? body.sessions ?? [])
  if (
    action === 'attach' ||
    action === 'claim' ||
    action === 'start' ||
    action === 'stop' ||
    action === 'detach' ||
    action === 'release'
  )
    return (
      head +
      (s.id
        ? LF +
          'session=' +
          text(s.id) +
          ' · kind=' +
          text(s.kind) +
          ' · status=' +
          text(s.status) +
          ' · instance=' +
          text(s.instanceId || '-') +
          ' · owner=' +
          text(asRecord(s.owner).agentId || '-')
        : LF + clip(body))
    )
  if (action === 'grant') return head + LF + clip(body.grant ?? body.grants ?? body)
  if (action === 'revoke') return head + (body.revoked === false ? ' · 未找到该 grant' : '')
  const rest: Record<string, unknown> = {}
  for (const k of Object.keys(body)) if (k !== 'ok') rest[k] = body[k]
  return head + (Object.keys(rest).length ? LF + clip(rest) : '')
}

export function formatStatus(body: Record<string, unknown>, sessions: SessionRow[]): string {
  const readiness = asRecord(body.readiness)
  const addon = asRecord(readiness.addon)
  const vg = asRecord(body.virtualGui)
  const lines = ['SESSION status']
  lines.push(
    'gateway  ' + (readiness.gateway ? 'up' : 'down') + (body.running ? ' · backend running' : ''),
  )
  lines.push(
    'addon    ' +
      (addon.connected ? 'connected' : 'disconnected') +
      ' · ready_instances=' +
      String(addon.ready_instances ?? 0) +
      (addon.instanceId ? ' · instance=' + text(addon.instanceId) : '') +
      (addon.kind ? ' · kind=' + text(addon.kind) : '') +
      (addon.handshake_error ? ' · handshake_error=' + text(addon.handshake_error) : ''),
  )
  lines.push('bpy      ' + (readiness.bpy ? 'ready' : 'not ready'))
  if (body.addonError) lines.push('addonError ' + text(body.addonError))
  if (vg.enabled !== undefined)
    lines.push(
      'virtual-gui ' +
        (vg.enabled ? 'enabled' : 'disabled') +
        (vg.ready ? ' · ready' : '') +
        (vg.preview ? ' · preview-only' : '') +
        (vg.reason ? ' · reason=' + text(vg.reason) : ''),
    )
  lines.push(formatSessionList(sessions))
  return lines.join(LF)
}

export function formatDoctor(body: Record<string, unknown>): string {
  const lines = [
    'SESSION doctor · ok=' +
      String(body.ok === true) +
      (body.kind ? ' · kind=' + text(body.kind) : '') +
      (body.protocol !== undefined ? ' · protocol=' + text(body.protocol) : ''),
  ]
  if (body.root_cause) lines.push('root_cause: ' + text(body.root_cause))
  if (body.fix) lines.push('fix: ' + text(body.fix))
  const checks = body.checks
  if (Array.isArray(checks)) {
    for (const c of checks) {
      const row = asRecord(c)
      lines.push(
        '· ' +
          text(row.name || row.id || '?') +
          ' ' +
          (row.ok === false ? 'FAIL' : 'ok') +
          (row.detail ? ' · ' + text(row.detail) : ''),
      )
    }
  }
  const vg = asRecord(body.virtualGui)
  if (vg.enabled !== undefined)
    lines.push(
      'virtual-gui ' +
        (vg.enabled ? 'enabled' : 'disabled') +
        (vg.ready ? ' · ready' : '') +
        (vg.reason ? ' · reason=' + text(vg.reason) : ''),
    )
  if (!checks && !body.root_cause) lines.push(clip(body))
  return lines.join(LF)
}

export function sessionHelp(): string {
  return [
    'blender_session 动作（会话生命周期与归属；不含 Gateway 运维）：',
    '  list | status | doctor | attach | claim | detach | release',
    '  grant / revoke / grants（写权限 scope：exclusive | collection:名 | prefix:名 | object:名）',
    '  pause / resume（只有人类能暂停；agent 调用返回 human_action_required）',
    '  mark_read（清掉 human_modified 冲突）| presence | heartbeat',
    '  start / stop（只作用 virtual-gui；start 需人类先在 Blender Presence 面板允许，默认不允许）',
    '  txn（子动作 op=snapshot|restore|list|prune|mark|revert|marks|drop|help；写快照/回滚需 exclusive grant）',
    '旧工具迁移：blender_viewport → blender_session（status/doctor）+ blender_see；blender_rt_txn → blender_session action=txn。',
  ].join(LF)
}

export function isSessionRow(v: unknown): boolean {
  const r = asRecord(v)
  return isRecord(r) && typeof r.id === 'string'
}
