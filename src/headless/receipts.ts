import { HOLDER } from '../constants.js'
import { backendJson, backendPost, leasedText } from '../host/http.js'
import { clipText, safeStringify } from '../ui/cards.js'

/**
 * v0.9.3（D1/F3）：客户端单次等待上限。超过它就**不干等**，转作业层并回 jobId（对齐 bash 的 promoted 语义）。
 * 默认 100 s：外层 run_code 的默认 deadline 是 120 s，留 20 s 余量给回执与状态查询（promoted 路径只是一次很快的 /job 查询）。
 * 调大它 = 允许单次同步等更久（代价：外层 deadline 到点时更容易"结果在服务端、客户端拿不到"）。
 */
export const HEADLESS_WAIT_MS = (() => {
  const n = Number(process.env.DSH_HEADLESS_WAIT_MS)
  // 下限 1000ms：验收测试会把它调到秒级来复现 promoted 路径（别把合法的小窗口当成脏值）
  return Number.isFinite(n) && n >= 1000 ? n : 100000
})()
/** run id 由**客户端**生成：窗口到点时立刻就能报出一个可用句柄（服务端会用它登记台账） */
export function newRunId(): string {
  return 'run-' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6)
}
/** 后台化 + 可选等待（F3）：POST /headless asJob:true 拿 jobId，再 op=wait 等一小会儿 */
export async function startJobThenMaybeWait(port: number, body: any, waitS: number | null): Promise<any> {
  const jr: any = await backendPost(port, '/headless', Object.assign({}, body, { asJob: true }), 60000)
  const lt = leasedText(jr)
  if (lt) return { text: lt }
  if (!jr || jr.error) return { text: 'HEADLESS 转作业层失败 · ' + String((jr && (jr.error || jr.raw)) || 'unknown') }
  // 后端 /headless 的回包是 {ok, ms, result:<引擎回执>} —— jobId 在 result 里
  const inner: any = jr.result && typeof jr.result === 'object' ? jr.result : jr
  const jobId = String(inner.jobId || (inner.job && inner.job.id) || '')
  const budget = waitS && waitS > 0 ? Math.min(Math.round(waitS * 1000), HEADLESS_WAIT_MS + 2000) : 0
  if (!budget) return { jobId: jobId, job: inner.job || null, waited: false }
  const wr: any = await backendPost(port, '/job', { op: 'wait', id: jobId, timeoutMs: budget }, budget + 30000)
  const lt2 = leasedText(wr)
  if (lt2) return { text: lt2 }
  const job = (wr && wr.job) || null
  if (job && job.status && job.status !== 'running') return { jobId: jobId, job: job, waited: true, finished: true }
  return { jobId: jobId, job: job || inner.job || null, waited: true, finished: false }
}
/** shots 回执归一化：sync 路径给 {rows:[…]}，job 快照给 qc_render 原样的 {views:[…]} */
export function normalizeShots(s: any): any {
  if (!s || typeof s !== 'object') return null
  let rows = Array.isArray(s.rows) ? s.rows : null
  if (!rows && Array.isArray(s.views)) {
    rows = s.views.map((e: any) => {
      const cov = e && e.frame && typeof e.frame.coverage === 'number' ? e.frame.coverage : null
      const row: any = {
        name: (e && e.view) || null,
        path: (e && e.path) || null,
        ms: (e && e.ms) || null,
        bytes: (e && e.bytes) || null,
        md5: (e && e.hash) || null,
        res: (e && e.res) || null,
        device: (e && e.device) || null,
        coverage_estimate: cov
      }
      if (cov !== null && cov < 0.05) row.warning = { code: 'subject_too_small', coverage_estimate: cov }
      return row
    })
  }
  const covs = (rows || []).map((r: any) => r.coverage_estimate).filter((x: any) => typeof x === 'number')
  return {
    ok: !!s.ok,
    count: (rows || []).length,
    rows: rows || [],
    coverage_min: typeof s.coverage_min === 'number' ? s.coverage_min : covs.length ? Math.min.apply(null, covs) : null,
    outdir: s.outdir || null,
    jsonl: s.jsonl || null,
    total_ms: s.total_ms || null,
    engine: s.engine || null,
    samples: s.samples || null,
    device_line: s.device_line || null,
    unknown_views: s.unknown_views || null,
    error: s.error || null
  }
}
/** 结构化信封（block 0）：稳定字段 + 有界大小（大结果只给 resultPath） */
export function receiptEnvelope(res: any, ctx: any): any {
  const r: any = res || {}
  const rj = r.resultJson !== undefined ? r.resultJson : r.result
  const rjStr = rj === null || rj === undefined ? null : safeStringify(rj)
  const ENV_LIMIT = 8000
  const big = !!(rjStr && rjStr.length > ENV_LIMIT)
  const artifacts = (Array.isArray(r.artifacts) ? r.artifacts : []).slice(0, 40).map((a: any) => (typeof a === 'string' ? { name: a } : a))
  return {
    kind: ctx.mode === 'job' ? 'job' : r.kind || 'headless',
    mode: ctx.mode,
    status: r.status || null,
    // 作业层快照没有 ok 字段：按终态推断（done/finished=成功；failed/killed/timeout/stale=失败；running=未判定）
    ok:
      typeof r.ok === 'boolean'
        ? r.ok
        : r.status === 'done' || r.status === 'finished'
          ? true
          : r.status === 'failed' || r.status === 'killed' || r.status === 'timeout' || r.status === 'stale'
            ? false
            : null,
    runId: r.runId || ctx.runId || null,
    jobId: ctx.jobId || (r.id && String(r.id).indexOf('job-') === 0 ? r.id : null),
    id: r.id || null,
    // v0.9.4（P0 返回通道）：这里原来是 `? true : undefined` —— 宿主 dsh-util-values 的
    // lossless-JSON 门**拒收值为 undefined 的自有可枚举键**（JSON.stringify 会把它丢掉，
    // 所以肉眼看不出问题），于是 blender_rt_headless / rt_job 的每一条回执都被判
    // `value is not lossless JSON`，无头批处理这条第一路径整条被打死。改成「有才给键」。
    ...(ctx.mode === 'promoted' ? { promoted: true } : {}),
    ms: typeof r.ms === 'number' ? r.ms : null,
    timeoutMs: r.timeoutMs || null,
    exitCode: r.exitCode === undefined ? null : r.exitCode,
    timedOut: !!r.timedOut,
    resultJson: big ? null : rj === undefined ? null : rj,
    resultTruncated: !!(r.resultTruncated || big),
    resultPath: r.resultPath || r.outJson || null,
    resultPathWsl: r.resultPathWsl || r.outJsonWsl || null,
    resultBytes: r.resultBytes || null,
    resultParseError: r.resultParseError || null,
    stdoutTail: clipText(r.stdoutTail !== undefined && r.stdoutTail !== null ? r.stdoutTail : r.stdout, 1200, 1200),
    stderrTail: clipText(r.stderrTail !== undefined && r.stderrTail !== null ? r.stderrTail : r.stderr, 800, 800),
    logs: r.logs || (r.stdoutLog ? { stdout: r.stdoutLog, stderr: r.stderrLog } : null),
    outdir: r.outdir || null,
    artifacts: artifacts,
    shots: normalizeShots(r.shots),
    inputFile: r.inputFile || null,
    pathWarnings: r.pathWarnings || [],
    stage: r.stage || r.stageName || null,
    stageAt: r.stageAt || null,
    idleMs: typeof r.idleMs === 'number' ? r.idleMs : null,
    lines: typeof r.lines === 'number' ? r.lines : null,
    logBytes: typeof r.logBytes === 'number' ? r.logBytes : null,
    engine: r.engine || null,
    // v0.9.4：这里原来是裸的 r.gpu.fell_back_to_cpu —— 后端没给这个字段时值就是 undefined，
    // 又一个「宿主门拒收 undefined 值」的实例（回归测试 tests/lossless_guard.mjs 抓到的第二处）。
    gpu: r.gpu
      ? {
          ok: !!r.gpu.ok,
          fell_back_to_cpu: !!r.gpu.fell_back_to_cpu,
          device: (r.gpu.after && r.gpu.after.device_type) || r.gpu.configured || null
        }
      : null,
    scriptFile: r.scriptFile || null,
    expect: r.expect || null,
    notes: ctx.notes || [],
    hint: ctx.hint || null
  }
}
/** 人读摘要（block 1）—— 沿用 v0.9.2 的叙述口径，读的是 v0.9.3 的结构化字段 */
export function headlessHumanText(res: any, ctx: any): string {
  const r: any = res || {}
  const NLx = String.fromCharCode(10)
  const parts: string[] = []
  const okFlag =
    typeof r.ok === 'boolean'
      ? r.ok
      : r.status === 'done' || r.status === 'finished'
        ? true
        : r.status === 'failed' || r.status === 'killed' || r.status === 'timeout' || r.status === 'stale'
          ? false
          : null
  const st = r.status || (okFlag ? 'finished' : 'unknown')
  parts.push(
    (okFlag === true ? 'HEADLESS ok' : okFlag === false ? 'HEADLESS 失败' : 'HEADLESS 进行中') +
      ' · status=' +
      String(st) +
      (r.exitCode === undefined || r.exitCode === null ? '' : ' · exit=' + String(r.exitCode)) +
      (r.timedOut ? ' · 超时被杀' : '') +
      (typeof r.ms === 'number' ? ' · ' + String(r.ms) + 'ms' : '') +
      (r.reason ? ' · ' + String(r.reason) : '')
  )
  if (ctx && ctx.mode === 'job') parts.push('模式：作业层（jobId=' + String(ctx.jobId || '') + '，等待窗口内已完成）')
  if (r.failure_hint) parts.push('提示：' + String(r.failure_hint))
  if (r.session) parts.push('会话：' + String(r.session))
  if (r.script) parts.push('脚本：' + String(r.script))
  if (r.scriptFile)
    parts.push('脚本文件：' + String(r.scriptFile.resolved || r.scriptFile.given) + '（' + String(r.scriptFile.bytes) + ' 字节）')
  if (r.inputFile && r.inputFile.md5)
    parts.push(
      '输入文件：' +
        String(r.inputFile.path) +
        ' · ' +
        String(r.inputFile.size) +
        ' 字节 · mtime=' +
        String(r.inputFile.mtime || r.inputFile.mtimeMs) +
        ' · md5=' +
        String(r.inputFile.md5) +
        (r.inputFile.md5Partial ? '（部分哈希）' : '')
    )
  const engName = r.engine || (r.gpu && r.gpu.after && r.gpu.after.engine) || null
  if (engName) parts.push('引擎：' + String(engName) + (r.engineMode ? '（mode=' + String(r.engineMode) + '）' : ''))
  if (r.gpu) {
    const g = r.gpu
    parts.push(
      'GPU：' +
        (g.fell_back_to_cpu
          ? '⚠️ 回落 CPU（' + String(g.error || (g.before && g.before.device_type) || '') + '）'
          : '✅ ' +
            String((g.after && g.after.device_type) || g.configured || '') +
            ' · ' +
            String(((g.after && g.after.gpu_enabled) || []).join(','))) +
        (g.configured ? ' · 本次已自动配置 ' + String(g.configured) : '')
    )
  }
  if (r.stage || r.stageName) parts.push('最后阶段（stage）：' + safeStringify(r.stage || r.stageName))
  if (r.lastException) parts.push('最后异常：' + String(r.lastException))
  if (r.logs) parts.push('日志：' + String(r.logs.stdout || '') + ' · ' + String(r.logs.stderr || ''))
  const rj = r.resultJson !== undefined ? r.resultJson : r.result
  if (rj !== null && rj !== undefined) {
    const s = safeStringify(rj) || ''
    parts.push(
      'result: ' + (s.length > 6000 ? s.slice(0, 6000) + NLx + '…[result 共 ' + String(s.length) + ' 字符；全文见 resultPath]' : s)
    )
  }
  if (r.resultPath)
    parts.push(
      'result 已落盘：' +
        String(r.resultPath) +
        (r.resultPathWsl ? '（WSL：' + String(r.resultPathWsl) + '）' : '') +
        ' · ' +
        String(r.resultBytes || 0) +
        ' 字节，全量 JSON'
    )
  if (r.resultParseError)
    parts.push(
      '⚠️ 末行 HEADLESS 不是合法 JSON（**原始输出已保留**，见下面的 stdout）· ' +
        String(r.resultParseError.message || '') +
        NLx +
        '  该行：' +
        String(r.resultParseError.line || '').slice(0, 300)
    )
  if (Array.isArray(r.shots && r.shots.rows) ? r.shots.rows.length : false) {
    parts.push(
      'shots（' +
        String(r.shots.rows.length) +
        ' 张，覆盖度=非底色像素占比）：' +
        NLx +
        r.shots.rows
          .map(
            (x: any) =>
              '  · ' +
              String(x.name) +
              ' · ' +
              String(x.ms) +
              'ms · ' +
              String(x.bytes) +
              'B · md5=' +
              String(x.md5) +
              (typeof x.coverage_estimate === 'number' ? ' · coverage=' + (x.coverage_estimate * 100).toFixed(2) + '%' : '') +
              (x.warning ? '  ⚠️ ' + String((x.warning && x.warning.hint) || x.warning.code) : '')
          )
          .join(NLx)
    )
    if (r.shots.jsonl) parts.push('shots jsonl：' + String(r.shots.jsonl))
  } else if (r.shots && r.shots.error) parts.push('⚠️ shots 失败：' + String(r.shots.error))
  if (Array.isArray(r.pathWarnings) && r.pathWarnings.length) {
    parts.push(
      '⚠️ 路径体检（' +
        String(r.pathWarnings.length) +
        ' 条）：' +
        NLx +
        r.pathWarnings
          .map((w: any) => '  · ' + String(w.code) + ' · ' + String(w.evidence || w.value || '') + NLx + '    ' + String(w.hint || ''))
          .join(NLx)
    )
  }
  if (Array.isArray(r.warnings) && r.warnings.length) {
    parts.push(
      '⚠️ warnings（' +
        String(r.warnings.length) +
        ' 条）：' +
        NLx +
        r.warnings
          .map(
            (w: any) =>
              '  · ' +
              (w && typeof w === 'object' ? String(w.code || '') + (w.hint ? ' — ' + String(w.hint) : JSON.stringify(w)) : String(w))
          )
          .join(NLx)
    )
  }
  if (r.stdoutTruncated) parts.push('（stdout 已截断：全量见 logs 路径 / resultPath）')
  const so = String(r.stdoutTail !== undefined && r.stdoutTail !== null ? r.stdoutTail : r.stdout || '')
  if (so)
    parts.push(
      (r.stdoutTruncated ? '--- stdout 尾（已截断）---' : '--- stdout ---') +
        NLx +
        (rj !== null && rj !== undefined ? clipText(so, 500, 800) : clipText(so, 1500, 2500))
    )
  const se = String(r.stderrTail !== undefined && r.stderrTail !== null ? r.stderrTail : r.stderr || '')
  if (se.trim()) parts.push('--- stderr（头+尾）---' + NLx + clipText(se, 500, 1500))
  if (r.expect) {
    const ex = r.expect,
      obs = ex.observed || {}
    parts.push(
      'expect 校验：' +
        (ex.ok ? '✅ 通过' : '❌ **未通过**') +
        (obs.delta
          ? ' · 新增对象 ' +
            String(obs.delta.objects) +
            ' / 网格 ' +
            String(obs.delta.meshes) +
            '（before ' +
            String((obs.before || {}).objects) +
            ' → after ' +
            String((obs.after || {}).objects) +
            '）'
          : '') +
        ((ex.warnings || []).length ? NLx + '  ' + ex.warnings.join(NLx + '  ') : '')
    )
  }
  const arts = Array.isArray(r.artifacts) ? r.artifacts : []
  if (arts.length)
    parts.push(
      '产物：' +
        arts
          .slice(0, 12)
          .map((a: any) => (typeof a === 'string' ? a : a.name + '(' + a.bytes + 'B' + (a.fresh ? ' 新' : '') + ')'))
          .join(' · ')
    )
  if (r.noiseFiltered) parts.push('（已过滤 ' + String(r.noiseFiltered) + ' 个噪音产物）')
  if (r.hint) parts.push('提示：' + String(r.hint))
  if (r.traceback) parts.push('--- traceback ---' + NLx + String(r.traceback).slice(0, 1200))
  if (r.id && String(r.id).indexOf('job-') === 0) parts.push('作业号：' + String(r.id) + '（runId=' + String(r.runId || '-') + '）')
  if (r.runId)
    parts.push('运行号：' + String(r.runId) + '（blender_rt_job(op="status"/"collect"/"wait", id=…) 可查；后端重启后也能从磁盘台账查到）')
  if (Array.isArray(ctx && ctx.notes)) for (const n of ctx.notes) parts.push('· ' + String(n))
  return parts.join(NLx)
}
/** 无头回执（sync / 作业层完成）：{text, envelope} —— 两个 text block 的来源 */
export function headlessReceipt(res: any, ctx: any): any {
  return { text: headlessHumanText(res, ctx), envelope: receiptEnvelope(res, ctx) }
}
/**
 * v0.9.3（D1）：**promoted 回执** —— 任务还在跑（或已结束但我们没等到），句柄已经有效。
 * 与 bash 通道的 promoted 语义对齐：kind="promoted" + jobId，note 里写清「怎么收结果」。
 */
export function promotedReceipt(job: any, jobId: string, why: string): any {
  const NLx = String.fromCharCode(10)
  const j: any = job || {}
  const st = j.status || 'running'
  const logs = j.logs || (j.stdoutLog ? { stdout: j.stdoutLog, stderr: j.stderrLog } : null)
  const env: any = {
    kind: 'promoted',
    mode: 'promoted',
    promoted: true,
    status: st,
    ok: null,
    jobId: jobId,
    runId: j.runId || jobId,
    id: j.id || jobId,
    ms: typeof j.ms === 'number' ? j.ms : null,
    stage: j.stage || j.stageName || null,
    idleMs: typeof j.idleMs === 'number' ? j.idleMs : null,
    lines: typeof j.lines === 'number' ? j.lines : null,
    logBytes: typeof j.logBytes === 'number' ? j.logBytes : null,
    resultJson: j.resultJson || null,
    resultTruncated: !!j.resultTruncated,
    resultPath: j.resultPath || null,
    resultPathWsl: j.resultPathWsl || null,
    stdoutTail: clipText(j.stdoutTail, 1200, 1200),
    stderrTail: clipText(j.stderrTail, 800, 800),
    logs: logs,
    outdir: j.outdir || null,
    artifacts: (j.artifacts || []).slice(0, 40),
    shots: normalizeShots(j.shots),
    inputFile: j.inputFile || null,
    pathWarnings: j.pathWarnings || [],
    hint: '用 blender_rt_job(op="wait", id="' + jobId + '") 阻塞等结果，或 op="collect" 看进展（stage/idleMs）；op="list" 看全部作业',
    notes: [why]
  }
  const parts: string[] = []
  parts.push('⏳ 已后台化（**不是失败**）· ' + why)
  parts.push(
    '句柄：' +
      jobId +
      ' · status=' +
      String(st) +
      (env.stage ? ' · stage=' + safeStringify(env.stage) : '') +
      (typeof env.idleMs === 'number' ? ' · 最近输出 ' + String(Math.round(env.idleMs / 1000)) + 's 前' : '')
  )
  if (logs) parts.push('日志（运行期就有增量：PYTHONUNBUFFERED=1）：' + String(logs.stdout || '') + ' · ' + String(logs.stderr || ''))
  if (env.stdoutTail) parts.push('--- stdout 尾 ---' + NLx + String(env.stdoutTail).slice(-1500))
  parts.push(
    '收结果：blender_rt_job(op="wait", id="' +
      jobId +
      '", timeout_ms=120000) —— 阻塞到完成或超时（不要连发 status 轮询，会撞重复调用检测）；' +
      '也可 op="collect"（随时看，未完成时也能看到 stage/日志尾），op="kill" 中止。' +
      ' ⚠ 这个 id 是**Blender 侧台账** id，不是 DSH 的作业 id —— 拿它去 DSH 的 job_output 会回 unknown job。'
  )
  parts.push('提醒：下次这类长活直接 as_job=true（预期 >100 s 都建议），就不会占用调用窗口。')
  return { text: parts.join(NLx), envelope: env }
}
/**
 * v0.9.3（D1）：客户端等待窗口到点 / 宿主取消时的兜底 —— 先问一句服务端状态：
 *   已结束 → 直接 collect 把结果拿回来（窗口与完成是赛跑，可能刚好赶上）；
 *   还在跑 → 回 promoted 回执（runId 是客户端生成的，句柄一定有效）。
 */
export async function promotedFromTimeout(port: number, runId: string, err: any): Promise<any> {
  const msg = String((err && err.message) || err)
  const job = await fetchJobStatus(port, runId)
  if (job && job.status && job.status !== 'running' && job.status !== 'unknown') {
    try {
      const cr: any = await backendJson(port, '/job', 15000, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ op: 'collect', id: runId, holder: HOLDER })
      })
      const cj = (cr && cr.job) || null
      if (cj && cj.status && cj.status !== 'running') {
        return headlessReceipt(cj, {
          mode: 'sync',
          jobId: null,
          runId: runId,
          notes: ['客户端窗口到点时任务其实已经跑完 → 已直接取回结果（' + msg + '）']
        })
      }
    } catch (e2: any) {
      /* 取不回来就走 promoted */
    }
  }
  return promotedReceipt(job, runId, '客户端等待窗口到点或宿主取消：' + msg)
}

/** 取某个 id（run-… / job-…）的状态快照（拿不到就返回 null，绝不抛） */
export async function fetchJobStatus(port: number, id: string): Promise<any> {
  try {
    const r: any = await backendJson(port, '/job', 8000, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ op: 'status', id: id, holder: HOLDER })
    })
    return (r && r.job) || null
  } catch (e: any) {
    return null
  }
}
