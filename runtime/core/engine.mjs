// T6：createEngine 只做装配与分发 —— 各能力在 core/*、execution/*、transport/* 中实现。
// 行为冻结：公开 surface（工具/路由/指标/租约相关回执）与拆分前完全一致。
import path from "node:path";
import { fileURLToPath } from "node:url";
import { AddonClient, diagnose } from "./../transport/probes.mjs";
import { ADDRESS, WIN_TMP } from "./../paths.mjs";
import { winToWsl } from "./../config.mjs";
import { launchBlender } from "./../launch.mjs";
import { createKernel } from "./kernel.mjs";
import { createRenderState } from "./render-state.mjs";
import { createDumpResult } from "./output.mjs";
import { createLedger } from "./../execution/ledger.mjs";
import { createTrajectory } from "./../execution/trajectory.mjs";
import { createJobsApi } from "./../execution/jobs.mjs";
import { createWorkersApi } from "./../execution/workers.mjs";
import { createTxnApi } from "./../execution/txn.mjs";
import { createPresetApi } from "./../execution/preset.mjs";
import { createPublicMethods } from "./server-compat.mjs";
import { createPlanCallInner } from "./dispatch.mjs";
import { createPlanDispatch } from "./plan-dispatch.mjs";
import { createHeadlessRun } from "./../execution/headless.mjs";

const RUNTIME_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

/**
 * 装配引擎。参数只有 opts.address（addon 地址覆盖），与拆分前一致。
 * 注意：这里不实现任何业务；只负责把各个工厂接到同一个 deps 对象上，
 * 保证闭包共享同一份 addon / metrics / ledger / jobs（与拆分前单闭包语义一致）。
 */
export function createEngine(opts = {}) {
  const addon = new AddonClient(opts.address || ADDRESS);
  const { RENDER_FLAG_WSL, RENDER_FLAG_WIN, RENDER_STALE_MS, readRenderFlag, clearRenderFlag } = createRenderState({});
  // 需要 Blender 主线程的命令（ping 不在内：它走 addon 服务器线程，渲染中照样可用）
  const MAIN_THREAD_CMDS = new Set(['execute_code', 'get_scene_info', 'get_object_info', 'get_viewport_screenshot',
    'export_scene', 'describe_node_type', 'bpy_api_lookup', 'drain_human_activity',
    'get_activity_state', 'get_selection', 'send_selection_to_dsh']);
  const injected = new Set();
  /** 通道侧指标：谁在写 / 忙不忙 / 队列多深（供 /status、/who 用） */
  const metrics = { calls: 0, errors: 0, timeouts: 0, inflight: 0, lastCmd: null, lastCmdAt: null, lastOkAt: null, lastError: null, lastDiagnosis: null, views: 0, headlessRuns: 0, lastHeadlessMs: null, workerStarts: 0, workerExecs: 0,
                    lastErrorAt: null, aiPausedAt: null };
  const rawSend = addon.send.bind(addon);
  addon.send = async (type, params, timeoutMs, opts2) => {
    // 渲染中把主线程命令秒回 busy，不再排队到超时
    if (MAIN_THREAD_CMDS.has(String(type))) {
      const g = readRenderFlag();
      if (g.present && !g.stale) {
        metrics.errors++;
        const msg = 'BUSY_RENDER：Blender 正在渲染（' + String(Math.round((g.ageMs || 0) / 1000)) + ' s，scene='
          + String((g.info && g.info.scene) || '?') + '）—— 主线程命令会排队到超时；'
          + '用 blender_rt_plan(op="render_wait") 等它，或等渲染完再发（长渲染建议一开始就走 blender_rt_job）';
        metrics.lastError = msg;
        const e = new Error(msg);
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
      const msg = String((e && e.message) || e);
      metrics.errors++;
      metrics.lastError = msg;
      metrics.lastErrorAt = Date.now();
      if (/AI_PAUSED|AI 操作已暂停/.test(msg)) metrics.aiPausedAt = metrics.lastErrorAt;
      const transport = /timeout|closed|ECONN|ECONNRESET|socket/i.test(msg);
      if (transport) metrics.timeouts++;
      if (transport) {
        try { e.diagnosis = await diagnose(); metrics.lastDiagnosis = e.diagnosis; } catch (x) { /* 诊断自身失败不影响原错误 */ }
      } else {
        e.kind = 'execution';
      }
      throw e;
    } finally {
      metrics.inflight--;
    }
  };
  const SESSION_NAME = String(process.env.DSH_SESSION || ('plugin-pid-' + process.pid));
  const RESULTS_DIR = path.join(winToWsl(WIN_TMP), 'results');
  const E = { addon, injected, metrics, RENDER_FLAG_WIN, RENDER_FLAG_WSL, RENDER_STALE_MS, readRenderFlag, clearRenderFlag,
              HERE: RUNTIME_DIR, SESSION_NAME, RESULTS_DIR };
  const kernel = createKernel(E);
  for (const k of Object.keys(kernel)) E[k] = kernel[k];
  E.dumpResult = createDumpResult(E).dumpResult;
  const ledger = createLedger(E);
  E.ledger = ledger;
  for (const k of Object.keys(ledger)) E[k] = ledger[k];
  const trajectory = createTrajectory(E);
  for (const k of Object.keys(trajectory)) E[k] = trajectory[k];
  const jobsApi = createJobsApi(E);
  for (const k of Object.keys(jobsApi)) E[k] = jobsApi[k];
  const workersApi = createWorkersApi(E);
  for (const k of Object.keys(workersApi)) E[k] = workersApi[k];
  const txnApi = createTxnApi(E);
  const presetApi = createPresetApi(E);
  const compat = createPublicMethods(E);
  for (const k of Object.keys(compat)) E[k] = compat[k];
  const dispatchApi = createPlanCallInner(E);
  E.planCallInner = dispatchApi.planCallInner;
  const planDispatch = createPlanDispatch(E);
  E.perfCall = planDispatch.perfCall;
  const headlessApi = createHeadlessRun(E);
  const { start: _start, stop: _stop, ...restCompat } = compat;
  const surface = {
    addon: addon,
    plan: (op, payload) => planDispatch.planCall(op, payload),
    txn: (op, payload) => txnApi.txnCall(op, payload),
    preset: (op, payload) => presetApi.presetCall(op, payload),
    worker: { start: workersApi.workerStart, exec: workersApi.workerExec, see: workersApi.workerSee, watch: workersApi.workerWatch,
              reset: workersApi.workerReset, status: workersApi.workerStatus, stop: workersApi.workerStop,
              restart: workersApi.workerRestart, list: workersApi.workerList, snapshot: workersApi.workerSnapshot,
              state: workersApi.workerState },
    job: { start: jobsApi.jobStart, status: jobsApi.jobStatus, collect: jobsApi.jobCollect, kill: jobsApi.jobKill,
           list: jobsApi.jobList, wait: jobsApi.jobWait },
    launchBlender: (o) => launchBlender(o || {}),
    sceneEpoch: kernel.sceneEpoch,
    renderFlag: () => readRenderFlag(),
    ...restCompat,
    headless: headlessApi.headless,
    start: compat.start,
    stop: compat.stop,
  };
  return trajectory.withTrajectory(surface);
}
