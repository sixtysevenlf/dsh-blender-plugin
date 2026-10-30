// HTTP 服务配置与请求统计（T7）。PORT/HOST 在模块加载时解析一次，保持与拆分前一致。
import { CFG } from "../config.js";

const argv = process.argv.slice(2);
const portArg = argv.indexOf("--port");
export const PORT = Number(portArg >= 0 ? argv[portArg + 1] : (process.env.VIEWPORT_PORT || CFG.httpPort));
export const HOST = "127.0.0.1";

export interface HttpStats {
  [k: string]: unknown;
  frames: number; acts: number; cmds: number; loops: number; views: number; headless: number; plan: number; worker: number; txn: number; preset: number; job: number; selections: number;
  lastSelectionMs: number | null; lastPlanMs: number | null; lastViewMs: number | null; lastHeadlessMs: number | null; lastFrameMs: number | null; lastActMs: number | null;
  lastError: string | null; startedAt: number;
  launches?: number; lastLaunchMs?: number | null; perf?: number; opt?: number; staleLeasesReclaimed?: number; lastLeaseNote?: string | null;
}
export const stats: HttpStats = { frames: 0, acts: 0, cmds: 0, loops: 0, views: 0, headless: 0, plan: 0, worker: 0, txn: 0, preset: 0, job: 0, selections: 0, lastSelectionMs: null, lastPlanMs: null, lastViewMs: null, lastHeadlessMs: null, lastFrameMs: null, lastActMs: null, lastError: null, startedAt: Date.now() };

/** v0.9.4（P2-1）：各路由最近 20 次耗时的 p50/p95 —— 只观测，不自动重启。 */
const RTT_N = 20;
export const RTT: Record<string, number[]> = { frame: [], act: [], view: [], headless: [], plan: [], worker: [], job: [] };
export function pushRtt(kind: string, ms: unknown): void {
  const a = RTT[kind];
  if (!a) return;
  const v = Number(ms);
  a.push(Number.isFinite(v) ? Math.round(v) : 0);
  if (a.length > RTT_N) a.shift();
}
export function rttView(): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const k of Object.keys(RTT)) {
    const raw = RTT[k];
    const sorted = raw.slice().sort((x, y) => x - y);
    const q = (p: number): number | null => (sorted.length ? sorted[Math.min(sorted.length - 1, Math.floor(p * sorted.length))] : null);
    out[k] = { last: raw.length ? raw[raw.length - 1] : null, p50: q(0.5), p95: q(0.95), n: raw.length };
  }
  return out;
}

/** 每路由请求计数（诊断用：判断是否还有旧标签页在轮询已删除的接口） */
export const reqCounts = new Map<string, number>();
export function bump(p: string): void { reqCounts.set(p, (reqCounts.get(p) || 0) + 1); }
