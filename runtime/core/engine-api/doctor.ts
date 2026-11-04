// engine API：通道诊断（F1.1 从 server-compat.ts 拆出）
import { diagnose, freshPing, protocolInfo, tcpProbe } from '../../transport/probes.js'
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
 * P0-3：addon 侧阶段预算。宿主 backendJson 默认 20s、T.commands 静态截止 60s；
 * 这里的预算必须落在宿主之前，否则模型只拿到 aborted，看不到任何结构化诊断。
 */
/**
 * /status 的 bpy 往返（REGION_CODE）预算。注意 installAddonSend 在 transport 错误后还会
 * 串一次 diagnose()（半开 addon 约 2.5s），所以这里不能贴着 host 的 20s，留足余量。
 */
export const ADDON_STATUS_TIMEOUT_MS = 3500
/** /doctor 的 status 阶段预算：更短，给 diagnose() 的 ping 探针留时间（A3 半开场景总目标 <10s）。 */
export const ADDON_DOCTOR_STATUS_TIMEOUT_MS = 3000
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
 * P2（PR review）：addon 明确回了错误（非超时）时的归因。
 * 这类失败说明通道在（TCP 通、addon 有响应），不要归成 main-thread-busy / blender-unreachable。
 */
export function addonErrorDiagnosis(error: string): Record<string, unknown> {
  return {
    kind: 'addon-error',
    tcp: true,
    ping: null,
    error: error,
    stage: 'execute_code',
    summary:
      '后端（9877）在、9876 端口也通，addon 回了错误（不是超时）：' +
      error +
      ' —— 通道本身仍在，按 addonError 处理（如操作已暂停 / 参数或脚本报错），不是 main-thread-busy。',
    fix: '按 addonError 的具体报错修（例如在 DSH 里恢复被暂停的「AI 操作」；脚本/参数错误按报错改参数），不要按「主线程忙」重试。',
    hint: 'TCP 127.0.0.1:9876 通，但 addon 明确回了错误 → 归因 addonError，不是通道未起、也不是主线程忙。',
  }
}

/**
 * P0-3：doctor 失败分支。TCP 已通时把「addon 不回包」与「后端没起」分开 ——
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
  // P2（PR review）：非超时错误（如「AI 操作已暂停」/脚本报错）不是 main-thread-busy。
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
      '后端（9877）在，9876 端口也通，但 addon 在 ' +
      String(ADDON_DOCTOR_STATUS_TIMEOUT_MS) +
      'ms 内没回 bpy 往返（execute_code 超时）—— addon 线程卡住 / Blender 主线程被占 / 对面不是本仓 addon',
    fix: '先确认 Blender 在运行且「DSH Blender Bridge」已 Connect；再把 Blender 从渲染/模态操作里放出来，必要时重载 addon（N 面板 Disconnect → Connect）。op=start 只管 9877 后端进程，不修这条通道。',
    hint: 'TCP 127.0.0.1:9876 通 + 后端 9877 在，但 bpy 往返超时 → 问题在 addon/bpy，不在后端进程。',
  }
  return { ok: false, addon, metrics: metricsView(E), error: err }
}

export function createDoctorApi(E: EngineApiDeps) {
  const { addon, metrics } = E
  return {
    async protocol() {
      return await protocolInfo()
    },
    /** 现场体检：真跑一次 bpy 往返（status），通了才算 ok；不通才去三级判定 */
    async doctor() {
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
