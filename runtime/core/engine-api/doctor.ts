// engine API：通道诊断
import {
  ADDON_PROTOCOL_VERSION,
  gatewayHandshakeError,
  gatewayInstances,
} from '../../transport/addon-ws.js'
import type { AddonHandshakeError, AddonInstanceView } from '../../transport/addon-ws.js'
import { FLAGS_CODE, REGION_CODE } from '../flags.js'
import { jsonFromStdout } from '../output/index.js'
import { PLUGIN_VERSION, RUNTIME_DIR, engineProvenance, runtimeFingerprint } from '../evidence.js'
import { wslToWin } from '../../config.js'
import { COMMAND_CATALOG } from '../../catalog/index.js'
import { errorMessage } from '../json.js'
import type { EngineApiDeps } from './index.js'
import { metricsView } from './metrics.js'
import { displayCapabilities } from '../../display/prerequisites.js'
import { buildInstallPlans } from '../../display/install.js'

/**
 * addon 侧阶段预算。宿主 backendJson 默认 20s、T.commands 静态截止 60s；
 * 这里的预算必须落在宿主之前，否则模型只拿到 aborted，看不到任何结构化诊断。
 */
/**
 * /status 的 bpy 往返（REGION_CODE）预算：2.5s。
 *
 * extension/bpy 的硬超时 <=3s，首调失败的第 95 百分位 <=3s；Gateway 路径的传输诊断是
 * 同步的实例表判定（不再串 legacy 9876 的 ~2.5s diagnose），所以半开 addon 的
 * /status 稳态 = 2.5s + 路由开销，落在预算内。
 */
export const ADDON_STATUS_TIMEOUT_MS = 2500
/** /doctor 的 status 阶段预算：2.5s；复用 statusView 的同源 readiness，不重复探测。 */
export const ADDON_DOCTOR_STATUS_TIMEOUT_MS = 2500
/** /commands 的 FLAGS 预算：2s（单层硬超时；commands 断连的 socket closed 不再叠加重试）。 */
export const ADDON_FLAGS_TIMEOUT_MS = 2000
/** /commands 单个集成 status 预算：1.5s；五个集成并行，总代价不随数量线性增长。 */
export const ADDON_INTEGRATION_TIMEOUT_MS = 1500

const TIMEOUT_RE = /timeout|timed out|aborted|abort/i

/** 是否"等不到 addon 回包"类错误（超时/被 abort）：把失败归因到 extension/bpy 而不是后端进程。 */
export function isAddonTimeout(e: unknown): boolean {
  return TIMEOUT_RE.test(errorMessage(e))
}

function readinessView(
  instances: AddonInstanceView[] | null,
  addonError: string | null,
  handshakeError: AddonHandshakeError | null,
): Record<string, unknown> {
  const readyInstances = instances ? instances.filter((x) => x.ready) : []
  const instanceId = readyInstances.length === 1 ? readyInstances[0].instanceId : null
  const connected = instances === null ? !addonError : readyInstances.length > 0
  const bpyOk = !addonError && (instances === null || readyInstances.length === 1)
  return {
    gateway: { live: true },
    addon: {
      connected,
      ready_instances: instances === null ? null : readyInstances.length,
      instanceId,
      handshake_error: handshakeError,
      kind: handshakeError ? 'addon-handshake-rejected' : null,
    },
    bpy: { ok: !addonError, error: addonError },
    instanceId,
    ready: bpyOk && connected,
  }
}

export async function statusView(
  E: EngineApiDeps,
  opts: { addonTimeoutMs?: number } = {},
): Promise<Record<string, unknown>> {
  const {
    LEDGER,
    RESULTS_DIR,
    SESSION_NAME,
    TRAJ_DIR,
    TRAJ_FULL,
    TRAJ_OFF,
    addon,
    jobSnapshot,
    jobs,
    ledgerList,
    ledgerState,
    runSnapshot,
    runs,
  } = E
  const addonTimeoutMs = opts.addonTimeoutMs || ADDON_STATUS_TIMEOUT_MS
  let region = null,
    addonError = null
  try {
    const out = await addon.send('execute_code', { code: REGION_CODE }, addonTimeoutMs)
    region = jsonFromStdout(out)
  } catch (e) {
    addonError = errorMessage(e).slice(0, 300)
  }
  return {
    region: region,
    addonError: addonError,
    virtualGui: virtualGuiView(),
    readiness: readinessView(gatewayInstances(addon), addonError, gatewayHandshakeError(addon)),
    plugin: {
      version: PLUGIN_VERSION,
      runtimeDir: wslToWin(RUNTIME_DIR),
      runtime: runtimeFingerprint(),
      provenance: engineProvenance({}),
    },
    session: SESSION_NAME,
    resultsDir: wslToWin(RESULTS_DIR),
    trajectory: {
      dir: wslToWin(TRAJ_DIR),
      off: TRAJ_OFF,
      full: TRAJ_FULL,
      note: 'v0.9.0（#15）：每次顶层调用一行 JSONL（route/op/ms/ok/参数摘要）；DSH_TRAJ=0 关，DSH_TRAJ_FULL=1 记更多参数',
    },
    runs: Array.from(runs.values())
      .slice(-5)
      .map((r) => runSnapshot(r as Record<string, unknown>)),
    jobs: Array.from(jobs.values())
      .slice(-5)
      .map((j) => jobSnapshot(j)),
    ledger: Object.assign(
      {
        path: wslToWin(LEDGER),
        pathWsl: LEDGER,
        recent: ledgerList(5).map((x: Record<string, unknown>) => ({
          id: x.id,
          kind: x.kind || 'job',
          status: x.status,
          startedAt: x.startedAt,
          ms: x.ms,
        })),
      },
      ledgerState(),
    ),
  }
}

/**
 * （PR review）：addon 明确回了错误（非超时）时的归因。
 * 这类失败说明通道在（TCP 通、addon 有响应），不要归成 main-thread-busy / blender-unreachable。
 */
export function addonErrorDiagnosis(error: string): Record<string, unknown> {
  // Blender 正在渲染时主线程命令会先吃 BUSY_RENDER：这是「等渲染」，不是 addon 坏 / 主线程忙。
  const busy = /BUSY_RENDER/.test(error)
  return {
    kind: 'addon-error',
    tcp: true,
    ping: null,
    error: error,
    stage: 'addon',
    summary: busy
      ? 'Gateway 在（/health 通）、addon 连接也通，Blender 正在渲染（BUSY_RENDER）：' +
        error +
        ' —— 主线程被渲染占用，不是 addon 进程坏、也不是通道未起。'
      : 'Gateway 在、addon 控制连接也通，addon 回了错误（不是超时）：' +
        error +
        ' —— 通道本身仍在，按 addonError 处理（如操作已暂停 / 参数或脚本报错），不是 main-thread-busy。',
    fix: busy
      ? '等渲染结束，或用 blender_op(op="render_wait") 等它；长渲染一开始就走 blender_run。不要按「主线程忙」反复重试，也不用 op=start（那只修 Gateway 进程）。'
      : '按 addonError 的具体报错修（例如在 DSH 里恢复被暂停的「AI 操作」；脚本/参数错误按报错改参数），不要按「主线程忙」重试。',
    hint: busy
      ? 'BUSY_RENDER：主线程命令会排队到超时，等渲染结束再发。'
      : 'addon 控制连接通，但 addon 明确回了错误 → 归因 addonError，不是通道未起、也不是主线程忙。',
  }
}

function handshakeDiagnosis(rejection: AddonHandshakeError): Record<string, unknown> {
  return {
    kind: 'addon-handshake-rejected',
    stage: 'handshake',
    connected: false,
    tcp: true,
    ready_instances: 0,
    code: rejection.code,
    upgrade_required: rejection.upgrade_required === true,
    summary:
      'Gateway 在，但 addon 握手被拒绝：' +
      rejection.message +
      '这不是 addon 未启动，而是 addon 版本/协议不匹配。',
    fix: rejection.fix,
    hint: '旧 addon 重连会持续被拒绝；升级到 2.0.0（protocol 12）并重启 Blender。',
    error: rejection.message,
  }
}

function noAddonDiagnosis(instances: AddonInstanceView[]): Record<string, unknown> {
  return {
    kind: 'addon-unreachable',
    stage: 'transport',
    connected: false,
    tcp: false,
    ready_instances: 0,
    instances: instances.map((x) => x.instanceId),
    summary:
      'Gateway 在，但没有 Blender addon 连接到 Gateway —— Blender 未启动、DSH Blender Bridge 扩展未启用/未连接，或仍在握手。',
    fix: '启动 Blender 4.2.10 LTS，在 3D 视图 N 面板启用「DSH Blender Bridge」2.0.0（protocol 12），确认扩展显示已连接 Gateway；纯渲染/批处理改用 blender_run。',
    hint: 'Gateway /health 只证明 Gateway 进程存活，不代表 extension/bpy 可用。',
    error: 'no addon connected to Gateway',
  }
}

function ambiguousDiagnosis(ready: AddonInstanceView[]): Record<string, unknown> {
  return {
    kind: 'ambiguous-instance',
    stage: 'transport',
    connected: true,
    tcp: true,
    ready_instances: ready.length,
    instances: ready.map((x) => x.instanceId),
    summary:
      'Gateway 有多个 Blender 实例连接，doctor 不能替你选一个：' +
      ready.map((x) => x.instanceId).join(' / '),
    fix: '在调用里显式传 instanceId（或先停掉多余实例），再重试 doctor。',
    hint: '多实例时任何省略 instanceId 的路由都必须报 ambiguity，不能静默选第一个。',
    error: 'ambiguous addon instance',
  }
}

function gatewayFailure(
  E: EngineApiDeps,
  e: unknown,
  elapsedMs: number,
  instance: AddonInstanceView,
): Record<string, unknown> {
  const err = errorMessage(e)
  if (!isAddonTimeout(e))
    return {
      ok: false,
      addon: { ...addonErrorDiagnosis(err), connected: true, instanceId: instance.instanceId },
      metrics: metricsView(E),
      error: err,
    }
  return {
    ok: false,
    addon: {
      kind: 'addon-unresponsive',
      tcp: true,
      connected: true,
      ping: null,
      instanceId: instance.instanceId,
      error: err,
      stage: 'addon',
      timeout_ms: ADDON_DOCTOR_STATUS_TIMEOUT_MS,
      elapsed_ms: elapsedMs,
      summary:
        'Gateway 在、addon 控制连接也在，但 addon 在 ' +
        String(ADDON_DOCTOR_STATUS_TIMEOUT_MS) +
        'ms 内没回 bpy 往返（execute_code 超时）—— addon 线程卡住 / Blender 主线程被占。',
      fix: '先确认 Blender 在运行且「DSH Blender Bridge」已 Connect；再把 Blender 从渲染/模态操作里放出来，必要时重载 addon（N 面板 Disconnect → Connect）。op=start 只管 Gateway 进程，不修这条通道。',
      hint: 'addon 控制连接通 + Gateway 在，但 bpy 往返超时 → 问题在 extension/bpy，不在 Gateway 进程。',
    },
    metrics: metricsView(E),
    error: err,
  }
}

async function gatewayDoctor(
  E: EngineApiDeps,
  instances: AddonInstanceView[],
): Promise<Record<string, unknown>> {
  const t0 = Date.now()
  const ready = instances.filter((x) => x.ready)
  if (ready.length === 0) {
    const rejected = gatewayHandshakeError(E.addon)
    const addon = rejected ? handshakeDiagnosis(rejected) : noAddonDiagnosis(instances)
    return {
      ok: false,
      addon,
      readiness: readinessView(instances, String(addon.error || 'no addon'), rejected),
      metrics: metricsView(E),
      error: addon.error,
    }
  }
  if (ready.length > 1) {
    const addon = ambiguousDiagnosis(ready)
    return {
      ok: false,
      addon,
      readiness: readinessView(instances, String(addon.error || 'ambiguous instance'), null),
      metrics: metricsView(E),
      error: addon.error,
    }
  }
  const instance = ready[0]
  try {
    const s = await statusView(E, { addonTimeoutMs: ADDON_DOCTOR_STATUS_TIMEOUT_MS })
    if (s.addonError) throw new Error(String(s.addonError))
    const pingStart = Date.now()
    let pingMs: number | null = null
    try {
      await E.addon.send('ping', {}, 2000)
      pingMs = Date.now() - pingStart
    } catch (e) {
      pingMs = null
    }
    return {
      ok: true,
      addon: {
        kind: 'ok',
        tcp: true,
        connected: true,
        instanceId: instance.instanceId,
        ready_instances: 1,
        ping: pingMs !== null,
        ping_ms: pingMs,
        roundTripMs: Date.now() - t0,
        summary: '通道健康：Gateway WebSocket + bpy 往返都通',
        protocol: ADDON_PROTOCOL_VERSION,
        protocol_label: 'Gateway WebSocket (protocol 12)',
        protocol_from: 'gateway',
        fix: '无需处理',
        region: s.region,
      },
      readiness: s.readiness,
      metrics: metricsView(E),
    }
  } catch (e) {
    return gatewayFailure(E, e, Date.now() - t0, instance)
  }
}

/** 五个集成 status 并行探测：一个卡住只付一份预算，而不是串行叠加。 */
async function integrationStatus(E: EngineApiDeps): Promise<Record<string, unknown>> {
  // 集成"开关已开但仍不可用"的实情（缺 key 等）由各自 status 命令回答
  const integrations: Record<string, unknown> = {}
  // Five independent status checks run in parallel: a stuck integration costs one
  // 1.5s budget, not five serial budgets (single-layer bounded probe).
  const probes = [
    'get_polyhaven_status',
    'get_hyper3d_status',
    'get_sketchfab_status',
    'get_polypizza_status',
    'get_hunyuan3d_status',
  ]
  const results = await Promise.all(
    probes.map(async (name) => {
      const key = name.replace('get_', '').replace('_status', '')
      try {
        const r = await E.addon.send(name, {}, ADDON_INTEGRATION_TIMEOUT_MS)
        const rr = (r || null) as Record<string, unknown> | null
        return [
          key,
          {
            enabled: !!(rr && rr.enabled),
            message: String((rr && rr.message) || '')
              .split('\n')[0]
              .slice(0, 160),
          },
        ] as const
      } catch (e) {
        const timeout = isAddonTimeout(e)
        const msg = errorMessage(e).slice(0, 120)
        return [
          key,
          {
            enabled: false,
            timeout: timeout,
            message: timeout
              ? 'timeout（' + String(ADDON_INTEGRATION_TIMEOUT_MS) + 'ms）：' + msg
              : msg,
          },
        ] as const
      }
    }),
  )
  for (const [key, value] of results) integrations[key] = value
  return integrations
}

/** Capabilities plus the guided-install plans shared by doctor --fix and the add-on button. */
function virtualGuiView(): Record<string, unknown> {
  const capabilities = displayCapabilities()
  return { ...capabilities, installPlans: buildInstallPlans(capabilities) }
}

export function createDoctorApi(E: EngineApiDeps) {
  const { addon } = E
  return {
    async protocol() {
      return {
        proto: { id: 'ws', label: 'Gateway WebSocket (protocol 12)', panelHint: '' },
        from: 'gateway',
      }
    },
    /** 现场体检：真跑一次 Gateway → addon → bpy 往返（status），通了才算 ok。 */
    async doctor() {
      const gateway = gatewayInstances(E.addon)
      if (!gateway) {
        return {
          ok: false,
          addon: {
            kind: 'gateway-required',
            stage: 'config',
            summary: 'engine 需要 Gateway addon client；3.1 不再直连 addon TCP 端口。',
            fix: '用 lib/runtime/gateway.js 启动 Gateway，并让 host/tools 只连 Gateway。',
          },
          virtualGui: virtualGuiView(),
          metrics: metricsView(E),
          error: 'gateway addon client required',
        }
      }
      const result = await gatewayDoctor(E, gateway)
      return { ...result, virtualGui: displayCapabilities() }
    },
    /** 3D 视口区域 rect / 窗口尺寸 / 着色模式 */
    status() {
      return statusView(E)
    },
    async commands() {
      const out = await addon.send('execute_code', { code: FLAGS_CODE }, ADDON_FLAGS_TIMEOUT_MS)
      const meta = (jsonFromStdout(out) || { flags: {}, scene: null, file: null }) as Record<
        string,
        unknown
      >
      const flags = (meta.flags || {}) as Record<string, unknown>
      const available: string[] = []
      const disabled: string[] = []
      for (const name of Object.keys(COMMAND_CATALOG)) {
        const gate = COMMAND_CATALOG[name].gate
        if (!gate || flags[gate]) available.push(name)
        else disabled.push(name)
      }
      // Args / pause_safe / network come from the addon's single command
      // registry (get_addon_info), never from a second host-side table.
      const info = (await addon
        .send('get_addon_info', {}, ADDON_FLAGS_TIMEOUT_MS)
        .catch(() => null)) as Record<string, unknown> | null
      const rawSpecs = info && Array.isArray(info.command_specs) ? info.command_specs : []
      const specs = new Map<string, Record<string, unknown>>()
      for (const row of rawSpecs) {
        if (
          row &&
          typeof row === 'object' &&
          typeof (row as Record<string, unknown>).name === 'string'
        )
          specs.set(String((row as Record<string, unknown>).name), row as Record<string, unknown>)
      }
      const commands = Object.keys(COMMAND_CATALOG).map((name) => {
        const entry = COMMAND_CATALOG[name]
        const spec = specs.get(name) || {}
        return {
          name,
          description: entry.d,
          access: entry.access,
          gate: entry.gate,
          enabled: !entry.gate || !!flags[entry.gate],
          pause_safe: spec.pause_safe === true,
          network: spec.network === true,
          args: Array.isArray(spec.params) ? spec.params : [],
        }
      })
      const integrations = await integrationStatus(E)
      return {
        scene: meta.scene,
        file: meta.file,
        total: available.length,
        available: available,
        disabled: disabled,
        gates: flags,
        integrations: integrations,
        commands: commands,
      }
    },
    /** 内环：启动（spec 见 runner.py 顶部契约） */
  }
}
