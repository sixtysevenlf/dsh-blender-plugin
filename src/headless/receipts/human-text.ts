/** headless 人读摘要（block 1）：按 v0.9.2 叙述口径逐段拼装（顺序与 v2.6.2 完全一致）。 */
import { clipText, safeStringify } from '../../ui/cards.js'
import { redactSecrets } from '../../tools/errors.js'
import { asRecord, isRecord, text } from '../../contracts/dynamic.js'

/** 回执上下文：envelope（构造信封）与本文件（人读叙述）共用的唯一声明。 */
export interface ReceiptContext {
  mode?: 'sync' | 'job' | 'promoted'
  runId?: string | null
  jobId?: string | null
  notes?: string[]
  hint?: string | null
}

const NLx = String.fromCharCode(10)

export function headlessHumanText(res: unknown, ctx: ReceiptContext): string {
  const r = asRecord(res)
  const parts: string[] = []
  const okFlag = okFlagOf(r)
  parts.push(statusLine(r, okFlag))
  if (ctx && ctx.mode === 'job')
    parts.push('模式：作业层（jobId=' + text(ctx.jobId) + '，等待窗口内已完成）')
  pushBasic(parts, r)
  pushEngine(parts, r)
  if (r.stage || r.stageName)
    parts.push('最后阶段（stage）：' + String(safeStringify(r.stage || r.stageName)))
  if (r.lastException) parts.push('最后异常：' + text(r.lastException))
  if (r.logs) {
    const logs = asRecord(r.logs)
    parts.push('日志：' + text(logs.stdout) + ' · ' + text(logs.stderr))
  }
  pushResult(parts, r)
  pushShots(parts, r)
  pushPathWarnings(parts, r)
  pushWarnings(parts, r)
  pushStdout(parts, r)
  pushStderr(parts, r)
  pushExpect(parts, r)
  pushArtifacts(parts, r)
  if (r.noiseFiltered) parts.push('（已过滤 ' + text(r.noiseFiltered) + ' 个噪音产物）')
  if (r.hint) parts.push('提示：' + text(r.hint))
  if (r.traceback) parts.push('--- traceback ---' + NLx + redactSecrets(r.traceback).slice(0, 1200))
  if (typeof r.id === 'string' && r.id.indexOf('job-') === 0)
    parts.push('作业号：' + r.id + '（runId=' + text(r.runId || '-') + '）')
  if (r.runId)
    parts.push(
      '运行号：' +
        text(r.runId) +
        '（blender_run action=status/collect/wait id=… 可查；Gateway 重启后也能从磁盘队列查到）',
    )
  const notes = ctx.notes
  if (Array.isArray(notes)) for (const n of notes) parts.push('· ' + text(n))
  return parts.join(NLx)
}

function okFlagOf(r: Record<string, unknown>): boolean | null {
  return typeof r.ok === 'boolean'
    ? r.ok
    : r.status === 'done' || r.status === 'finished'
      ? true
      : r.status === 'failed' ||
          r.status === 'killed' ||
          r.status === 'timeout' ||
          r.status === 'stale'
        ? false
        : null
}

function statusLine(r: Record<string, unknown>, okFlag: boolean | null): string {
  const st = r.status || (okFlag ? 'finished' : 'unknown')
  return (
    (okFlag === true ? 'HEADLESS ok' : okFlag === false ? 'HEADLESS 失败' : 'HEADLESS 进行中') +
    ' · status=' +
    text(st) +
    (r.exitCode === undefined || r.exitCode === null ? '' : ' · exit=' + text(r.exitCode)) +
    (r.timedOut ? ' · 超时被杀' : '') +
    (typeof r.ms === 'number' ? ' · ' + String(r.ms) + 'ms' : '') +
    (r.reason ? ' · ' + text(r.reason) : '')
  )
}

function pushBasic(parts: string[], r: Record<string, unknown>): void {
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
}

function pushEngine(parts: string[], r: Record<string, unknown>): void {
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
}

function pushResult(parts: string[], r: Record<string, unknown>): void {
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
}

function pushShots(parts: string[], r: Record<string, unknown>): void {
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
}

function pushPathWarnings(parts: string[], r: Record<string, unknown>): void {
  if (!Array.isArray(r.pathWarnings) || !r.pathWarnings.length) return
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

function pushWarnings(parts: string[], r: Record<string, unknown>): void {
  if (!Array.isArray(r.warnings) || !r.warnings.length) return
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
              ? text(warning.code) + (warning.hint ? ' — ' + text(warning.hint) : JSON.stringify(w))
              : text(w))
          )
        })
        .join(NLx),
  )
}

function pushStdout(parts: string[], r: Record<string, unknown>): void {
  if (r.stdoutTruncated) parts.push('（stdout 已截断：全量见 logs 路径 / resultPath）')
  const so = String(
    r.stdoutTail !== undefined && r.stdoutTail !== null ? r.stdoutTail : r.stdout || '',
  )
  if (!so) return
  const rj = r.resultJson !== undefined ? r.resultJson : r.result
  parts.push(
    (r.stdoutTruncated ? '--- stdout 尾（已截断）---' : '--- stdout ---') +
      NLx +
      (rj !== null && rj !== undefined
        ? clipText(redactSecrets(so), 500, 800)
        : clipText(redactSecrets(so), 1500, 2500)),
  )
}

function pushStderr(parts: string[], r: Record<string, unknown>): void {
  const se = String(
    r.stderrTail !== undefined && r.stderrTail !== null ? r.stderrTail : r.stderr || '',
  )
  if (se.trim()) parts.push('--- stderr（头+尾）---' + NLx + clipText(redactSecrets(se), 500, 1500))
}

function pushExpect(parts: string[], r: Record<string, unknown>): void {
  if (!r.expect) return
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

function pushArtifacts(parts: string[], r: Record<string, unknown>): void {
  const arts = Array.isArray(r.artifacts) ? r.artifacts : []
  if (!arts.length) return
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
}
