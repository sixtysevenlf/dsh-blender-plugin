/**
 * virtual-gui 的宿主安装/状态 RPC。
 *
 * 浏览器侧 DSH Plugins 页面只负责展示与收集人类动作：开关、缺失依赖、安装进度。
 * 安装命令只来自 Gateway /doctor 的 virtualGui.installPlans，宿主只执行计划里
 * 已经展示给用户的 oneClick argv；手动步骤原样返回，不在这里拼包管理器命令。
 *
 * 这是宿主进程能力，不是模型工具：只有 DSH 认证的 owner peer 可以调用。
 */
import { asArray, asRecord, text } from '../contracts/dynamic.js'
import { ensureBackend, getLastBackendFailure } from './backend/index.js'
import { backendJson } from './http.js'
import { runHostInstall } from '../tui/install.js'
import type { HostInstallPlan } from '../tui/install.js'
/** Shape of the host permission snapshot returned by the permission module. */
export interface VirtualGuiPermissionSnapshot {
  allowed: boolean
  source: string
  updatedAt: number | null
}

export interface VirtualGuiInstallView {
  state: 'idle' | 'installing' | 'done' | 'failed'
  planId: string
  label: string
  startedAt: number | null
  finishedAt: number | null
  ok: boolean | null
  detail: string
  lines: string[]
}

export interface VirtualGuiPanelDeps {
  ensureBackend?: (port: number) => Promise<boolean>
  doctor?: (port: number) => Promise<Record<string, unknown>>
  startInstall?: (plan: HostInstallPlan) => VirtualGuiInstallView
}

interface PanelFailure {
  ok: false
  error: { code: string; message: string; details?: Record<string, unknown> }
}

interface PanelSuccess {
  ok: true
  value: Record<string, unknown>
}

const INSTALL_LINE_LIMIT = 8
const INSTALL_LINE_CHARS = 200

function idleInstall(): VirtualGuiInstallView {
  return {
    state: 'idle',
    planId: '',
    label: '',
    startedAt: null,
    finishedAt: null,
    ok: null,
    detail: '',
    lines: [],
  }
}

let install: VirtualGuiInstallView = idleInstall()

/** 插件重载/测试之间清掉上一次安装进度。 */
export function resetVirtualGuiInstall(): void {
  install = idleInstall()
}

export function virtualGuiInstallView(): VirtualGuiInstallView {
  return { ...install, lines: install.lines.slice() }
}

function appendInstallOutput(chunk: string): void {
  const lines = String(chunk)
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => line.slice(0, INSTALL_LINE_CHARS))
  if (lines.length === 0) return
  install = {
    ...install,
    lines: install.lines.concat(lines).slice(-INSTALL_LINE_LIMIT),
  }
}

/** 启动一次已通过校验的一键安装；返回立即可见的 installing 快照。 */
export function startVirtualGuiInstall(
  plan: HostInstallPlan,
  runner: (
    plan: HostInstallPlan,
    onLine?: (chunk: string) => void,
  ) => Promise<{
    ok: boolean
    detail: string
  }> = runHostInstall,
): VirtualGuiInstallView {
  install = {
    state: 'installing',
    planId: plan.id,
    label: plan.label,
    startedAt: Date.now(),
    finishedAt: null,
    ok: null,
    detail: '',
    lines: [],
  }
  void runner(plan, appendInstallOutput)
    .then((result) => {
      install = {
        ...install,
        state: result.ok ? 'done' : 'failed',
        finishedAt: Date.now(),
        ok: result.ok,
        detail: String(result.detail || '').slice(0, 500),
      }
    })
    .catch((error: unknown) => {
      install = {
        ...install,
        state: 'failed',
        finishedAt: Date.now(),
        ok: false,
        detail: String(error instanceof Error ? error.message : error).slice(0, 500),
      }
    })
  return virtualGuiInstallView()
}

function planFrom(value: unknown): HostInstallPlan | null {
  const row = asRecord(value)
  const id = text(row.id)
  if (!id) return null
  const oneClick = asArray(row.oneClick)
    .map((item) => text(item))
    .filter((item) => item.length > 0)
  const steps = asArray(row.steps)
    .map((item) => {
      const step = asRecord(item)
      const command = text(step.command)
      if (!command) return null
      return { description: text(step.description) || command, command }
    })
    .filter((step): step is { description: string; command: string } => step !== null)
  return {
    id,
    label: text(row.label) || id,
    ok: row.ok === true,
    needsRoot: row.needsRoot === true,
    supported: row.supported === true && oneClick.length > 0,
    oneClick: oneClick.length > 0 ? oneClick : null,
    command: text(row.command) || null,
    steps,
    reason: text(row.reason) || undefined,
  }
}

/** 把 /doctor 的动态 installPlans 收窄成宿主可执行的计划；非法行直接丢弃。 */
export function normalizeInstallPlans(value: unknown): HostInstallPlan[] {
  return asArray(value)
    .map(planFrom)
    .filter((plan): plan is HostInstallPlan => plan !== null)
}

function failure(code: string, message: string, details?: Record<string, unknown>): PanelFailure {
  const error: PanelFailure['error'] = { code, message }
  if (details) error.details = details
  return { ok: false, error }
}

function ownerPeer(peer: unknown): boolean {
  const principal = asRecord(peer).principal
  return asRecord(principal).kind === 'owner'
}

async function defaultDoctor(port: number): Promise<Record<string, unknown>> {
  return backendJson(port, '/doctor', 60000)
}

async function readCapabilities(
  port: number,
  deps: VirtualGuiPanelDeps,
): Promise<{ capability: Record<string, unknown>; plans: HostInstallPlan[] } | PanelFailure> {
  const ensure = deps.ensureBackend || ensureBackend
  const doctor = deps.doctor || defaultDoctor
  let reachable: boolean
  try {
    reachable = await ensure(port)
  } catch {
    reachable = false
  }
  if (!reachable) {
    const ownerFailure = getLastBackendFailure()
    if (ownerFailure) {
      return failure(ownerFailure.code, ownerFailure.root_cause + ' ' + ownerFailure.fix, {
        failure: ownerFailure,
      })
    }
    return failure('backend_unreachable', 'Gateway 不可达；先启动 DSH Blender Gateway')
  }
  let body: Record<string, unknown>
  try {
    body = await doctor(port)
  } catch (error) {
    return failure(
      'doctor_failed',
      '读取 /doctor 失败：' + text(error instanceof Error ? error.message : error),
    )
  }
  const capability = asRecord(body.virtualGui)
  if (Object.keys(capability).length === 0) {
    return failure('virtual_gui_unavailable', '/doctor 没有返回 virtualGui 能力块')
  }
  const plans = normalizeInstallPlans(capability.installPlans)
  const copy = { ...capability }
  delete copy.installPlans
  return { capability: copy, plans }
}

/** RPC handler：op=status | install | install-status。 */
export async function handleVirtualGuiPanel(
  payload: unknown,
  _peer: unknown,
  port: number,
  permission: VirtualGuiPermissionSnapshot,
  deps: VirtualGuiPanelDeps = {},
): Promise<PanelFailure | PanelSuccess> {
  const body = asRecord(payload)
  const op = text(body.op) || 'status'
  if (!ownerPeer(_peer))
    return failure('forbidden', 'virtual-gui settings are controlled by the human owner')
  if (!Number.isFinite(port) || port <= 0)
    return failure('backend_unavailable', 'Gateway port is not configured')
  if (op === 'install-status') {
    return { ok: true, value: { install: virtualGuiInstallView() } }
  }
  if (op === 'install') {
    if (install.state === 'installing')
      return failure('install_in_progress', '已有安装正在进行，等它结束再试')
    const planId = text(body.planId)
    if (!planId) return failure('invalid_params', 'planId must be a non-empty string')
    const read = await readCapabilities(port, deps)
    if ('ok' in read) return read
    const plan = read.plans.find((candidate) => candidate.id === planId)
    if (!plan)
      return failure('plan_not_found', '安装计划不存在或已过期；重新检测依赖后再试', {
        planId,
      })
    if (plan.ok) return failure('already_installed', plan.label + ' 已经就绪', { planId })
    if (!plan.supported || !plan.oneClick) {
      return failure(
        'manual_install_required',
        plan.reason || '该依赖没有一键安装命令，请按显示的步骤手动安装',
        {
          planId,
          command: plan.command,
          steps: plan.steps,
        },
      )
    }
    const started = (deps.startInstall || startVirtualGuiInstall)(plan)
    return { ok: true, value: { started: true, install: started } }
  }
  if (op !== 'status')
    return failure('invalid_params', 'op must be status, install, or install-status')
  const read = await readCapabilities(port, deps)
  if ('ok' in read) return read
  return {
    ok: true,
    value: {
      permission,
      capability: read.capability,
      plans: read.plans,
      install: virtualGuiInstallView(),
    },
  }
}
