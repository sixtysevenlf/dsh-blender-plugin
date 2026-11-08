import fs from 'node:fs'
import path from 'node:path'
import { winToWsl } from '../../config.js'
import { errorMessage } from '../../core/json.js'
import { noInputFileWarning } from '../../file_guard.js'
import { workdirStagingWarning } from '../../proc_env.js'
import type { StagingInfo } from '../../staging.js'
import type { HeadlessDeps, HeadlessOpts } from './types.js'

/** GPU 回执里的 engine 字段（Python 侧 JSON，形状动态） */
export function gpuAfterEngine(gpu: Record<string, unknown> | null): unknown {
  if (!gpu) return null
  const after = gpu.after
  if (!after || typeof after !== 'object') return null
  return (after as Record<string, unknown>).engine || null
}

/** 动态边界解析：从 expect 观测里取指定层对象的字段（JSON 来自 Python 侧，形状由脚本决定） */
function objField(rec: Record<string, unknown> | null, group: string, field: string): unknown {
  if (!rec) return null
  const g = rec[group]
  if (!g || typeof g !== 'object') return null
  const v = (g as Record<string, unknown>)[field]
  return v === undefined ? null : v
}

function deltaObjects(rec: Record<string, unknown> | null): unknown {
  return objField(rec, 'delta', 'objects') || 0
}

function taggedLines(stdout: string, tag: string): string[] {
  return stdout.split('\n').filter((l) => l.indexOf(tag) === 0)
}

function lastTaggedPayload(stdout: string, tag: string): string | null {
  const lines = taggedLines(stdout, tag)
  return lines.length ? lines[lines.length - 1].slice(tag.length).trim() : null
}

export interface ParsedHeadlessStdout {
  parsed: unknown
  parseError: { message: string; line: string } | null
  shotsRes: Record<string, unknown> | null
  gpu: Record<string, unknown> | null
}

export function parseHeadlessStdout(stdout: string): ParsedHeadlessStdout {
  // ---- 结果契约：HEADLESS <单行 JSON>（取最后一条）
  // v0.9.3：末行不是合法 JSON 时**不再覆盖 raw**（旧版回 {_parse_error, raw} 顶掉真实输出，
  // 调用方只能看到 "_parse_error: Unexpected token 'O'"）。现在 parsed 保持 null，解析失败单独记在
  // resultParseError 里，stdout 原文（stdoutTail / logs）原样保留。
  let parsed: unknown = null
  let parseError: { message: string; line: string } | null = null
  const head = lastTaggedPayload(stdout, 'HEADLESS ')
  if (head !== null) {
    try {
      parsed = JSON.parse(head)
    } catch (e) {
      parseError = { message: errorMessage(e).slice(0, 200), line: head.slice(0, 400) }
    }
  }
  // ---- v0.9.3：shots 回执（DSH_SHOTS <单行 JSON>）
  let shotsRes: Record<string, unknown> | null = null
  const shot = lastTaggedPayload(stdout, 'DSH_SHOTS ')
  if (shot !== null) {
    try {
      shotsRes = JSON.parse(shot) as Record<string, unknown>
    } catch (e) {
      shotsRes = { ok: false, parse_error: errorMessage(e).slice(0, 200) }
    }
  }
  // ---- GPU 回执（前导打印 DSH_GPU）
  let gpu: Record<string, unknown> | null = null
  const g = lastTaggedPayload(stdout, 'DSH_GPU ')
  if (g !== null) {
    try {
      gpu = JSON.parse(g) as Record<string, unknown>
    } catch (e) {
      gpu = { _parse_error: errorMessage(e) }
    }
  }
  return { parsed, parseError, shotsRes, gpu }
}

export interface ExpectEval {
  spec: Record<string, unknown>
  observed: Record<string, unknown> | null
  ok: boolean
  warnings: string[]
}

export function evaluateExpect(
  exp: Record<string, unknown> | null,
  stdout: string,
): ExpectEval | null {
  if (!exp) return null
  let observed: Record<string, unknown> | null = null
  const payload = lastTaggedPayload(stdout, 'DSH_EXPECT ')
  if (payload !== null) {
    try {
      observed = JSON.parse(payload) as Record<string, unknown>
    } catch (e) {
      observed = null
    }
  }
  const expectEval: ExpectEval = { spec: exp, observed: observed, ok: true, warnings: [] }
  const minNew = Number(exp.minNewObjects !== undefined ? exp.minNewObjects : exp.min_new_objects)
  if (!observed || observed.error) {
    expectEval.ok = false
    expectEval.warnings.push(
      'expect 观测失败：' + ((observed && observed.error) || '没有 DSH_EXPECT 行'),
    )
  } else if (Number.isFinite(minNew) && Number(deltaObjects(observed)) < minNew) {
    expectEval.ok = false
    expectEval.warnings.push(
      '对象数未达预期：before=' +
        String(objField(observed, 'before', 'objects')) +
        ' → after=' +
        String(objField(observed, 'after', 'objects')) +
        '（要求新增 ≥' +
        minNew +
        '）—— 典型的 build 空跑',
    )
  }
  return expectEval
}

export { collectArtifacts } from './artifacts.js'
export type { ArtifactCollection, ArtifactInfo } from './artifacts.js'
export { classifyFailure, extractFailureContext, failureHint } from './failure.js'
export type { FailureClass, FailureContext } from './failure.js'

export interface ShotsSummary {
  ok: boolean
  count: number
  rows: Array<Record<string, unknown>>
  coverage_min: number | null
  outdir: unknown
  jsonl: unknown
  total_ms: unknown
  render_ms: unknown
  engine: unknown
  samples: unknown
  device_line: unknown
  unknown_views: unknown
  scope_warnings: unknown
  error: unknown
}

export function summarizeShots(shotsRes: Record<string, unknown>): ShotsSummary {
  const rowsIn: unknown[] = Array.isArray(shotsRes.views) ? (shotsRes.views as unknown[]) : []
  const rows: Array<Record<string, unknown>> = rowsIn.map((x) => {
    const e: Record<string, unknown> =
      x && typeof x === 'object' ? (x as Record<string, unknown>) : {}
    const frame =
      e.frame && typeof e.frame === 'object' ? (e.frame as Record<string, unknown>) : null
    const cov = frame && typeof frame.coverage === 'number' ? frame.coverage : null
    const row: Record<string, unknown> = {
      name: e.view || null,
      path: e.path || null,
      ms: e.ms || null,
      bytes: e.bytes || null,
      md5: e.hash || null,
      res: e.res || null,
      device: e.device || null,
      coverage_estimate: cov,
    }
    if (cov !== null && cov < 0.05) {
      row.warning = {
        code: 'subject_too_small',
        coverage_estimate: cov,
        hint:
          '主体只占画面 ' +
          (cov * 100).toFixed(2) +
          '% —— 这个机位看不清细节：from 拉近 / 换 look_at / ' +
          '给这一项加 targets=[名字] 或 focus="x,y,z,r" 缩小取景范围（blender_see 同类判据）',
      }
    }
    return row
  })
  const covs = rows
    .map((r) => r.coverage_estimate)
    .filter((x): x is number => typeof x === 'number')
  return {
    ok: !!shotsRes.ok,
    count: rows.length,
    rows: rows,
    coverage_min: covs.length ? Math.min.apply(null, covs) : null,
    outdir: shotsRes.outdir || null,
    jsonl: shotsRes.jsonl || null,
    total_ms: shotsRes.total_ms || null,
    render_ms: shotsRes.render_ms || null,
    engine: shotsRes.engine || null,
    samples: shotsRes.samples || null,
    device_line: shotsRes.device_line || null,
    unknown_views: shotsRes.unknown_views || null,
    scope_warnings: shotsRes.scope_warnings || null,
    error:
      shotsRes.error ||
      (shotsRes.ok === false ? shotsRes.hint || 'shots 失败（见 error/traceback）' : null),
  }
}

/** Shots-only runs have no HEADLESS result; synthesize the same shape as a full result. */
export function applyShotsResult(parsed: unknown, shots: ShotsSummary | null): unknown {
  if (!shots || parsed !== null) return parsed
  return {
    ok: !!shots.ok,
    shots: shots.rows,
    outdir: shots.outdir,
    jsonl: shots.jsonl,
    count: shots.count,
    coverage_min: shots.coverage_min,
    error: shots.error,
  }
}

/** Merge path audit, scene hygiene and the no-input warning into the run record order. */
export function finishRunWarnings(
  pathWarnings: unknown[],
  hygieneWarnings: unknown[],
  fileOpt: unknown,
  fileLooksPython: boolean,
): unknown[] {
  const noFile = !fileOpt || fileLooksPython ? [noInputFileWarning(fileLooksPython)] : []
  return (pathWarnings || []).concat(hygieneWarnings).concat(noFile)
}

/** Append the workDir-not-shared warning once, preserving pathAudit's original ordering. */
export function withWorkdirWarning(
  pathWarnings: unknown[],
  staging: StagingInfo | null | undefined,
): unknown[] {
  if (
    !staging ||
    !staging.substituted ||
    pathWarnings.some(
      (w) =>
        typeof w === 'object' &&
        w !== null &&
        (w as { code?: unknown }).code === 'WORKDIR_NOT_SHARED',
    )
  ) {
    return pathWarnings
  }
  const w = workdirStagingWarning(staging)
  if (w) pathWarnings.push(w)
  return pathWarnings
}

export interface OutJsonInfo {
  path: string | null
  bytes: number
  wsl: string | null
}

export function writeResultJson(
  parsed: unknown,
  opts: HeadlessOpts,
  d: Pick<HeadlessDeps, 'dumpResult' | 'readScriptPath'>,
  runId: string,
): OutJsonInfo {
  // ---- v0.9.1（93-A3）：结构化结果落盘（>4KB 自动落，或 outJson= 指定路径）—— 不必再从 stdout 里 indexOf 切片
  const outJsonInfo: OutJsonInfo = { path: null, bytes: 0, wsl: null }
  try {
    if (parsed !== null && parsed !== undefined) {
      const s = JSON.stringify(parsed)
      outJsonInfo.bytes = s.length
      if (opts.outJson) {
        const t = d.readScriptPath(String(opts.outJson))
        fs.mkdirSync(path.dirname(t.wsl), { recursive: true })
        fs.writeFileSync(t.wsl, s, 'utf8')
        outJsonInfo.path = t.win
        outJsonInfo.wsl = t.wsl // Windows 形态 + WSL 可见形态各一份
      } else if (s.length > 4000) {
        const dump = d.dumpResult(parsed, runId)
        outJsonInfo.path = dump.path
        outJsonInfo.bytes = dump.bytes
        outJsonInfo.wsl = outJsonInfo.path ? winToWsl(outJsonInfo.path) : null // 两种形态都给
      }
    }
  } catch (e) {
    /* 落盘失败不影响主结果 */
  }
  return outJsonInfo
}
