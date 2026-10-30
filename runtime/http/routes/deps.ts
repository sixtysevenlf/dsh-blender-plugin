// 路由公共依赖类型（T15 迁移）：类型声明集中于此，运行时行为零变化。
import type { IncomingMessage, ServerResponse } from "node:http";
import type { EngineSurface } from "../../core/engine.js";
import type { HttpStats } from "../config.js";
import type { LeaseRec, LeaseView } from "../lease.js";
import type { SelectionStoreView } from "../host.js";

export type JsonFn = (res: ServerResponse, code: number, obj: unknown) => void;
export type HostJsonFn = (res: ServerResponse, code: number, obj: unknown, origin?: unknown) => void;
export type StreamJsonFn = (res: ServerResponse, producer: () => Promise<unknown>, hbMs?: number) => Promise<unknown>;
export type ReadBodyFn = (req: IncomingMessage, limit?: number) => Promise<string>;
export type LeaseGateFn = (payload?: Record<string, unknown> | null) => Record<string, unknown> | null;
export type IsReadOnlyFn = (path: string, op: unknown) => boolean;
export type LeaseAcquireFn = (holder: unknown, ttlMs: unknown, force: unknown, scope?: unknown) => { denied: true; holder: string | null; expiresInMs: number } | { granted: true; lease: LeaseView };

/** 路由工厂共同依赖（server.ts 组装的 E 对象）。 */
export interface HttpRouteDeps {
  [k: string]: unknown;
  engine: EngineSurface;
  selectionStore: SelectionStoreView;
  json: JsonFn;
  hostJson: HostJsonFn;
  hostErrText: (e: unknown) => string;
  streamJson: StreamJsonFn;
  readBody: ReadBodyFn;
  leaseGate: LeaseGateFn;
  isReadOnly: IsReadOnlyFn;
  leaseView: () => LeaseView;
  leaseAcquire: LeaseAcquireFn;
  releaseStaleLease: (why: unknown) => string | null;
  LEASE: LeaseRec;
  pushRtt: (kind: string, ms: unknown) => void;
  stats: HttpStats;
  PORT: number;
  describeConfig: (over?: { httpPort?: number | string }) => Record<string, unknown>;
  engineProvenance: (opts?: unknown) => unknown;
  reqCounts: Map<string, number>;
  rttView: () => Record<string, unknown>;
}

/** JSON 对象的边界解析：非对象一律当空对象（与旧 JS 的 `payload.x` 读取等价）。 */
export function objArg(v: unknown): Record<string, unknown> {
  return (v !== null && typeof v === "object") ? v as Record<string, unknown> : {};
}
