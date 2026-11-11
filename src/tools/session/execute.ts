/**
 * blender_session：GUI 会话的生命周期、归属、写权限 scope、pause 与 txn 子面。
 *
 * Gateway 进程本身由宿主管理；这个工具不暴露 launch / lease / kill 运维动作。
 */
import { BACKEND_HINT } from '../../constants.js'
import { asRecord, text } from '../../contracts/dynamic.js'
import { ensureBackend, getLastBackendFailure } from '../../host/backend/index.js'
import { virtualGuiAllowed } from '../../host/virtual-gui-permission.js'
import { backendJson } from '../../host/http.js'
import { toolError } from '../errors.js'
import { gatewayFailure, isFailureBody, listSessions, sessionAction } from '../shared/session.js'
import {
  formatDoctor,
  formatSessionList,
  formatSessionResult,
  formatStatus,
  sessionHelp,
} from './presenter.js'
import { executeTxn } from './txn.js'

const CAMEL: Record<string, string> = {
  session_id: 'sessionId',
  instance_id: 'instanceId',
  kind: 'kind',
  grant_id: 'grantId',
  ttl_ms: 'ttlMs',
  target_agent_id: 'targetAgentId',
  target_dsh_session_id: 'targetDshSessionId',
  target_host_id: 'targetHostId',
  force: 'force',
}

function present(v: unknown): boolean {
  return v !== undefined && v !== null && String(v).trim() !== ''
}

/** scopes 接受数组、JSON 数组字符串，或逗号分隔字符串。 */
function parseScopes(raw: unknown): string[] | undefined {
  if (raw === undefined || raw === null) return undefined
  if (Array.isArray(raw)) return raw.map((x) => String(x)).filter(Boolean)
  const s = String(raw).trim()
  if (!s) return undefined
  if (s.startsWith('[')) {
    try {
      const parsed: unknown = JSON.parse(s)
      if (Array.isArray(parsed)) return parsed.map((x) => String(x)).filter(Boolean)
    } catch {
      /* 不是 JSON，按逗号串处理 */
    }
  }
  return s
    .split(',')
    .map((x) => x.trim())
    .filter(Boolean)
}

function payloadFor(action: string, args: Record<string, unknown>): Record<string, unknown> {
  const p: Record<string, unknown> = {}
  for (const [snake, camel] of Object.entries(CAMEL)) {
    if (present(args[snake])) p[camel] = args[snake]
  }
  const scopes = parseScopes(args.scopes ?? args.scope)
  if (scopes !== undefined) p.scopes = scopes
  if (action === 'start') {
    p.kind = present(args.kind) ? text(args.kind) : 'virtual-gui'
    // 许可在宿主工具面判定；Gateway 只负责 feature/provider/容量门。
  }
  return p
}

/** Model-callable actions only. Internal host actions (update_*,
 * presence_notice) are deliberately absent: accepting an arbitrary action here
 * bypassed the host-side checks. */
const ACTIONS = new Set([
  'list',
  'status',
  'doctor',
  'attach',
  'claim',
  'detach',
  'release',
  'grant',
  'revoke',
  'grants',
  'pause',
  'resume',
  'mark_read',
  'presence',
  'heartbeat',
  'start',
  'stop',
  'txn',
  'help',
])

const NEED_SESSION = [
  'detach',
  'release',
  'stop',
  'pause',
  'resume',
  'mark_read',
  'presence',
  'heartbeat',
  'grants',
  'grant',
  'revoke',
]

function validate(action: string, args: Record<string, unknown>): string | null {
  if (action === 'start') {
    const kind = present(args.kind) ? text(args.kind) : 'virtual-gui'
    if (kind !== 'virtual-gui')
      return 'start 只启动 virtual-gui；user-gui 由用户在 Blender 里打开并 Connect 后 action=attach'
    return null
  }
  if (NEED_SESSION.includes(action) && !present(args.session_id)) return action + ' 需要 session_id'
  if (action === 'grant' && parseScopes(args.scopes ?? args.scope) === undefined)
    return 'grant 需要 scopes（exclusive | collection:名 | prefix:名 | object:名）'
  if (action === 'revoke' && !present(args.grant_id)) return 'revoke 需要 grant_id'
  if (action === 'mark_read' && parseScopes(args.scopes ?? args.scope) === undefined)
    return 'mark_read 需要 scopes（exclusive | collection:名 | prefix:名 | object:名）'
  return null
}

async function statusOf(port: number): Promise<unknown> {
  const listed = await listSessions(port)
  if (!listed.ok) return listed.failure
  let body: Record<string, unknown>
  try {
    body = await backendJson(port, '/status', 20000)
  } catch (e: unknown) {
    return toolError(
      'gateway_unreachable',
      'status 失败 · ' + text(e instanceof Error ? e.message : e),
      BACKEND_HINT,
    )
  }
  if (body.ok === false && !body.readiness && !body.running)
    return toolError(
      'backend_error',
      'status 失败 · ' + text(body.error || body.raw || 'unknown'),
      '先 blender_session action=doctor 看 Gateway/addon 状态',
    )
  return { text: formatStatus(body, listed.sessions) }
}

async function doctorOf(port: number): Promise<unknown> {
  let body: Record<string, unknown>
  try {
    body = await backendJson(port, '/doctor', 60000)
  } catch (e: unknown) {
    return toolError(
      'gateway_unreachable',
      'doctor 失败 · ' + text(e instanceof Error ? e.message : e),
      BACKEND_HINT,
    )
  }
  if (body.ok === false && !body.checks && !body.kind && !body.error)
    return toolError('backend_error', 'doctor 失败 · ' + text(body.raw || 'unknown'), BACKEND_HINT)
  if (body.ok === false && body.error && !body.checks && !body.kind && !body.root_cause)
    return toolError(
      'backend_error',
      'doctor 失败 · ' + text(body.error),
      'Gateway 由宿主管理：重启 DSH 插件后重试；addon 需在 Blender N 面板点 Connect',
    )
  return { text: formatDoctor(body) }
}

function notAllowed(): ReturnType<typeof toolError> {
  return toolError(
    'virtual_gui_not_allowed',
    'virtual-gui 未获人类允许',
    '在 DSH 侧栏 Plugins → Blender 配置卡（或右侧栏 Blender Presence 面板）打开“允许 agent 启动 virtual-gui”',
    undefined,
    'session',
  )
}

export async function executeSession(
  port: number,
  args: Record<string, unknown>,
): Promise<unknown> {
  const action = text(args.action).trim() || 'list'
  if (!ACTIONS.has(action))
    return toolError(
      'invalid_params',
      '未知的 session action：' + action,
      'action=help 查看模型可用的动作',
    )
  const bad = validate(action, args)
  if (bad) return toolError('invalid_params', bad, 'action=help 查看可用动作与参数')
  // 宿主级许可门：默认拒绝，且先于后端探测/拉起 Gateway。
  if (action === 'start' && !virtualGuiAllowed()) return notAllowed()
  if (action === 'help') return { text: sessionHelp() }
  if (!(await ensureBackend(port))) {
    const failure = getLastBackendFailure()
    if (failure) return toolError(failure.code, failure.root_cause, failure.fix)
    return toolError(
      'backend_unreachable',
      '后端不可用（127.0.0.1:' + String(port) + '）',
      BACKEND_HINT,
    )
  }
  if (action === 'txn') return executeTxn(port, args)
  if (action === 'status') return statusOf(port)
  if (action === 'doctor') return doctorOf(port)
  if (action === 'list') {
    const listed = await listSessions(port)
    if (!listed.ok) return listed.failure
    return { text: formatSessionList(listed.sessions) }
  }
  const body = await sessionAction(port, action, payloadFor(action, args))
  if (isFailureBody(body)) return gatewayFailure(body, 'SESSION ' + action)
  return { text: formatSessionResult(action, asRecord(body)) }
}
