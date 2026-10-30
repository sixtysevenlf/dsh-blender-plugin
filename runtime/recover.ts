/**
 * v2.6 · 磁盘台账对账（本轮实测反馈 #1：pid 没了 ≠ 任务失败）
 *
 * 现场：后端重启/被外部杀掉之后，jobs/index.jsonl 里只剩一条 status=running、pid 早已不存在；
 * 旧行为一律收敛成 stale，而**子进程其实已经跑完、产物/日志都在**。调用方（模型）只能自己去
 * Get-Process + ls 破案，实测每次至少赔一轮往返。
 *
 * 新行为：先找磁盘证据，对得上就判 completed (recovered)；找不到证据才判 stale。
 * 证据优先级：
 *   ① <jobDir>/summary.json        —— 作业终态摘要（引擎自己在 close 时写的）
 *   ② rec.resultPath               —— 结构化结果落盘文件（>4KB 或显式 out_json）
 *   ③ stdout 日志终态              —— DSH_STAGE {"name":"done"} / HEADLESS {json} / Traceback
 *
 * 设计约束：日志可能有几百 MB —— 只读尾部 256 KB；只在 pid 已死的罕见路径上调用。
 */
import fs from 'node:fs'
import path from 'node:path'
import { errorMessage } from "./core/json.js";

export const RECOVER_VERSION = '2.6.1'
const NL = String.fromCharCode(10)
const STAGE_TAG = 'DSH_STAGE '
const PROGRESS_TAG = 'DSH_PROGRESS '

export interface MapPair {
  winToWsl: (p: string) => string;
  wslToWin: (p: string) => string;
}

/** 默认映射器（同 OS 时原样返回）—— engine 会注入真实的 winToWsl/wslToWin */
const IDENTITY_MAP: MapPair = { winToWsl: (p: string) => p, wslToWin: (p: string) => p }

function toWsl(p: unknown, map: MapPair): string | null {
  const s = String(p == null ? '' : p)
  if (!s) return null
  if (/^[A-Za-z]:[\\/]/.test(s) || s.startsWith('\\\\')) return map.winToWsl(s)
  return s
}

export interface LogEvidence {
  path: string | null;
  exists: boolean;
  bytes: number;
  mtime: number | null;
  lastStage: string | null;
  sawDone: boolean;
  sawScriptEnd: boolean;
  sawHeadless: boolean;
  sawTraceback: boolean;
  lastProgress: Record<string, unknown> | null;
  tail: string | null;
  error?: string;
}

/**
 * 读日志尾部并抽"终态证据"。只读尾部，避免大日志把进程拖死。
 * 返回 { exists, bytes, mtime, lastStage, sawDone, sawScriptEnd, sawHeadless, sawTraceback,
 *        lastProgress, path, tail, error? }
 */
export function logEvidence(pathWsl: string, maxBytes = 262144): LogEvidence {
  const ev: LogEvidence = { path: pathWsl, exists: false, bytes: 0, mtime: null, lastStage: null,
               sawDone: false, sawScriptEnd: false, sawHeadless: false, sawTraceback: false,
               lastProgress: null, tail: null }
  try {
    const st = fs.statSync(pathWsl)
    if (!st.isFile()) return ev
    ev.exists = true; ev.bytes = st.size; ev.mtime = st.mtimeMs
    const len = Math.min(maxBytes, st.size)
    const buf = Buffer.alloc(len)
    const fd = fs.openSync(pathWsl, 'r')
    try { fs.readSync(fd, buf, 0, len, st.size - len) } finally { fs.closeSync(fd) }
    const txt = buf.toString('utf8')
    ev.tail = txt.slice(-4000)
    for (const raw of txt.split(NL)) {
      const s = raw.replace(/\r$/, '')
      if (s.indexOf(STAGE_TAG) === 0) {
        try {
          const o = JSON.parse(s.slice(STAGE_TAG.length).trim()) as Record<string, unknown>
          if (o && o.name) {
            ev.lastStage = String(o.name)
            if (o.name === 'done') ev.sawDone = true
            if (o.name === 'script-end') ev.sawScriptEnd = true
          }
        } catch (e) { /* 半行/坏行忽略 */ }
      } else if (s.indexOf(PROGRESS_TAG) === 0) {
        try {
          const o = JSON.parse(s.slice(PROGRESS_TAG.length).trim()) as Record<string, unknown>
          if (o && typeof o === 'object') ev.lastProgress = o
        } catch (e) { /* 坏行忽略 */ }
      } else if (s.indexOf('HEADLESS ') === 0) {
        ev.sawHeadless = true
      } else if (/^Traceback \(most recent call last\)/.test(s)) {
        ev.sawTraceback = true
      }
    }
  } catch (e) {
    ev.error = errorMessage(e).slice(0, 160)
  }
  return ev
}

export interface RecoverOut {
  found: boolean;
  source: string | null;
  status: string | null;
  exitCode: unknown;
  finishedAt: number | null;
  artifacts: unknown[];
  evidence: Record<string, unknown> | null;
  stdoutLog: string | null;
  stderrLog: string | null;
  lastProgress: Record<string, unknown> | null;
}

/**
 * 在磁盘上找"这条 run/job 已经跑完"的证据。
 * rec 可含：dir / jobDir / outdir / resultPath / stdoutLog / stdoutLogWsl / logs
 * map = {winToWsl, wslToWin}
 */
export function recoverFromDisk(rec: Record<string, unknown> | null | undefined, map: MapPair = IDENTITY_MAP): RecoverOut {
  const out: RecoverOut = { found: false, source: null, status: null, exitCode: undefined,
                finishedAt: null, artifacts: [], evidence: null, stdoutLog: null, stderrLog: null,
                lastProgress: null }
  if (!rec || typeof rec !== 'object') return out

  // ① 作业终态摘要
  const dirWin = (rec.dir || rec.jobDir || null) as string | null
  if (dirWin) {
    try {
      const dirWsl = toWsl(dirWin, map) as string
      const sPath = path.join(dirWsl, 'summary.json')
      const st = fs.statSync(sPath)
      const s = JSON.parse(fs.readFileSync(sPath, 'utf8')) as Record<string, unknown>
      out.found = true; out.source = 'summary.json'
      out.status = (s.status === 'failed' || s.status === 'killed') ? 'failed' : 'done'
      out.exitCode = s.exitCode
      out.finishedAt = typeof s.finishedAt === 'number' && s.finishedAt ? s.finishedAt : st.mtimeMs
      out.artifacts = Array.isArray(s.artifacts) ? s.artifacts : []
      out.evidence = { path: map.wslToWin ? map.wslToWin(sPath) : sPath, mtime: st.mtimeMs,
                       bytes: st.size, exitCode: s.exitCode == null ? null : s.exitCode,
                       stage: s.stage || null, lastException: s.lastException || null }
      out.stdoutLog = path.join(dirWsl, 'stdout.log')
      out.stderrLog = path.join(dirWsl, 'stderr.log')
      return out
    } catch (e) { /* 没有摘要 → 看日志 */ }
  }

  // ② 结构化结果落盘
  if (rec.resultPath) {
    try {
      const p = toWsl(rec.resultPath, map) as string
      const st = fs.statSync(p)
      out.found = true; out.source = 'resultPath'; out.status = 'done'
      out.finishedAt = st.mtimeMs
      out.evidence = { path: rec.resultPath, mtime: st.mtimeMs, bytes: st.size }
      return out
    } catch (e) { /* 结果文件不存在 → 看日志 */ }
  }

  // ③ stdout 日志（run 与 job 通用；候选按可信度排序）
  const cands: unknown[] = []
  if (rec.stdoutLog) cands.push(rec.stdoutLog)
  if (rec.stdoutLogWsl) cands.push(rec.stdoutLogWsl)
  const logs = rec.logs as { stdout?: unknown } | null | undefined
  if (logs && logs.stdout) cands.push(logs.stdout)
  if (dirWin) {
    const dw = toWsl(dirWin, map)
    if (dw) cands.push(path.join(dw, 'stdout.log'))
  }
  const seen = new Set<string>()
  for (const c of cands) {
    const wsl = toWsl(c, map)
    if (!wsl || seen.has(wsl)) continue
    seen.add(wsl)
    const ev = logEvidence(wsl)
    if (!ev.exists) continue
    out.found = true; out.source = 'stdout.log'
    out.stdoutLog = wsl
    out.stderrLog = wsl.replace(/stdout\.log$/, 'stderr.log')
    out.lastProgress = ev.lastProgress || null
    out.evidence = { path: map.wslToWin ? map.wslToWin(wsl) : wsl, bytes: ev.bytes, mtime: ev.mtime,
                     lastStage: ev.lastStage, sawDone: ev.sawDone, sawHeadless: ev.sawHeadless,
                     sawTraceback: ev.sawTraceback }
    if (ev.sawDone || ev.sawHeadless) out.status = ev.sawTraceback ? 'failed' : 'done'
    else if (ev.sawTraceback) out.status = 'failed'
    else out.status = 'incomplete'
    out.finishedAt = ev.mtime
    return out
  }
  return out
}

/**
 * 把一条 status=running 的磁盘台账记录收敛成终态。
 *  - pid 还活着 → 原样返回（不抢）
 *  - pid 没了但磁盘有终态证据 → status=done/failed + recovered:true
 *  - 否则 → status=stale（保留旧语义），但附 evidence 让调用方少一次破案
 */
export function reconcileLedger(rec: Record<string, unknown> | null, map: MapPair = IDENTITY_MAP, isAlive: (pid: unknown) => boolean = () => false): Record<string, unknown> | null {
  if (!rec || rec.status !== 'running') return rec
  if (rec.pid && isAlive(rec.pid)) return rec
  const deadWhy = rec.pid
    ? ('pid ' + String(rec.pid) + ' 已不存在（进程被外部杀掉，或后端重启后丢了句柄）')
    : '台账里没有 pid（后端重启前只记了 running）'
  const ev = recoverFromDisk(rec, map)
  if (ev.found && (ev.status === 'done' || ev.status === 'failed')) {
    return Object.assign({}, rec, {
      status: ev.status,
      recovered: true,
      wasStatus: 'running',
      pidAlive: false,
      recoveredFrom: ev.source,
      recoveredEvidence: ev.evidence,
      recoveredLastProgress: ev.lastProgress || undefined,
      exitCode: ev.exitCode === undefined ? rec.exitCode : ev.exitCode,
      finishedAt: ev.finishedAt || rec.finishedAt || null,
      stage: (ev.evidence && ev.evidence.lastStage) || rec.stage || null,
      artifacts: (ev.artifacts && ev.artifacts.length) ? ev.artifacts : rec.artifacts,
      staleReason: deadWhy,
      hint: '**进程已不在，但磁盘产物对得上 → 判为已完成（recovered，证据：' + ev.source + '）**。'
          + '结果/日志/产物照常可读：op=collect 会给 stdoutTail，op=list 里也看得到。'
          + '要重跑请用同一命令再起一次 —— 不要拿这个 id 继续等。',
    })
  }
  return Object.assign({}, rec, {
    status: 'stale', wasStatus: 'running', pidAlive: false, staleReason: deadWhy,
    evidence: ev.found ? ev.evidence : null,
    evidenceChecked: ['summary.json', 'resultPath', 'stdout.log'],
    hint: '这条只是**磁盘台账**里的历史记录（内存句柄已丢）。'
        + (ev.found
          ? ('日志在但没跑出终态（lastStage=' + String((ev.evidence && ev.evidence.lastStage) || '?') + '）→ 判未完成。')
          : '日志/产物都找不到 → 判未完成。')
        + '确认进程：PowerShell Get-Process blender；清孤儿：Stop-Process。新作业用 op=kill 收。',
  })
}
