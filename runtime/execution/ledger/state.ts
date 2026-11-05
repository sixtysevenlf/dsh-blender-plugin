// 运行台账（内存 runs + 磁盘 JSONL + 可写性自证）
import path from 'node:path'
import { winToWsl } from '../../config.js'
import { WIN_TMP, WSL_TMP } from '../../paths.js'
import fs from 'node:fs'
import { errorCode, errorMessage } from '../../core/json.js'
import { PLUGIN_VERSION } from '../../core/evidence.js'

export interface WorkdirProbe {
  wsl: string
  win: string
  writable: boolean
  error: string | null
  [k: string]: unknown
}
export interface LedgerWarn {
  path: string
  error: string
  hint: string
}
export interface LedgerApi {
  runs: Map<string, unknown>
  LEDGER: string
  ledgerAppend: (rec: Record<string, unknown>) => void
  ledgerState: () => Record<string, unknown>
  ledgerList: (limit?: number) => Array<Record<string, unknown>>
  runSnapshot: (r: Record<string, unknown>) => Record<string, unknown>
  reconcileLedger: (rec: Record<string, unknown> | null) => Record<string, unknown> | null
}

export function createLedgerStore() {
  const runs = new Map<string, unknown>()
  const LEDGER = path.join(winToWsl(WIN_TMP), 'jobs', 'index.jsonl')

  // ---- v0.9.3（同源）：workDir 可写性 —— 旧版台账/日志写失败是**静默**的
  // （实测：workDir 落在只读挂载上时，磁盘台账一条都写不进去，而 op=list/status 只会说"没有记录"）。
  let ledgerWarn: LedgerWarn | null = null
  let workdirProbeCache: WorkdirProbe | null = null
  function workdirProbe(): WorkdirProbe {
    if (workdirProbeCache) return workdirProbeCache
    const out: WorkdirProbe = { wsl: WSL_TMP, win: WIN_TMP, writable: false, error: null }
    try {
      fs.mkdirSync(WSL_TMP, { recursive: true })
      const probe = path.join(WSL_TMP, '.dsh_write_probe_' + process.pid)
      fs.writeFileSync(probe, 'ok', 'utf8')
      fs.unlinkSync(probe)
      out.writable = true
    } catch (e) {
      out.error = errorCode(e) || errorMessage(e).slice(0, 200)
    }
    workdirProbeCache = out
    return out
  }

  function ledgerAppend(rec: Record<string, unknown>): void {
    try {
      fs.mkdirSync(path.dirname(LEDGER), { recursive: true })
      fs.appendFileSync(
        LEDGER,
        JSON.stringify(Object.assign({ pluginVersion: PLUGIN_VERSION, at: Date.now() }, rec)) +
          String.fromCharCode(10),
        'utf8',
      )
      ledgerWarn = null
    } catch (e) {
      // 静默失败是这一版要消灭的东西：记下来，让 /status 与 op=list 能说出来
      ledgerWarn = {
        path: LEDGER,
        error: errorCode(e) || errorMessage(e).slice(0, 200),
        hint: 'workDir 不可写 → 磁盘台账/日志落不下去（内存台账与产物不受影响）。改 dsh-blender.config.json 的 workDir 或 DSH_BLENDER_WORKDIR 到一个两端都可写的目录（WSL 路径也可，如 /home/<user>/dsh-blender-work）。',
      }
    }
  }
  function ledgerState(): Record<string, unknown> {
    const p = workdirProbe()
    return {
      path: LEDGER,
      writable: !!p.writable && !ledgerWarn,
      workDir: p,
      lastError: ledgerWarn ? ledgerWarn.error : p.writable ? null : p.error,
      hint: ledgerWarn
        ? ledgerWarn.hint
        : p.writable
          ? null
          : 'workDir 不可写（' +
            String(p.error) +
            '）：台账/日志会静默失败 —— 换一个可写 workDir（WSL 路径也行）。',
    }
  }
  function ledgerList(limit = 40): Array<Record<string, unknown>> {
    try {
      const lines = fs
        .readFileSync(LEDGER, 'utf8')
        .trim()
        .split(String.fromCharCode(10))
        .filter(Boolean)
        .slice(-limit * 4)
      const by = new Map<string, Record<string, unknown>>()
      for (const l of lines) {
        try {
          const o = JSON.parse(l) as Record<string, unknown>
          const id = String(o.id)
          by.set(id, Object.assign(by.get(id) || {}, o))
        } catch (e) {
          /* skip */
        }
      }
      return Array.from(by.values())
        .sort((a, b) => (Number(b.startedAt) || 0) - (Number(a.startedAt) || 0))
        .slice(0, limit)
    } catch (e) {
      return []
    }
  }

  return { runs, LEDGER, ledgerAppend, ledgerState, ledgerList }
}
