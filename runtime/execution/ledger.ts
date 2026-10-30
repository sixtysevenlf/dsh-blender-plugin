// 运行台账：内存 runs + 磁盘 JSONL + 进程对账（T6）
// @imports
import path from "node:path";
import { winToWsl, wslToWin } from "../config.js";
import { WIN_TMP, WSL_TMP } from "../paths.js";
import fs from "node:fs";
import { PLUGIN_VERSION, pidAlive } from "../core/evidence.js";
import { reconcileLedger as reconcileLedgerFromDisk } from "../recover.js";
import { errorCode, errorMessage } from "../core/json.js";


export interface WorkdirProbe { wsl: string; win: string; writable: boolean; error: string | null; [k: string]: unknown }
export interface LedgerWarn { path: string; error: string; hint: string }
export interface LedgerApi {
  runs: Map<string, unknown>;
  LEDGER: string;
  ledgerAppend: (rec: Record<string, unknown>) => void;
  ledgerState: () => Record<string, unknown>;
  ledgerList: (limit?: number) => Array<Record<string, unknown>>;
  runSnapshot: (r: Record<string, unknown>) => Record<string, unknown>;
  reconcileLedger: (rec: Record<string, unknown> | null) => Record<string, unknown> | null;
}

export function createLedger(_E?: unknown) {
  // deps: none
  const runs = new Map<string, unknown>();
  const LEDGER = path.join(winToWsl(WIN_TMP), 'jobs', 'index.jsonl');
  // ---- v0.9.3（D4/D8 同源）：workDir 可写性 —— 旧版台账/日志写失败是**静默**的
  // （实测：workDir 落在只读挂载上时，磁盘台账一条都写不进去，而 op=list/status 只会说"没有记录"）。
  let ledgerWarn: LedgerWarn | null = null;
  let workdirProbeCache: WorkdirProbe | null = null;
  function workdirProbe(): WorkdirProbe {
    if (workdirProbeCache) return workdirProbeCache;
    const out: WorkdirProbe = { wsl: WSL_TMP, win: WIN_TMP, writable: false, error: null };
    try {
      fs.mkdirSync(WSL_TMP, { recursive: true });
      const probe = path.join(WSL_TMP, '.dsh_write_probe_' + process.pid);
      fs.writeFileSync(probe, 'ok', 'utf8');
      fs.unlinkSync(probe);
      out.writable = true;
    } catch (e) { out.error = errorCode(e) || errorMessage(e).slice(0, 200); }
    workdirProbeCache = out;
    return out;
  }
  function ledgerAppend(rec: Record<string, unknown>): void {
    try {
      fs.mkdirSync(path.dirname(LEDGER), { recursive: true });
      fs.appendFileSync(LEDGER, JSON.stringify(Object.assign({ pluginVersion: PLUGIN_VERSION, at: Date.now() }, rec)) + String.fromCharCode(10), 'utf8');
      ledgerWarn = null;
    } catch (e) {
      // 静默失败是这一版要消灭的东西：记下来，让 /status 与 op=list 能说出来
      ledgerWarn = { path: LEDGER, error: errorCode(e) || errorMessage(e).slice(0, 200),
        hint: 'workDir 不可写 → 磁盘台账/日志落不下去（内存台账与产物不受影响）。改 dsh-blender.config.json 的 workDir 或 DSH_BLENDER_WORKDIR 到一个两端都可写的目录（WSL 路径也可，如 /home/<user>/dsh-blender-work）。' };
    }
  }
  function ledgerState(): Record<string, unknown> {
    const p = workdirProbe();
    return { path: LEDGER, writable: !!p.writable && !ledgerWarn, workDir: p,
             lastError: ledgerWarn ? ledgerWarn.error : (p.writable ? null : p.error),
             hint: ledgerWarn ? ledgerWarn.hint : (p.writable ? null : ('workDir 不可写（' + String(p.error) + '）：台账/日志会静默失败 —— 换一个可写 workDir（WSL 路径也行）。')) };
  }
  function ledgerList(limit = 40): Array<Record<string, unknown>> {
    try {
      const lines = fs.readFileSync(LEDGER, 'utf8').trim().split(String.fromCharCode(10)).filter(Boolean).slice(-limit * 4);
      const by = new Map<string, Record<string, unknown>>();
      for (const l of lines) {
        try { const o = JSON.parse(l) as Record<string, unknown>; const id = String(o.id); by.set(id, Object.assign(by.get(id) || {}, o)); } catch (e) { /* skip */ }
      }
      return Array.from(by.values()).sort((a, b) => (Number(b.startedAt) || 0) - (Number(a.startedAt) || 0)).slice(0, limit);
    } catch (e) { return []; }
  }
  /**
   * v0.9.3（D2）：run（blender_rt_headless 的同步执行）与 job 同一份结构化回执形状 ——
   * collect/status 收 run-… 与 job-… 都是同一套字段，调用方不必分辨两者。
   */
  function runSnapshot(r: Record<string, unknown>): Record<string, unknown> {
    const now = Date.now();
    const running = r.status === 'running';
    const rLogs = r.logs as { stdoutWsl?: string } | null;
    const rArtifacts: unknown[] = Array.isArray(r.artifacts) ? r.artifacts : [];
    return { kind: 'headless', id: r.id, status: r.status, pid: r.pid,
             pidAlive: running ? pidAlive(r.pid) : false,
             ms: (Number(r.finishedAt) || now) - Number(r.startedAt), timeoutMs: r.timeoutMs || null,
             outdir: r.outdir || WIN_TMP, outdirWsl: r.outdirWsl || null,
             logs: r.logs || null, script: r.script || null, exitCode: r.exitCode, signal: r.signal || null,
             timedOut: r.timedOut || false, failureClass: r.failureClass || null,
             stage: r.stage || null, stageName: r.stageName || null, stageAt: r.stageAt || null,
             stageAgoMs: r.stageAt ? (now - Number(r.stageAt)) : null,
             lastOutputAt: r.lastOutputAt || null,
             idleMs: r.lastOutputAt ? (now - Number(r.lastOutputAt)) : (now - Number(r.startedAt)),
             lines: r.lines || 0,
             logBytes: (() => { try { const p = rLogs && rLogs.stdoutWsl; return p ? fs.statSync(p).size : 0 } catch (e) { return 0 } })(),
             resultJson: r.result || null, result: r.result || null, resultParseError: r.parseError || null,
             resultPath: r.resultPath || null, resultPathWsl: r.resultPathWsl || null,
             resultBytes: r.resultBytes || 0, resultTruncated: !!r.resultTruncated,
             stdoutTail: r.stdoutTail || null, stderrTail: r.stderrTail || null,
             pathWarnings: r.pathWarnings || [], warnings: r.warnings || r.pathWarnings || [], shots: r.shots || null,
             progress: r.progress || null, progressAt: r.progressAt || null,
             progressAgeMs: r.progressAt ? (now - Number(r.progressAt)) : null,
             heartbeat: r.heartbeat || null, progressHistory: r.progressHistory || [],
             inputFile: r.inputFile || null,
             artifacts: rArtifacts.map((n: unknown) => (typeof n === 'string' ? { name: n } : n)),
             artifactCount: rArtifacts.length,
             expect: r.expect || null, pluginVersion: PLUGIN_VERSION,
             hint: '这是 blender_rt_headless 的运行台账（run 与 job 同一 id 空间）：产物在 outdir，日志见 logs；'
                 + '结果在 resultJson / resultPath（运行中则用 stage / idleMs 看进展）' };
  }
  /**
   * 磁盘台账记录（后端重启后的历史）：running 但 pid 已不存在时的收敛。
   *
   * v2.6（本轮反馈 #1）：旧版一律判 stale —— 但实测两次「子进程其实已经跑完、产物/日志都在」，
   * 调用方要自己 Get-Process + ls 破案，每次至少赔一轮往返。现在先做磁盘对账：
   *   summary.json / resultPath / stdout 终态（DSH_STAGE done、HEADLESS、Traceback）
   * 对得上 ⇒ status=done|failed + recovered=true（证据在 recoveredFrom/recoveredEvidence）；
   * 对不上才判 stale，并把 evidence（日志大小/lastStage）附回去。
   * 纯逻辑在 runtime/recover.mjs，可脱离 Blender 单测（tests/ledger_recover_selftest.mjs）。
   */
  function reconcileLedger(rec: Record<string, unknown> | null): Record<string, unknown> | null {
    if (!rec || rec.status !== 'running') return rec;
    return reconcileLedgerFromDisk(rec, { winToWsl: winToWsl, wslToWin: wslToWin }, pidAlive);
  }
  const api: LedgerApi = { runs, LEDGER, ledgerAppend, ledgerState, ledgerList, runSnapshot, reconcileLedger };
  return api;
}
