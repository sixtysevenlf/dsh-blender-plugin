// 长任务可观测性：DSH_STAGE / DSH_PROGRESS 扫描与心跳文件（T6）
// @imports
import fs from "node:fs";
import path from "node:path";
import { errorMessage } from "./json.js";


export interface StageRecord {
  stage?: unknown;
  stageAt?: number | null;
  stageName?: string | null;
  progress?: unknown;
  progressAt?: number | null;
  progressHistory?: Array<{ at: number; p: unknown }>;
  lastOutputAt?: number | null;
  lines?: number;
  logs?: { stdoutWsl?: string } | null;
  dir?: string;
  startedAt: number;
  heartbeat?: Record<string, unknown>;
}

const STAGE_TAG = 'DSH_STAGE ';
/** v2.6（本轮反馈 #4）：进度通道 —— 脚本侧 dsh_progress(...) / K.progress(...) 打 DSH_PROGRESS {json}，
 *  op=status/collect 直接能读到最后一条（渲染逐帧 / 批处理逐件 / 下载百分比）。
 *  和 stage 的区别：stage 是"阶段跃迁"（少量），progress 是"阶段内进度"（高频、可覆盖、留最近 20 条）。 */
const PROGRESS_TAG = 'DSH_PROGRESS ';
function scanProgressLine(line: string, rec: StageRecord, now: number): boolean {
  if (line.indexOf(PROGRESS_TAG) !== 0) return false;
  try {
    const o = JSON.parse(line.slice(PROGRESS_TAG.length).trim()) as Record<string, unknown>;
    rec.progress = o; rec.progressAt = now;
    rec.progressHistory = (rec.progressHistory || []).concat([{ at: now, p: o }]).slice(-20);
    if (o.name) { rec.stageName = String(o.name); rec.stageAt = now; }
  } catch (e) {
    rec.progress = { raw: line.slice(0, 200), parse_error: errorMessage(e) };
    rec.progressAt = now;
  }
  return true;
}
function scanStageLine(line: string, rec: StageRecord, now: number): boolean {
  if (line.indexOf(STAGE_TAG) !== 0) return false;
  try {
    const obj = JSON.parse(line.slice(STAGE_TAG.length).trim()) as Record<string, unknown>;
    rec.stage = obj; rec.stageAt = now; rec.stageName = obj.name ? String(obj.name) : null;
    return true;
  } catch (e) {
    rec.stage = { raw: line.slice(0, 200), parse_error: errorMessage(e) };
    rec.stageAt = now;
    return false;
  }
}
/** 把一段 chunk 按行喂给 stage 扫描（跨 chunk 的半行用尾部缓冲兜住） */
function makeStageScanner(rec: StageRecord): (chunk: unknown) => void {
  let tail = '';
  return (chunk) => {
    const now = Date.now();
    rec.lastOutputAt = now;
    const text = tail + String(chunk);
    const lines = text.split('\n');
    tail = lines.pop() || '';
    if (tail.length > 8192) tail = tail.slice(-2048);
    rec.lines = (rec.lines || 0) + lines.length;
    for (const l of lines) {
      const s = l.replace(/\r$/, '');
      if (s.indexOf(STAGE_TAG) === 0) scanStageLine(s, rec, now);
      else if (s.indexOf(PROGRESS_TAG) === 0) scanProgressLine(s, rec, now);
    }
  };
}
/**
 * v2.6（本轮反馈 #4）：运行心跳 —— 每 5s 更新 rec.heartbeat（并可选 append 到 <dir>/heartbeat.jsonl）。
 * 现场问题：一帧 EEVEE 渲染 2–3 分钟，op=status 只有 stage/idleMs 两个数字，看不出"到第几帧 / 写到多少字节"。
 * heartbeat 给：elapsedMs / logBytes / lines / stage / lastProgressAt —— 够判断"它在动还是在卡"。
 */
function startHeartbeat(rec: StageRecord, dirWsl?: string | null): () => void {
  const tick = (): void => {
    try {
      let bytes = 0;
      if (rec.logs && rec.logs.stdoutWsl) { try { bytes = fs.statSync(rec.logs.stdoutWsl).size } catch (e) {} }
      if (!bytes && rec.dir) { try { bytes = fs.statSync(path.join(rec.dir, 'stdout.log')).size } catch (e) {} }
      const hb: Record<string, unknown> = { at: Date.now(), elapsedMs: Date.now() - rec.startedAt, logBytes: bytes,
                   lines: rec.lines || 0, stage: rec.stageName || null,
                   progressAt: rec.progressAt || null,
                   progressAgeMs: rec.progressAt ? (Date.now() - rec.progressAt) : null };
      rec.heartbeat = hb;
      if (dirWsl) { try { fs.appendFileSync(path.join(dirWsl, 'heartbeat.jsonl'), JSON.stringify(hb) + String.fromCharCode(10), 'utf8') } catch (e) {} }
    } catch (e) { /* 心跳绝不能把任务搞挂 */ }
  };
  const t = setInterval(tick, 5000);
  if (t.unref) t.unref();
  return () => { try { clearInterval(t) } catch (e) {} };
}

export { STAGE_TAG, PROGRESS_TAG, scanProgressLine, scanStageLine, makeStageScanner, startHeartbeat };
