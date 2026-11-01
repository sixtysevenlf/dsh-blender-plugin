// engine API：通道诊断（F1.1 从 server-compat.ts 拆出）
import { diagnose, freshPing, protocolInfo, tcpProbe } from '../../transport/probes.js'
import { FLAGS_CODE, REGION_CODE } from '../flags.js'
import { jsonFromStdout } from '../output/index.js'
import { PLUGIN_VERSION, RUNTIME_DIR, engineProvenance, runtimeFingerprint } from '../evidence.js'
import { wslToWin } from '../../config.js'
import { COMMAND_CATALOG } from '../../catalog/index.js'
import { errorMessage } from '../json.js'
import type { EngineApiDeps } from './index.js'
import { metricsView } from './metrics.js'

export async function statusView(E: EngineApiDeps): Promise<Record<string, unknown>> {
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
  let region = null,
    addonError = null
  try {
    const out = await addon.send('execute_code', { code: REGION_CODE })
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
        const s = await statusView(E)
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
        const d = await diagnose()
        metrics.lastDiagnosis = d
        return { ok: false, addon: d, metrics: metricsView(E), error: errorMessage(e) }
      }
    },
    /** 3D 视口区域 rect / 窗口尺寸 / 着色模式 */
    status() {
      return statusView(E)
    },
    async commands() {
      const out = await addon.send('execute_code', { code: FLAGS_CODE })
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
          const r = await addon.send(t, {}, 15000)
          const rr = (r || null) as Record<string, unknown> | null
          integrations[t.replace('get_', '').replace('_status', '')] = {
            enabled: !!(rr && rr.enabled),
            message: String((rr && rr.message) || '')
              .split('\n')[0]
              .slice(0, 160),
          }
        } catch (e) {
          integrations[t] = { enabled: false, message: errorMessage(e).slice(0, 120) }
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
