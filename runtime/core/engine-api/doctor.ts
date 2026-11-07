// engine API：通道诊断
import { diagnose, freshPing, protocolInfo, tcpProbe } from '../../transport/probes.js'
import {
  ADDON_PROTOCOL_VERSION,
  gatewayHandshakeError,
  gatewayInstances,
} from '../../transport/addon-ws.js'
import type { AddonHandshakeError, AddonInstanceView } from '../../transport/addon-ws.js'
import type { Diagnosis } from '../../transport/probes.js'
import { FLAGS_CODE, REGION_CODE } from '../flags.js'
import { jsonFromStdout } from '../output/index.js'
import { PLUGIN_VERSION, RUNTIME_DIR, engineProvenance, runtimeFingerprint } from '../evidence.js'
import { wslToWin } from '../../config.js'
import { COMMAND_CATALOG } from '../../catalog/index.js'
import { errorMessage } from '../json.js'
import type { EngineApiDeps } from './index.js'
import { metricsView } from './metrics.js'

/**
 * addon 侧阶段预算。宿主 backendJson 默认 20s、T.commands 静态截止 60s；
 * 这里的预算必须落在宿主之前，否则模型只拿到 aborted，看不到任何结构化诊断。
 */
/**
 * /status 的 bpy 往返（REGION_CODE）预算：5s —— 修复后的诚实值（启动探测不再占串行队列头，
 * 修复前只能收到 3.5s）。注意 installAddonSend 在 transport 错误后还会串一次 diagnose()
 * （半开 addon 约 2.5s），所以这里不能贴着 host 的 20s，留足余量（半开稳态 ≈5s + 2.5s）。
 */
export const ADDON_STATUS_TIMEOUT_MS = 5000
/** /doctor 的 status 阶段预算：4s（修复后回调；半开场景 ≈4s + 复用诊断 ≈2.5s，仍 <10s 目标）。 */
export const ADDON_DOCTOR_STATUS_TIMEOUT_MS = 4000
/** /commands 的 FLAGS + 5 个集成必须整段落在客户端预算内：15s + 5×(3s+~2.5s 诊断)≈42.5s < 45s 客户端 < 60s 宿主。 */
export const ADDON_FLAGS_TIMEOUT_MS = 15000
/** /commands 单个集成 status 预算（5 个串行；失败还会各串一次 ~2.5s 诊断，故取 3s）。 */
export const ADDON_INTEGRATION_TIMEOUT_MS = 3000

const TIMEOUT_RE = /timeout|timed out|aborted|abort/i

/** 是否"等不到 addon 回包"类错误（超时/被 abort）：把失败归因到 addon/bpy 而不是后端进程。 */
export function isAddonTimeout(e: unknown): boolean {
  return TIMEOUT_RE.test(errorMessage(e))
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
      ? '等渲染结束，或用 blender_rt_plan(op="render_wait") 等它；长渲染一开始就走 blender_rt_job。不要按「主线程忙」反复重试，也不用 op=start（那只修 9877 后端进程）。'
      : '按 addonError 的具体报错修（例如在 DSH 里恢复被暂停的「AI 操作」；脚本/参数错误按报错改参数），不要按「主线程忙」重试。',
    hint: busy
      ? 'BUSY_RENDER：主线程命令会排队到超时，等渲染结束再发。'
      : 'addon 控制连接通，但 addon 明确回了错误 → 归因 addonError，不是通道未起、也不是主线程忙。',
  }
}

/**
 * doctor 失败分支。TCP 已通时把「addon 不回包」与「后端没起」分开 ——
 * 超时不再落成 blender-unreachable（现场 seq 52-56 的 start/doctor 自相矛盾即源于此）。
 */
async function failedDoctor(
  E: EngineApiDeps,
  e: unknown,
  elapsedMs: number,
): Promise<Record<string, unknown>> {
  const { metrics } = E
  // addon.send 的包装层（engine/lifecycle.ts）在 transport 错误后已经跑过一次 diagnose()
  // 并挂在 err.diagnosis 上 —— 复用它，避免 doctor 失败路径再串一个 ~2.5s 的探针。
  const timeout = isAddonTimeout(e)
  const err = errorMessage(e)
  // （PR review）：非超时错误（如「AI 操作已暂停」/脚本报错）不是 main-thread-busy。
  // diagnose() 在 ping 通时会固定归成 main-thread-busy，这里直接按 addonError 归因，不再串诊探针。
  if (!timeout)
    return { ok: false, addon: addonErrorDiagnosis(err), metrics: metricsView(E), error: err }
  const attached =
    (e as { diagnosis?: Diagnosis }).diagnosis ||
    (timeout ? (metrics.lastDiagnosis as Diagnosis | null) : null)
  const d: Diagnosis | null = attached || (await diagnose().catch(() => null))
  if (d) metrics.lastDiagnosis = d
  // 走到这里说明 doctor 入口的 tcpProbe() 已经通了：9876 有监听，只是 addon 不回 bpy 往返。
  const addon = {
    ...(d || {}),
    kind: 'addon-unresponsive',
    tcp: true,
    ping: d ? d.ping : null,
    error: err,
    stage: 'execute_code',
    timeout_ms: ADDON_DOCTOR_STATUS_TIMEOUT_MS,
    elapsed_ms: elapsedMs,
    summary:
      'Gateway 在（/health 通），addon 连接也通，但 addon 在 ' +
      String(ADDON_DOCTOR_STATUS_TIMEOUT_MS) +
      'ms 内没回 bpy 往返（execute_code 超时）—— addon 线程卡住 / Blender 主线程被占 / 对面不是本仓 addon',
    fix: '先确认 Blender 在运行且「DSH Blender Bridge」已 Connect；再把 Blender 从渲染/模态操作里放出来，必要时重载 addon（N 面板 Disconnect → Connect）。op=start 只管 Gateway 进程，不修这条通道。',
    hint: 'addon 连接通 + Gateway 在，但 bpy 往返超时 → 问题在 addon/bpy，不在 Gateway 进程。',
  }
  return { ok: false, addon, metrics: metricsView(E), error: err }
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
    hint: 'Gateway /health 只证明 Gateway 进程存活，不代表 addon/bpy 可用。',
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
      hint: 'addon 控制连接通 + Gateway 在，但 bpy 往返超时 → 问题在 addon/bpy，不在 Gateway 进程。',
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
    return { ok: false, addon, metrics: metricsView(E), error: addon.error }
  }
  if (ready.length > 1) {
    const addon = ambiguousDiagnosis(ready)
    return { ok: false, addon, metrics: metricsView(E), error: addon.error }
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
      metrics: metricsView(E),
    }
  } catch (e) {
    return gatewayFailure(E, e, Date.now() - t0, instance)
  }
}

export function createDoctorApi(E: EngineApiDeps) {
  const { addon, metrics } = E
  return {
    async protocol() {
      return await protocolInfo()
    },
    /** 现场体检：真跑一次 bpy 往返（status），通了才算 ok；不通才去三级判定 */
    async doctor() {
      const gateway = gatewayInstances(E.addon)
      if (gateway) return await gatewayDoctor(E, gateway)
      const t0 = Date.now()
      if (!(await tcpProbe())) {
        const d = await diagnose()
        metrics.lastDiagnosis = d
        return { ok: false, addon: d, metrics: metricsView(E) }
      }
      try {
        const s = await statusView(E, { addonTimeoutMs: ADDON_DOCTOR_STATUS_TIMEOUT_MS })
        // statusView 自己吞 addon 错误 → 这里必须把 addonError 当失败，否则 doctor 会假报 ok:true。
        // 把 send 包装层刚算出的 diagnosis 带过去，失败路径就不用再串一次 ~2.5s 探针。
        if (s.addonError) {
          const err = new Error(String(s.addonError)) as Error & { diagnosis?: Diagnosis }
          if (metrics.lastDiagnosis) err.diagnosis = metrics.lastDiagnosis as Diagnosis
          throw err
        }
        const pingMs = await freshPing().catch(() => null)
        const pinfo = await protocolInfo()
        return {
          ok: true,
          addon: {
            kind: 'ok',
            tcp: true,
            ping: true,
            ping_ms: pingMs,
            roundTripMs: Date.now() - t0,
            summary: '通道健康：TCP + ping + bpy 往返都通',
            protocol: pinfo.id,
            protocol_label: pinfo.label,
            protocol_from: pinfo.from,
            fix: '无需处理',
            region: s.region,
          },
          metrics: metricsView(E),
        }
      } catch (e) {
        return await failedDoctor(E, e, Date.now() - t0)
      }
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
      // 集成"开关已开但仍不可用"的实情（缺 key 等）由各自 status 命令回答
      const integrations: Record<string, unknown> = {}
      for (const t of [
        'get_polyhaven_status',
        'get_hyper3d_status',
        'get_sketchfab_status',
        'get_polypizza_status',
        'get_hunyuan3d_status',
      ]) {
        try {
          const r = await addon.send(t, {}, ADDON_INTEGRATION_TIMEOUT_MS)
          const rr = (r || null) as Record<string, unknown> | null
          integrations[t.replace('get_', '').replace('_status', '')] = {
            enabled: !!(rr && rr.enabled),
            message: String((rr && rr.message) || '')
              .split('\n')[0]
              .slice(0, 160),
          }
        } catch (e) {
          const timeout = isAddonTimeout(e)
          const msg = errorMessage(e).slice(0, 120)
          integrations[t.replace('get_', '').replace('_status', '')] = {
            enabled: false,
            timeout: timeout,
            message: timeout
              ? 'timeout（' + String(ADDON_INTEGRATION_TIMEOUT_MS) + 'ms）：' + msg
              : msg,
          }
        }
      }
      return {
        scene: meta.scene,
        file: meta.file,
        total: available.length,
        available: available,
        disabled: disabled,
        gates: flags,
        integrations: integrations,
      }
    },
    /** 内环：启动（spec 见 runner.py 顶部契约） */
  }
}
