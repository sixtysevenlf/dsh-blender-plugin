// 作业区助手（T7）：/host/jobs 的回执裁剪与 limit clamp。纯函数、零 I/O。
// ---------------------------------------------------------------- v2.1（§8.3）作业区助手
/**
 * §8.3 `<trimmed job>`：只留契约点名的字段，**裁掉大字段**（`script`、超长路径串等）。
 * `resultJson` > 8 KB 截断并置 `resultTruncated:true`（别让一次轮询把面板撑爆）。
 * **纯函数、零 I/O** —— 这是 `/host/jobs` 能做到"零 Blender 往返"的前提。
 */
function trimJob(j: unknown): Record<string, unknown> {
  const src = ((j && typeof j === 'object') ? j : {}) as Record<string, unknown>;
  let resultJson = (src.resultJson === undefined) ? null : src.resultJson;
  let resultTruncated = false;
  if (resultJson !== null) {
    try {
      const s = JSON.stringify(resultJson);
      if (s !== undefined && s.length > 8192) {
        resultJson = { truncated: true, preview: s.slice(0, 8192) };
        resultTruncated = true;
      }
    } catch (e) { resultJson = null; resultTruncated = true; }
  }
  return {
    id: String(src.id === undefined ? '' : src.id),
    kind: src.kind === undefined ? null : src.kind,
    status: src.status === undefined ? 'unknown' : src.status,
    runId: src.runId === undefined ? null : src.runId,
    pid: src.pid === undefined ? null : src.pid,
    pidAlive: !!src.pidAlive,
    ms: Number(src.ms) || 0,
    timeoutMs: src.timeoutMs === undefined ? null : src.timeoutMs,
    outdir: src.outdir === undefined ? null : src.outdir,
    logDir: src.logDir === undefined ? null : src.logDir,
    exitCode: src.exitCode === undefined ? null : src.exitCode,
    signal: src.signal === undefined ? null : src.signal,
    stage: src.stage === undefined ? null : src.stage,
    stageName: src.stageName === undefined ? null : src.stageName,
    stageAgoMs: src.stageAgoMs === undefined ? null : src.stageAgoMs,
    lastOutputAt: src.lastOutputAt === undefined ? null : src.lastOutputAt,
    idleMs: src.idleMs === undefined ? null : src.idleMs,
    lines: Number(src.lines) || 0,
    logBytes: Number(src.logBytes) || 0,
    resultJson: resultJson,
    resultTruncated: resultTruncated,
    fromLedger: !!src.fromLedger,
  };
}

/** §8.3 clamp 口径：缺省/非数 → 默认值；给了数 → 夹进 [lo,hi]（**不报错**，照 §1.x 既有口径） */
function clampIntParam(v: unknown, dflt: number, lo: number, hi: number): number {
  if (v === null || v === undefined || v === '') return dflt;
  const n = Number(v);
  if (!Number.isFinite(n)) return dflt;
  return Math.max(lo, Math.min(hi, Math.round(n)));
}
export { trimJob, clampIntParam };
