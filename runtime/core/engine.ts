// T6：createEngine 只做装配与分发 —— 各能力在 core/*、execution/*、transport/* 中实现。
// 行为冻结：公开 surface（工具/路由/指标/租约相关回执）与拆分前完全一致。
import path from "node:path";
import { fileURLToPath } from "node:url";
import { AddonClient, diagnose } from "./../transport/probes.js";
import type { AddonClient as AddonClientType } from "./../transport/probes.js";
import { ADDRESS, WIN_TMP } from "./../paths.js";
import { winToWsl } from "./../config.js";
import { launchBlender } from "./../launch.js";
import { createKernel } from "./kernel.js";
import { createRenderState } from "./render-state.js";
import { createDumpResult } from "./output.js";
import { createLedger } from "./../execution/ledger.js";
import { createTrajectory } from "./../execution/trajectory.js";
import { createJobsApi } from "./../execution/jobs.js";
import { createWorkersApi } from "./../execution/workers.js";
import { createTxnApi } from "./../execution/txn.js";
import { createPresetApi } from "./../execution/preset.js";
import { createPublicMethods } from "./server-compat.js";
import { createPlanCallInner } from "./dispatch.js";
import { createPlanDispatch } from "./plan-dispatch.js";
import { createHeadlessRun } from "./../execution/headless.js";
import { errorMessage } from "./json.js";

const RUNTIME_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

/** 通道侧指标：谁在写 / 忙不忙 / 队列多深（供 /status、/who 用） */
export interface EngineMetrics {
  [k: string]: unknown;
  calls: number;
  errors: number;
  timeouts: number;
  inflight: number;
  lastCmd: string | null;
  lastCmdAt: number | null;
  lastOkAt: number | null;
  lastError: string | null;
  lastDiagnosis: unknown;
  views: number;
  headlessRuns: number;
  lastHeadlessMs: number | null;
  workerStarts: number;
  workerExecs: number;
  lastErrorAt: number | null;
  aiPausedAt: number | null;
}

export type JobApi = ReturnType<typeof createJobsApi>;
export type WorkerApi = ReturnType<typeof createWorkersApi>;

/** 引擎 surface 上的作业视图（方法名与 server.mjs 兼容面一致）。 */
export interface JobView {
  start: JobApi['jobStart'];
  status: JobApi['jobStatus'];
  collect: JobApi['jobCollect'];
  kill: JobApi['jobKill'];
  list: JobApi['jobList'];
  wait: JobApi['jobWait'];
}

/** 引擎 surface 上的 worker 视图。 */
export interface WorkerView {
  start: WorkerApi['workerStart'];
  exec: WorkerApi['workerExec'];
  see: WorkerApi['workerSee'];
  watch: WorkerApi['workerWatch'];
  reset: WorkerApi['workerReset'];
  status: WorkerApi['workerStatus'];
  stop: WorkerApi['workerStop'];
  restart: WorkerApi['workerRestart'];
  list: WorkerApi['workerList'];
  snapshot: WorkerApi['workerSnapshot'];
  state: WorkerApi['workerState'];
}

export interface EngineOptions {
  address?: { host?: string; port?: number } | null;
  [k: string]: unknown;
}

/** 引擎公开面：http 层与脚本按能力名取用；多余字段经 index signature 保留 */
export interface EngineSurface {
  [k: string]: unknown;
  addon: AddonClientType;
  plan: (op: string, payload?: unknown) => Promise<unknown>;
  txn: (op: string, payload?: unknown) => Promise<unknown>;
  preset: (op: string, payload?: unknown) => Promise<unknown>;
  worker: WorkerView;
  job: JobView;
  launchBlender: (o?: unknown) => unknown;
  sceneEpoch: () => Promise<unknown>;
  renderFlag: () => unknown;
  headless: (opts?: Record<string, unknown>) => Promise<Record<string, unknown>>;
  start: () => Promise<unknown>;
  stop: () => unknown;
  metrics: () => unknown;
  protocol: () => Promise<unknown>;
  doctor: () => Promise<unknown>;
  status: () => Promise<unknown>;
  act: (code: unknown, timeoutMs?: number, file?: unknown) => Promise<unknown>;
  cmd: (name: unknown, params?: Record<string, unknown>, timeoutMs?: number) => Promise<unknown>;
  commands: () => Promise<unknown>;
  loopStart: (spec: unknown) => Promise<unknown>;
  loopStatus: (history?: number) => Promise<unknown>;
  loopStop: () => Promise<unknown>;
  loopBench: (iterations?: number) => Promise<unknown>;
  loopBoard: (limit?: number, groups?: boolean) => Promise<unknown>;
  loopExport: (opts?: Record<string, unknown>) => Promise<unknown>;
  loopHelp: () => Promise<unknown>;
  frameArea: (areaIndex?: number) => Promise<unknown>;
  perf: (op: string, payload?: unknown) => Promise<unknown>;
  opt: (op: string, payload?: unknown) => Promise<unknown>;
  frame: (maxSize?: number) => Promise<unknown>;
  view: (spec?: Record<string, unknown>) => Promise<unknown>;
  hostSceneItems: (spec?: Record<string, unknown>) => Promise<unknown>;
}

/**
 * 装配引擎。参数只有 opts.address（addon 地址覆盖），与拆分前一致。
 * 注意：这里不实现任何业务；只负责把各个工厂接到同一个 deps 对象上，
 * 保证闭包共享同一份 addon / metrics / ledger / jobs（与拆分前单闭包语义一致）。
 */
export function createEngine(opts: EngineOptions = {}): EngineSurface {
  const addon = new AddonClient((opts.address || ADDRESS) as { host: string; port: number });
  const { RENDER_FLAG_WSL, RENDER_FLAG_WIN, RENDER_STALE_MS, readRenderFlag, clearRenderFlag } = createRenderState({});
  // 需要 Blender 主线程的命令（ping 不在内：它走 addon 服务器线程，渲染中照样可用）
  const MAIN_THREAD_CMDS = new Set<string>(['execute_code', 'get_scene_info', 'get_object_info', 'get_viewport_screenshot',
    'export_scene', 'describe_node_type', 'bpy_api_lookup', 'drain_human_activity',
    'get_activity_state', 'get_selection', 'send_selection_to_dsh']);
  const injected = new Set<string>();
  const metrics: EngineMetrics = { calls: 0, errors: 0, timeouts: 0, inflight: 0, lastCmd: null, lastCmdAt: null, lastOkAt: null, lastError: null, lastDiagnosis: null, views: 0, headlessRuns: 0, lastHeadlessMs: null, workerStarts: 0, workerExecs: 0,
                    lastErrorAt: null, aiPausedAt: null };
  const rawSend = addon.send.bind(addon);
  addon.send = async (type: string, params: Record<string, unknown> = {}, timeoutMs?: number) => {
    // 渲染中把主线程命令秒回 busy，不再排队到超时
    if (MAIN_THREAD_CMDS.has(String(type))) {
      const g = readRenderFlag();
      if (g.present && !g.stale) {
        metrics.errors++;
        const msg = 'BUSY_RENDER：Blender 正在渲染（' + String(Math.round((g.ageMs || 0) / 1000)) + ' s，scene='
          + String((g.info && g.info.scene) || '?') + '）—— 主线程命令会排队到超时；'
          + '用 blender_rt_plan(op="render_wait") 等它，或等渲染完再发（长渲染建议一开始就走 blender_rt_job）';
        metrics.lastError = msg;
        const e = new Error(msg) as Error & { renderBusy?: boolean; renderGuard?: unknown; diagnosis?: unknown; kind?: string };
        e.renderBusy = true;
        e.renderGuard = g;
        throw e;
      }
    }
    metrics.calls++;
    metrics.inflight++;
    metrics.lastCmd = type;
    metrics.lastCmdAt = Date.now();
    try {
      const r = await rawSend(type, params, timeoutMs);
      metrics.lastOkAt = Date.now();
      metrics.lastDiagnosis = null;
      metrics.aiPausedAt = null;
      return r;
    } catch (e) {
      const msg = errorMessage(e);
      metrics.errors++;
      metrics.lastError = msg;
      metrics.lastErrorAt = Date.now();
      if (/AI_PAUSED|AI 操作已暂停/.test(msg)) metrics.aiPausedAt = metrics.lastErrorAt;
      const transport = /timeout|closed|ECONN|ECONNRESET|socket/i.test(msg);
      if (transport) metrics.timeouts++;
      const err = e as Error & { renderBusy?: boolean; renderGuard?: unknown; diagnosis?: unknown; kind?: string };
      if (transport) {
        try { err.diagnosis = await diagnose(); metrics.lastDiagnosis = err.diagnosis; } catch (x) { /* 诊断自身失败不影响原错误 */ }
      } else {
        err.kind = 'execution';
      }
      throw err;
    } finally {
      metrics.inflight--;
    }
  };
  const SESSION_NAME = String(process.env.DSH_SESSION || ('plugin-pid-' + process.pid));
  const RESULTS_DIR = path.join(winToWsl(WIN_TMP), 'results');
  const E: Record<string, unknown> = { addon, injected, metrics, RENDER_FLAG_WIN, RENDER_FLAG_WSL, RENDER_STALE_MS, readRenderFlag, clearRenderFlag,
              HERE: RUNTIME_DIR, SESSION_NAME, RESULTS_DIR };
  const kernel = createKernel(E as unknown as Parameters<typeof createKernel>[0]);
  Object.assign(E, kernel);
  E.dumpResult = createDumpResult(E as unknown as Parameters<typeof createDumpResult>[0]).dumpResult;
  const ledger = createLedger(E);
  E.ledger = ledger;
  Object.assign(E, ledger);
  const trajectory = createTrajectory(E as unknown as Parameters<typeof createTrajectory>[0]);
  Object.assign(E, trajectory);
  const jobsApi = createJobsApi(E as unknown as Parameters<typeof createJobsApi>[0]);
  Object.assign(E, jobsApi);
  const workersApi = createWorkersApi(E as unknown as Parameters<typeof createWorkersApi>[0]);
  Object.assign(E, workersApi);
  const txnApi = createTxnApi(E as unknown as Parameters<typeof createTxnApi>[0]);
  const presetApi = createPresetApi(E as unknown as Parameters<typeof createPresetApi>[0]);
  const compat = createPublicMethods(E as unknown as Parameters<typeof createPublicMethods>[0]);
  Object.assign(E, compat);
  const dispatchApi = createPlanCallInner(E as unknown as Parameters<typeof createPlanCallInner>[0]);
  E.planCallInner = dispatchApi.planCallInner;
  const planDispatch = createPlanDispatch(E as unknown as Parameters<typeof createPlanDispatch>[0]);
  E.perfCall = planDispatch.perfCall;
  const headlessApi = createHeadlessRun(E as unknown as Parameters<typeof createHeadlessRun>[0]);
  const { start: _start, stop: _stop, ...restCompat } = compat;
  const surface: EngineSurface = {
    addon: addon,
    plan: (op: string, payload?: unknown) => planDispatch.planCall(op, payload),
    txn: (op: string, payload?: unknown) => txnApi.txnCall(op, payload),
    preset: (op: string, payload?: unknown) => presetApi.presetCall(op, payload),
    worker: { start: workersApi.workerStart, exec: workersApi.workerExec, see: workersApi.workerSee, watch: workersApi.workerWatch,
              reset: workersApi.workerReset, status: workersApi.workerStatus, stop: workersApi.workerStop,
              restart: workersApi.workerRestart, list: workersApi.workerList, snapshot: workersApi.workerSnapshot,
              state: workersApi.workerState },
    job: { start: jobsApi.jobStart, status: jobsApi.jobStatus, collect: jobsApi.jobCollect, kill: jobsApi.jobKill,
           list: jobsApi.jobList, wait: jobsApi.jobWait },
    launchBlender: (o?: unknown) => launchBlender((o || {}) as Parameters<typeof launchBlender>[0]),
    sceneEpoch: kernel.sceneEpoch,
    renderFlag: () => readRenderFlag(),
    ...restCompat,
    headless: headlessApi.headless,
    start: compat.start,
    stop: compat.stop,
  };
  return trajectory.withTrajectory(surface) as EngineSurface;
}
