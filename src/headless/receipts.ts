import { HOLDER } from '../constants.js'
import { backendJson, backendPost, leasedText } from '../host/http.js'
import { clipText, safeStringify } from '../ui/cards.js'
import { redactSecrets, toolError } from '../tools/errors.js'
import { asRecord, isRecord, text } from '../contracts/dynamic.js'

export interface ReceiptContext {
  mode?: 'sync' | 'job' | 'promoted'
  runId?: string | null
  jobId?: string | null
  notes?: string[]
  hint?: string | null
}

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
export async function startJobThenMaybeWait(
  port: number,
  body: unknown,
  waitS: number | null,
): Promise<Record<string, unknown>> {
  const jr = await backendPost(
    port,
    '/headless',
    Object.assign({}, asRecord(body), { asJob: true }),
    60000,
  )
  const lt = leasedText(jr)
  if (lt) return { text: lt }
  if (jr.error)
    return toolError(
      'backend_error',
      'HEADLESS 转作业层失败 · ' + text(jr.error || jr.raw || 'unknown'),
      '检查后端 /headless 路由；或改用 blender_rt_job op=list 查看是否已建作业',
    ) as unknown as Record<string, unknown>
  // 后端 /headless 的回包是 {ok, ms, result:<引擎回执>} —— jobId 在 result 里
  const inner = isRecord(jr.result) ? jr.result : jr
  const job = asRecord(inner.job)
  const jobId = String(inner.jobId || (job.id ?? '') || '')
  const budget =
    waitS && waitS > 0 ? Math.min(Math.round(waitS * 1000), HEADLESS_WAIT_MS + 2000) : 0
  if (!budget) return { jobId: jobId, job: inner.job || null, waited: false }
  const wr = await backendPost(
    port,
    '/job',
    { op: 'wait', id: jobId, timeoutMs: budget },
    budget + 30000,
  )
  const lt2 = leasedText(wr)
  if (lt2) return { text: lt2 }
  const waitedJob = asRecord(wr.job)
  if (typeof waitedJob.status === 'string' && waitedJob.status !== 'running')
    return { jobId: jobId, job: wr.job, waited: true, finished: true }
  return { jobId: jobId, job: wr.job || inner.job || null, waited: true, finished: false }
}
/** shots 回执归一化：sync 路径给 {rows:[…]}，job 快照给 qc_render 原样的 {views:[…]} */
export function normalizeShots(s: unknown): Record<string, unknown> | null {
  if (!isRecord(s)) return null
  let rows: unknown[] | null = Array.isArray(s.rows) ? s.rows : null
  if (!rows && Array.isArray(s.views)) {
    rows = s.views.map((e) => {
      const rowSource = asRecord(e)
      const frame = asRecord(rowSource.frame)
      const cov = typeof frame.coverage === 'number' ? frame.coverage : null
      const row: Record<string, unknown> = {
        name: rowSource.view || null,
        path: rowSource.path || null,
        ms: rowSource.ms || null,
        bytes: rowSource.bytes || null,
        md5: rowSource.hash || null,
        res: rowSource.res || null,
        device: rowSource.device || null,
        coverage_estimate: cov,
      }
      if (cov !== null && cov < 0.05)
        row.warning = { code: 'subject_too_small', coverage_estimate: cov }
      return row
    })
  }
  const source = rows || []
  const covs = source
    .map((r) => asRecord(r).coverage_estimate)
    .filter((x): x is number => typeof x === 'number')
  return {
    ok: !!s.ok,
    count: source.length,
    rows: source,
    coverage_min:
      typeof s.coverage_min === 'number' ? s.coverage_min : covs.length ? Math.min(...covs) : null,
    outdir: s.outdir || null,
    jsonl: s.jsonl || null,
    total_ms: s.total_ms || null,
    engine: s.engine || null,
    samples: s.samples || null,
    device_line: s.device_line || null,
    unknown_views: s.unknown_views || null,
    error: s.error || null,
  }
}
/** 结构化信封（block 0）：稳定字段 + 有界大小（大结果只给 resultPath） */
export function receiptEnvelope(res: unknown, ctx: ReceiptContext): Record<string, unknown> {
  const r = asRecord(res)
  const rj = r.resultJson !== undefined ? r.resultJson : r.result
  const rjStr = rj === null || rj === undefined ? null : safeStringify(rj)
  const ENV_LIMIT = 8000
  const big = !!(rjStr && rjStr.length > ENV_LIMIT)
  const artifacts = (Array.isArray(r.artifacts) ? r.artifacts : [])
    .slice(0, 40)
    .map((a) => (typeof a === 'string' ? { name: a } : a))
  const gpu = asRecord(r.gpu)
  const after = asRecord(gpu.after)
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
          : r.status === 'failed' ||
              r.status === 'killed' ||
              r.status === 'timeout' ||
              r.status === 'stale'
            ? false
            : null,
    runId: r.runId || ctx.runId || null,
    jobId: ctx.jobId || (typeof r.id === 'string' && r.id.indexOf('job-') === 0 ? r.id : null),
    id: r.id || null,
    // v0.9.4（P0 返回通道）：这里原来是 ? true : undefined —— 宿主 dsh-util-values 的
    // lossless-JSON 门**拒收值为 undefined 的自有可枚举键**（JSON.stringify 会把它丢掉，
    // 所以肉眼看不出问题），于是 blender_rt_headless / rt_job 的每一条回执都被判
    // value is not lossless JSON，无头批处理这条第一路径整条被打死。改成「有才给键」。
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
    stdoutTail: clipText(
      r.stdoutTail !== undefined && r.stdoutTail !== null ? r.stdoutTail : r.stdout,
      1200,
      1200,
    ),
    stderrTail: clipText(
      r.stderrTail !== undefined && r.stderrTail !== null ? r.stderrTail : r.stderr,
      800,
      800,
    ),
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
          ok: !!gpu.ok,
          fell_back_to_cpu: !!gpu.fell_back_to_cpu,
          device: after.device_type || gpu.configured || null,
        }
      : null,
    scriptFile: r.scriptFile || null,
    expect: r.expect || null,
    notes: ctx.notes || [],
    hint: ctx.hint || null,
  }
}
/** 人读摘要（block 1）—— 沿用 v0.9.2 的叙述口径，读的是 v0.9.3 的结构化字段 */
export function headlessHumanText(res: unknown, ctx: ReceiptContext): string {
  const r = asRecord(res)
  const NLx = String.fromCharCode(10)
  const parts: string[] = []
  const okFlag =
    typeof r.ok === 'boolean'
      ? r.ok
      : r.status === 'done' || r.status === 'finished'
        ? true
        : r.status === 'failed' ||
            r.status === 'killed' ||
            r.status === 'timeout' ||
            r.status === 'stale'
          ? false
          : null
  const st = r.status || (okFlag ? 'finished' : 'unknown')
  parts.push(
    (okFlag === true ? 'HEADLESS ok' : okFlag === false ? 'HEADLESS 失败' : 'HEADLESS 进行中') +
      ' · status=' +
      text(st) +
      (r.exitCode === undefined || r.exitCode === null ? '' : ' · exit=' + text(r.exitCode)) +
      (r.timedOut ? ' · 超时被杀' : '') +
      (typeof r.ms === 'number' ? ' · ' + String(r.ms) + 'ms' : '') +
      (r.reason ? ' · ' + text(r.reason) : ''),
  )
  if (ctx && ctx.mode === 'job')
    parts.push('模式：作业层（jobId=' + text(ctx.jobId) + '，等待窗口内已完成）')
  if (r.failure_hint) parts.push('提示：' + text(r.failure_hint))
  if (r.session) parts.push('会话：' + text(r.session))
  if (r.script) parts.push('脚本：' + text(r.script))
  if (r.scriptFile) {
    const sf = asRecord(r.scriptFile)
    parts.push('脚本文件：' + text(sf.resolved || sf.given) + '（' + text(sf.bytes) + ' 字节）')
  }
  const inputFile = asRecord(r.inputFile)
  if (inputFile.md5)
    parts.push(
      '输入文件：' +
        text(inputFile.path) +
        ' · ' +
        text(inputFile.size) +
        ' 字节 · mtime=' +
        text(inputFile.mtime || inputFile.mtimeMs) +
        ' · md5=' +
        text(inputFile.md5) +
        (inputFile.md5Partial ? '（部分哈希）' : ''),
    )
  const gpu = asRecord(r.gpu)
  const gpuAfter = asRecord(gpu.after)
  const engName = r.engine || (gpu.after && gpuAfter.engine) || null
  if (engName)
    parts.push(
      '引擎：' + text(engName) + (r.engineMode ? '（mode=' + text(r.engineMode) + '）' : ''),
    )
  if (r.gpu) {
    const g = gpu
    parts.push(
      'GPU：' +
        (g.fell_back_to_cpu
          ? '⚠️ 回落 CPU（' +
            text(g.error || (g.before && asRecord(g.before).device_type) || '') +
            '）'
          : '✅ ' +
            text(gpuAfter.device_type || g.configured || '') +
            ' · ' +
            String(((gpuAfter.gpu_enabled as unknown[]) || []).join(','))) +
        (g.configured ? ' · 本次已自动配置 ' + text(g.configured) : ''),
    )
  }
  if (r.stage || r.stageName)
    parts.push('最后阶段（stage）：' + String(safeStringify(r.stage || r.stageName)))
  if (r.lastException) parts.push('最后异常：' + text(r.lastException))
  if (r.logs) {
    const logs = asRecord(r.logs)
    parts.push('日志：' + text(logs.stdout) + ' · ' + text(logs.stderr))
  }
  const rj = r.resultJson !== undefined ? r.resultJson : r.result
  if (rj !== null && rj !== undefined) {
    const s = safeStringify(rj) || ''
    parts.push(
      'result: ' +
        (s.length > 6000
          ? s.slice(0, 6000) + NLx + '…[result 共 ' + String(s.length) + ' 字符；全文见 resultPath]'
          : s),
    )
  }
  if (r.resultPath)
    parts.push(
      'result 已落盘：' +
        text(r.resultPath) +
        (r.resultPathWsl ? '（WSL：' + text(r.resultPathWsl) + '）' : '') +
        ' · ' +
        text(r.resultBytes || 0) +
        ' 字节，全量 JSON',
    )
  if (r.resultParseError) {
    const pe = asRecord(r.resultParseError)
    parts.push(
      '⚠️ 末行 HEADLESS 不是合法 JSON（**原始输出已保留**，见下面的 stdout）· ' +
        text(pe.message) +
        NLx +
        '  该行：' +
        text(pe.line).slice(0, 300),
    )
  }
  const shots = asRecord(r.shots)
  const shotRows = Array.isArray(shots.rows) ? shots.rows : []
  if (shotRows.length) {
    parts.push(
      'shots（' +
        String(shotRows.length) +
        ' 张，覆盖度=非底色像素占比）：' +
        NLx +
        shotRows
          .map((x) => {
            const row = asRecord(x)
            const warning = asRecord(row.warning)
            return (
              '  · ' +
              text(row.name) +
              ' · ' +
              text(row.ms) +
              'ms · ' +
              text(row.bytes) +
              'B · md5=' +
              text(row.md5) +
              (typeof row.coverage_estimate === 'number'
                ? ' · coverage=' + (row.coverage_estimate * 100).toFixed(2) + '%'
                : '') +
              (row.warning ? '  ⚠️ ' + text(warning.hint || warning.code) : '')
            )
          })
          .join(NLx),
    )
    if (shots.jsonl) parts.push('shots jsonl：' + text(shots.jsonl))
  } else if (shots.error) parts.push('⚠️ shots 失败：' + text(shots.error))
  if (Array.isArray(r.pathWarnings) && r.pathWarnings.length) {
    parts.push(
      '⚠️ 路径体检（' +
        String(r.pathWarnings.length) +
        ' 条）：' +
        NLx +
        r.pathWarnings
          .map((w) => {
            const warning = asRecord(w)
            return (
              '  · ' +
              text(warning.code) +
              ' · ' +
              text(warning.evidence || warning.value || '') +
              NLx +
              '    ' +
              text(warning.hint)
            )
          })
          .join(NLx),
    )
  }
  if (Array.isArray(r.warnings) && r.warnings.length) {
    parts.push(
      '⚠️ warnings（' +
        String(r.warnings.length) +
        ' 条）：' +
        NLx +
        r.warnings
          .map((w) => {
            const warning = asRecord(w)
            return (
              '  · ' +
              (isRecord(w)
                ? text(warning.code) +
                  (warning.hint ? ' — ' + text(warning.hint) : JSON.stringify(w))
                : text(w))
            )
          })
          .join(NLx),
    )
  }
  if (r.stdoutTruncated) parts.push('（stdout 已截断：全量见 logs 路径 / resultPath）')
  const so = String(
    r.stdoutTail !== undefined && r.stdoutTail !== null ? r.stdoutTail : r.stdout || '',
  )
  if (so)
    parts.push(
      (r.stdoutTruncated ? '--- stdout 尾（已截断）---' : '--- stdout ---') +
        NLx +
        (rj !== null && rj !== undefined
          ? clipText(redactSecrets(so), 500, 800)
          : clipText(redactSecrets(so), 1500, 2500)),
    )
  const se = String(
    r.stderrTail !== undefined && r.stderrTail !== null ? r.stderrTail : r.stderr || '',
  )
  if (se.trim()) parts.push('--- stderr（头+尾）---' + NLx + clipText(redactSecrets(se), 500, 1500))
  if (r.expect) {
    const ex = asRecord(r.expect)
    const observed = asRecord(ex.observed)
    const deltaRaw = observed.delta
    const delta = asRecord(deltaRaw)
    const before = asRecord(observed.before)
    const after = asRecord(observed.after)
    parts.push(
      'expect 校验：' +
        (ex.ok ? '✅ 通过' : '❌ **未通过**') +
        (deltaRaw
          ? ' · 新增对象 ' +
            text(delta.objects) +
            ' / 网格 ' +
            text(delta.meshes) +
            '（before ' +
            text(before.objects) +
            ' → after ' +
            text(after.objects) +
            '）'
          : '') +
        (Array.isArray(ex.warnings) && ex.warnings.length
          ? NLx + '  ' + ex.warnings.join(NLx + '  ')
          : ''),
    )
  }
  const arts = Array.isArray(r.artifacts) ? r.artifacts : []
  if (arts.length)
    parts.push(
      '产物：' +
        arts
          .slice(0, 12)
          .map((a) => {
            if (typeof a === 'string') return a
            const art = asRecord(a)
            return text(art.name) + '(' + text(art.bytes) + 'B' + (art.fresh ? ' 新' : '') + ')'
          })
          .join(' · '),
    )
  if (r.noiseFiltered) parts.push('（已过滤 ' + text(r.noiseFiltered) + ' 个噪音产物）')
  if (r.hint) parts.push('提示：' + text(r.hint))
  if (r.traceback) parts.push('--- traceback ---' + NLx + redactSecrets(r.traceback).slice(0, 1200))
  if (typeof r.id === 'string' && r.id.indexOf('job-') === 0)
    parts.push('作业号：' + r.id + '（runId=' + text(r.runId || '-') + '）')
  if (r.runId)
    parts.push(
      '运行号：' +
        text(r.runId) +
        '（blender_rt_job(op="status"/"collect"/"wait", id=…) 可查；后端重启后也能从磁盘台账查到）',
    )
  const notes = ctx.notes
  if (Array.isArray(notes)) for (const n of notes) parts.push('· ' + text(n))
  return parts.join(NLx)
}
export interface ToolReceipt {
  text: string
  envelope: Record<string, unknown>
  images?: unknown[]
  [key: string]: unknown
}
/** 无头回执（sync / 作业层完成）：{text, envelope} —— 两个 text block 的来源 */
export function headlessReceipt(res: unknown, ctx: ReceiptContext): ToolReceipt {
  return { text: headlessHumanText(res, ctx), envelope: receiptEnvelope(res, ctx) }
}
/**
 * v0.9.3（D1）：**promoted 回执** —— 任务还在跑（或已结束但我们没等到），句柄已经有效。
 * 与 bash 通道的 promoted 语义对齐：kind="promoted" + jobId，note 里写清「怎么收结果」。
 */
export function promotedReceipt(job: unknown, jobId: string, why: string): ToolReceipt {
  const NLx = String.fromCharCode(10)
  const j = asRecord(job)
  const st = j.status || 'running'
  const logs = j.logs || (j.stdoutLog ? { stdout: j.stdoutLog, stderr: j.stderrLog } : null)
  const env: Record<string, unknown> = {
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
    artifacts: (Array.isArray(j.artifacts) ? j.artifacts : []).slice(0, 40),
    shots: normalizeShots(j.shots),
    inputFile: j.inputFile || null,
    pathWarnings: j.pathWarnings || [],
    hint:
      '用 blender_rt_job(op="wait", id="' +
      jobId +
      '") 阻塞等结果，或 op="collect" 看进展（stage/idleMs）；op="list" 看全部作业',
    notes: [why],
  }
  const parts: string[] = []
  parts.push('⏳ 已后台化（**不是失败**）· ' + why)
  parts.push(
    '句柄：' +
      jobId +
      ' · status=' +
      text(st) +
      (env.stage ? ' · stage=' + String(safeStringify(env.stage)) : '') +
      (typeof env.idleMs === 'number'
        ? ' · 最近输出 ' + String(Math.round(env.idleMs / 1000)) + 's 前'
        : ''),
  )
  if (logs) {
    const l = asRecord(logs)
    parts.push(
      '日志（运行期就有增量：PYTHONUNBUFFERED=1）：' + text(l.stdout) + ' · ' + text(l.stderr),
    )
  }
  if (env.stdoutTail) parts.push('--- stdout 尾 ---' + NLx + text(env.stdoutTail).slice(-1500))
  parts.push(
    '收结果：blender_rt_job(op="wait", id="' +
      jobId +
      '", timeout_ms=120000) —— 阻塞到完成或超时（不要连发 status 轮询，会撞重复调用检测）；' +
      '也可 op="collect"（随时看，未完成时也能看到 stage/日志尾），op="kill" 中止。' +
      ' ⚠ 这个 id 是**Blender 侧台账** id，不是 DSH 的作业 id —— 拿它去 DSH 的 job_output 会回 unknown job。',
  )
  parts.push('提醒：下次这类长活直接 as_job=true（预期 >100 s 都建议），就不会占用调用窗口。')
  return { text: parts.join(NLx), envelope: env }
}
/**
 * v0.9.3（D1）：客户端等待窗口到点 / 宿主取消时的兜底 —— 先问一句服务端状态：
 *   已结束 → 直接 collect 把结果拿回来（窗口与完成是赛跑，可能刚好赶上）；
 *   还在跑 → 回 promoted 回执（runId 是客户端生成的，句柄一定有效）。
 */
export async function promotedFromTimeout(
  port: number,
  runId: string,
  err: unknown,
): Promise<Record<string, unknown>> {
  const msg = text(isRecord(err) ? err.message : err)
  const job = await fetchJobStatus(port, runId)
  const jobRecord = asRecord(job)
  if (
    typeof jobRecord.status === 'string' &&
    jobRecord.status !== 'running' &&
    jobRecord.status !== 'unknown'
  ) {
    try {
      const cr = await backendJson(port, '/job', 15000, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ op: 'collect', id: runId, holder: HOLDER }),
      })
      const cj = asRecord(asRecord(cr).job)
      if (typeof cj.status === 'string' && cj.status !== 'running') {
        return headlessReceipt(cj, {
          mode: 'sync',
          jobId: null,
          runId: runId,
          notes: ['客户端窗口到点时任务其实已经跑完 → 已直接取回结果（' + msg + '）'],
        })
      }
    } catch {
      /* 取不回来就走 promoted */
    }
  }
  return promotedReceipt(job, runId, '客户端等待窗口到点或宿主取消：' + msg)
}

/** 取某个 id（run-… / job-…）的状态快照（拿不到就返回 null，绝不抛） */
export async function fetchJobStatus(port: number, id: string): Promise<unknown> {
  try {
    const r = await backendJson(port, '/job', 8000, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ op: 'status', id: id, holder: HOLDER }),
    })
    return r.job || null
  } catch {
    return null
  }
}
