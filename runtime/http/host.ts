// 宿主集成（/host/*）：状态视图 + 7 条 /host 路由的处理器（T7）。
// 契约见 docs/宿主集成-接口契约-v2.3增补.md §S1–§S3；所有响应带 CORS，只读语义（不碰写租约）。
import type { IncomingMessage, ServerResponse } from "node:http";
import type { EngineSurface } from "../core/engine.js";
import { stats } from "./config.js";
import { leaseView } from "./lease.js";
import { hostCorsHeaders, readBody, clampItemsLimit } from "./response.js";
import { trimJob, clampIntParam } from "./jobs.js";

export interface SelectionStoreView {
  put: (payload: unknown) => Record<string, unknown>;
  snapshot: () => unknown;
  consume: (seq: unknown, payload: Record<string, unknown>) => Record<string, unknown>;
}
export interface HostRoutesDeps {
  engine: EngineSurface;
  selectionStore: SelectionStoreView;
  hostJson: (res: ServerResponse, code: number, obj: unknown, origin?: unknown) => void;
  hostErrText: (e: unknown) => string;
}

export function createHostRoutes(E: HostRoutesDeps) {
  const { engine, selectionStore, hostJson, hostErrText } = E;
/**
 * v2.6.1：`GET /host/state` 的响应体（纯内存，零 Blender 往返；任何一块取不到都给保守默认值，不抛）。
 * 「AI 已暂停」只能在 addon 拒掉一条命令之后才知道（后端不为它轮询 Blender），成功一次就清空。
 */
function hostStateView() {
  const now = Date.now();
  const age = (t: unknown): number | null => (typeof t === 'number' && t ? now - t : null);
  let m: Record<string, unknown> = {};
  try { m = (engine.metrics() || {}) as Record<string, unknown>; } catch (e) { m = {}; }
  let render: { busy: boolean; ageMs: number | null; scene: unknown } = { busy: false, ageMs: null, scene: null };
  try {
    const g = engine.renderFlag() as { present?: unknown; stale?: unknown; ageMs?: unknown; info?: unknown } | null;
    if (g && g.present) render = { busy: !g.stale, ageMs: (g.ageMs as number | null), scene: ((g.info as Record<string, unknown> | null)?.['scene']) || null };
  } catch (e) { /* 读不到标记 = 不在渲染 */ }
  let running = 0;
  try { running = (engine.job.list() || []).filter((j) => j && j.status === 'running').length; } catch (e) { running = 0; }
  return {
    ok: true,
    channel: {
      lastOkAgeMs: age(m.lastOkAt),
      lastCmd: m.lastCmd || null,
      lastCmdAgeMs: age(m.lastCmdAt),
      inflight: Number(m.inflight) || 0,
      lastError: m.lastError ? String(m.lastError).slice(0, 200) : null,
      lastErrorAgeMs: age(m.lastErrorAt),
      diagnosis: (m.lastDiagnosis && (m.lastDiagnosis as { kind?: unknown }).kind) ? String((m.lastDiagnosis as { kind?: unknown }).kind) : null,
      aiPaused: !!m.aiPausedAt,
    },
    lease: leaseView(),
    render: render,
    jobs: { running: running },
    backend: { pid: process.pid, uptimeMs: now - stats.startedAt },
  };
}

async function handleHost(req: IncomingMessage, res: ServerResponse, url: URL, p: string): Promise<void> {
  const origin: unknown = req.headers.origin || null;

  // §1.1 CORS 预检：204 空体
  if (req.method === 'OPTIONS') {
    res.writeHead(204, hostCorsHeaders(origin));
    res.end();
    return;
  }

  // §1.6 `@` 提及数据源
  if (req.method === 'GET' && p === '/host/scene-items') {
    let out: Record<string, unknown> = {};
    try {
      out = (await engine.hostSceneItems({ q: url.searchParams.get('q') || '',
                                          limit: clampItemsLimit(url.searchParams.get('limit')) })) as Record<string, unknown>;
    } catch (e) {
      stats.lastError = hostErrText(e);
      hostJson(res, 502, { ok: false, error: hostErrText(e) }, origin);
      return;
    }
    hostJson(res, 200, { ok: true, objects: out.objects || [], materials: out.materials || [],
                         collections: out.collections || [] }, origin);
    return;
  }

  // §8.3 headless 作业区（**只读**：只读后端已有的 engine.job 台账，**零 Blender 往返**）
  // 为什么面板看不到 headless：它跑的是独立 blender -b 进程（自己的场景），不碰 GUI ⇒ 视口/面板无画面。
  if (req.method === 'GET' && p === '/host/jobs') {
    const limit = clampIntParam(url.searchParams.get('limit'), 20, 1, 100);
    let all: Array<Record<string, unknown>> = [];
    try { all = engine.job.list() || []; } catch (e) { all = []; }
    const trimmed = all.map(trimJob);
    // running 在前；其余按耗时倒序（契约 §8.3）
    trimmed.sort((a, b) => ((b.status === 'running' ? 1 : 0) - (a.status === 'running' ? 1 : 0)) || ((Number(b.ms) || 0) - (Number(a.ms) || 0)));
    const running = trimmed.filter((j) => j.status === 'running').length;
    hostJson(res, 200, { ok: true, jobs: trimmed.slice(0, limit), running: running, total: trimmed.length }, origin);
    return;
  }
  if (req.method === 'GET' && p === '/host/jobs/log') {
    const id = String(url.searchParams.get('id') || '');
    const tail = clampIntParam(url.searchParams.get('tail'), 8000, 1, 200000);
    let snap: Record<string, unknown> | null = null;
    try { snap = engine.job.collect(id, tail); } catch (e) { snap = null; }
    // `engine.job.collect` 对不存在的 id 回 `{status:'unknown', ok:false}`（不是 null）⇒ 这里归一成明确的失败态
    if (!snap || snap.status === 'unknown' || snap.ok === false) {
      hostJson(res, 200, { ok: false, id: id, error: 'unknown_job', stdout: null, stderr: null, job: null }, origin);
      return;
    }
    hostJson(res, 200, { ok: true, id: id,
                         stdout: (snap.stdoutTail === undefined ? null : snap.stdoutTail),
                         stderr: (snap.stderrTail === undefined ? null : snap.stderrTail),
                         job: trimJob(snap) }, origin);
    return;
  }

  // v2.6.1（dsh-TUI 页脚状态字段，契约 v2.3 增补 §S6）：通道 / 租约 / 渲染 / 作业 / AI 暂停一次给全。
  // **零 Blender 往返**：只读内存里的 engine.metrics()、租约表、render_guard 标记文件与作业台账。
  if (req.method === 'GET' && p === '/host/state') {
    hostJson(res, 200, hostStateView(), origin);
    return;
  }

  // ── v2.3（Blender 原生选中 → DSH，契约 v2.3 增补 §S1–§S3）──────────────
  // addon 的 N 面板「发送选中到 DSH」直接 POST 到这里；客户端半边轮询 latest 并插入草稿。
  // 三条都**零 Blender 往返**：addon 主动推，轮询方只读内存。
  if (req.method === 'POST' && p === '/host/selection') {
    const raw = await readBody(req);
    let payload: unknown = null;
    try { payload = JSON.parse(raw); } catch (e) { payload = null; }
    const t0 = Date.now();
    const stored = selectionStore.put(payload);
    if (!stored.ok) {
      hostJson(res, 400, { ok: false, error: stored.error || 'invalid_payload' }, origin);
      return;
    }
    stats.selections = (stats.selections || 0) + 1;
    stats.lastSelectionMs = Date.now() - t0;
    hostJson(res, 200, stored, origin);
    return;
  }
  if (req.method === 'GET' && p === '/host/selection/latest') {
    hostJson(res, 200, selectionStore.snapshot(), origin);
    return;
  }
  if (req.method === 'POST' && p === '/host/selection/consume') {
    const raw = await readBody(req);
    let payload: unknown = null;
    try { payload = JSON.parse(raw); } catch (e) { payload = null; }
    const payloadRec: Record<string, unknown> = (payload && typeof payload === 'object') ? payload as Record<string, unknown> : {};
    const consumed = selectionStore.consume(payloadRec.seq, payloadRec);
    hostJson(res, consumed.ok ? 200 : 400, consumed, origin);
    return;
  }

  hostJson(res, 404, { ok: false, error: 'not found', path: p }, origin);
}
  return {
    async host(req: IncomingMessage, res: ServerResponse, url: URL, p: string): Promise<void> {
      try { await handleHost(req, res, url, p); }
      catch (e) {
        try { hostJson(res, 500, { ok: false, error: hostErrText(e) }, req.headers.origin || null); }
        catch (e2) { try { res.end(); } catch (e3) { /* 已经断了就算了 */ } }
      }
      return;
    },
  };
}
