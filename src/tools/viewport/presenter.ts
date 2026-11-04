/** blender_viewport 的呈现层：把后端回执格式化成模型可读文本（不改变任何判定）。 */
import { HERE, HOLDER, HOST_API, PLUGIN_VERSION } from '../../constants.js'
import { schemaResolve } from '../../contracts/tool.js'
import { asArray, asRecord, text } from '../../contracts/dynamic.js'
import { base, childAlive, getChild, isPaused } from '../../host/backend/index.js'
import { tuiHostSummary } from '../../tui/index.js'

const NL = String.fromCharCode(10)
// P0-3：把「超时/被 abort」与「连接被拒」分开 —— 前者是 addon/bpy 往返没回，后者才是后端没起。
const TIMEOUT_RE = /timeout|timed out|aborted|abort|ETIMEDOUT/i
const REFUSED_RE = /ECONNREFUSED|ECONNRESET|EHOSTUNREACH|ENOTFOUND|fetch failed|socket hang up/i

export function renderViewportOutput(
  _args: unknown,
  value: unknown,
): Array<{ type: 'text'; text: string }> {
  return [{ type: 'text', text: String(value) }]
}

export function formatWho(raw: unknown): string {
  const w = asRecord(raw)
  const lz = asRecord(w.lease)
  const m = asRecord(w.metrics)
  return [
    '租约：' +
      (lz.active
        ? '持有者 ' +
          text(lz.holder) +
          '（剩余 ' +
          String(Math.round(Number(lz.expiresInMs || 0) / 1000)) +
          's，续期 ' +
          text(lz.renewals || 0) +
          ' 次）'
        : '空闲（谁都能写）'),
    '本会话 holder：' + HOLDER + (lz.active && lz.holder === HOLDER ? '（就是本会话）' : ''),
    '写作用域：' + (lz.scope ? JSON.stringify(lz.scope) : '未声明（= 不限制）'),
    '通道：calls=' +
      text(m.calls || 0) +
      ' errors=' +
      text(m.errors || 0) +
      ' timeouts=' +
      text(m.timeouts || 0) +
      ' inflight=' +
      text(m.inflight || 0) +
      ' lastCmd=' +
      text(m.lastCmd || '-'),
    '统计：' + JSON.stringify(w.stats || {}),
  ].join(NL)
}

export function formatLeaseResult(op: string, r: unknown): string {
  if (op === 'lease' && asRecord(r).error === 'leased') {
    const x = asRecord(r)
    return (
      '租约被占用：holder=' +
      text(x.holder) +
      '，剩余 ' +
      String(Math.round(Number(x.expiresInMs || 0) / 1000)) +
      's' +
      NL +
      text(x.hint) +
      NL +
      '（确认对方在跑就知道为什么被挡；要强抢再加 force=true）'
    )
  }
  return (op === 'lease' ? 'LEASE ' : 'RELEASE ') + JSON.stringify(r)
}

export function formatLeaseError(op: string, e: unknown): string {
  return op + ' 失败：' + errorText(e) + '（后端没起？blender_viewport op=start）'
}

export function formatLaunch(r: unknown): string {
  const x = asRecord(r)
  const lines: string[] = []
  lines.push(
    (x.ok ? 'LAUNCH ok' : 'LAUNCH 未成功') +
      (x.already ? ' · 已经在监听（幂等，未重复启动）' : x.dryRun ? ' · dryRun（没启动）' : '') +
      (x.launched ? ' · 已 spawn pid=' + text(x.pid) : '') +
      ' · addon 端口 ' +
      text(x.addonPort || '-') +
      ' · 等待 ' +
      text(x.waitedMs || 0) +
      'ms',
  )
  if (x.exe)
    lines.push(
      'blender.exe：' + text(x.exe) + (x.configFrom ? '（来源 ' + text(x.configFrom) + '）' : ''),
    )
  if (x.error) lines.push('错误：' + text(x.error))
  if (x.hint) lines.push('提示：' + text(x.hint))
  const steps = asArray(x.steps)
  for (const s of steps) lines.push('· ' + text(s))
  appendLaunchBootLines(lines, x)
  const doc = asRecord(x.doctor)
  const addon = asRecord(doc.addon)
  if (doc.addon)
    lines.push(
      '启动后体检：' + (addon.summary ? text(addon.summary) : JSON.stringify(addon)).slice(0, 400),
    )
  if (x.bootScript) lines.push('boot 脚本：' + text(x.bootScript) + '（想手工排查就直接跑它）')
  return lines.join(NL)
}

function appendLaunchBootLines(lines: string[], x: Record<string, unknown>): void {
  const b = asRecord(x.boot)
  if (x.boot) {
    lines.push(
      'boot 结论（GUI 侧自己写的 launch-status.json）：loaded_by=' +
        text(b.loaded_by || '-') +
        ' · server_running=' +
        text(b.server_running) +
        ' · enabled=' +
        JSON.stringify(b.enabled || []),
    )
    const bootErrors = asArray(b.errors)
    if (bootErrors.length) lines.push('boot 错误：' + bootErrors.slice(0, 4).map(text).join(' | '))
    if (b.env) lines.push('boot 环境：' + JSON.stringify(b.env))
    const seen = asArray(b.addon_modules_seen)
    if (seen.length)
      lines.push('GUI 里可见的 addon 模块：' + seen.slice(0, 20).map(text).join(', '))
    return
  }
  if (x.launched || x.dryRun) {
    lines.push(
      'boot 状态文件：' +
        text(x.statusFile || '?') +
        '（没读到：可能 Blender 还没走到脚本，或 exe 路径不对）',
    )
  }
}

export function formatDoctor(raw: unknown): string {
  const d = asRecord(raw)
  const metrics = asRecord(d.metrics)
  const diag = d.diagnosis ? asRecord(d.diagnosis) : asRecord(metrics.lastDiagnosis)
  const lines: string[] = []
  lines.push('诊断：' + text(d.kind || diag.kind || (d.ok ? 'ok' : 'unknown')))
  lines.push('宿主 API：' + HOST_API) // v0.9.2（R2）：DSH 升级后一眼看出插件挂的是哪份 dsh-tools
  lines.push(tuiHostSummary()) // v2.6.1：dsh-TUI 宿主半边接上了哪些接缝
  lines.push('插件：v' + PLUGIN_VERSION + ' @ ' + HERE)
  lines.push(
    'schemastery：' +
      (schemaResolve.via
        ? '已解析（' + schemaResolve.via + '）'
        : '❌ ' + String(schemaResolve.error || '未解析') + ' —— 工具未注册（降级模式）'),
  )
  if (diag.summary) lines.push('结论：' + text(diag.summary))
  if (diag.fix) lines.push('修法：' + text(diag.fix))
  if (d.addon) lines.push('addon：' + JSON.stringify(d.addon))
  const ledger = d.ledger ? asRecord(d.ledger) : null
  const workDir = ledger ? asRecord(ledger.workDir) : null
  if (ledger)
    lines.push(
      '工作目录：' +
        text(workDir?.wsl || '?') +
        (workDir?.writable
          ? '（可写）'
          : '（⚠️ **不可写** —— ' + text(ledger.lastError || '?') + '）'),
    )
  if (ledger && !ledger.writable && ledger.hint) lines.push('修法：' + text(ledger.hint))
  lines.push('计数：' + JSON.stringify(d.stats || {}))
  lines.push(
    '指标：calls=' +
      text(metrics.calls || 0) +
      ' errors=' +
      text(metrics.errors || 0) +
      ' timeouts=' +
      text(metrics.timeouts || 0) +
      ' inflight=' +
      text(metrics.inflight || 0) +
      ' lastCmd=' +
      text(metrics.lastCmd || '-') +
      (metrics.lastCmdAgeMs === null || metrics.lastCmdAgeMs === undefined
        ? ''
        : ' (' + String(Math.round(Number(metrics.lastCmdAgeMs) / 1000)) + 's 前)') +
      ' views=' +
      text(metrics.views || 0) +
      ' headlessRuns=' +
      text(metrics.headlessRuns || 0),
  )
  return lines.join(NL)
}

export function formatStatus(port: number, raw: unknown): string {
  const s = asRecord(raw)
  const region = s.region ? asRecord(s.region) : null
  const views = region ? asArray(region.views) : []
  const v = views.length ? asRecord(views[0]) : null
  const ledger = s.ledger ? asRecord(s.ledger) : null
  const plugin = s.plugin ? asRecord(s.plugin) : null
  const runs = asArray(s.runs)
  const child = getChild()
  const payload: Record<string, unknown> = {
    running: true,
    paused: isPaused(),
    child: child
      ? childAlive()
        ? 'alive pid=' + String(child.pid)
        : 'dead pid=' + String(child.pid)
      : 'none',
    watchdog: '15s',
    endpoint: base(port),
    viewportRegion: v ? v.region : null,
    shading: v ? v.shading : null,
    window: region ? region.window : null,
    stats: s.stats,
    diagnosis: s.diagnosis || null,
    addonError: s.addonError || null, // v0.9.3：addon 没连上也要能看到本地信息（台账/工作目录）
    plugin: s.plugin || null, // v0.8.10（A0）：进程内 lib 的版本 + runtime 指纹
    pluginVersion: plugin ? plugin.version : null,
    hostApi: HOST_API, // v0.9.2（R2）：插件侧解析到的宿主 dsh-tools
    toolVersion: PLUGIN_VERSION,
    // v1.0.1+S11（issue #10）：包名形态解析走哪条通道 / 为什么失败 —— 这类静默整体消失必须留现场
    schemaResolve: {
      via: schemaResolve.via || null,
      error: schemaResolve.error || null,
      tried: schemaResolve.tried,
    },
    recentRuns: runs.map((r) => formatRun(r)),
    ledger: s.ledger || null, // v0.8.10（D4）：磁盘台账（后端重启后仍可查）
    // v0.9.3：workDir 可写性自证（只读挂载上写台账/日志会静默失败 —— 这里必须说出来）
    workDir: ledger ? ledger.workDir || null : null,
    workDirWritable: !!(ledger && ledger.writable),
    workDirWarning: ledger && !ledger.writable ? text(ledger.hint || ledger.lastError || '') : null,
  }
  return appendAddonVerdict(payload, s)
}

/** P0-3：addon 出问题时如实标注，next_action 指向 Blender addon（不是 op=start）。 */
function appendAddonVerdict(payload: Record<string, unknown>, s: Record<string, unknown>): string {
  if (!s.addonError) return JSON.stringify(payload)
  const addonError = text(s.addonError)
  payload.addonError = addonError
  const timeout = isTimeoutError(addonError)
  payload.code = timeout ? 'addon_unresponsive' : 'addon_error'
  payload.next_action = timeout
    ? '后端（9877）在，addon/bpy 没在预算内回包：检查 Blender 是否运行且「DSH Blender Bridge」已 Connect、是否卡在渲染/模态操作；重载 addon 后用 op=doctor 复核'
    : '后端（9877）在，addon 回了错误：按 addonError 修（检查 Blender 是否运行、「DSH Blender Bridge」是否 Connect），再用 op=doctor 复核'
  return JSON.stringify(payload)
}

function formatRun(raw: unknown): Record<string, unknown> {
  const x = asRecord(raw)
  const expect = asRecord(x.expect)
  return {
    id: x.id,
    status: x.status,
    pid: x.pid,
    ms: x.ms,
    outdir: x.outdir,
    artifacts: x.artifactCount,
    expectOk: x.expect ? expect.ok : null,
  }
}

/** P0-3：超时/被 abort ≠ 连接被拒 —— 判定两套 verdict 的共用判据。 */
export function isTimeoutError(e: unknown): boolean {
  const name = e && typeof e === 'object' ? String((e as { name?: unknown }).name || '') : ''
  return name === 'TimeoutError' || name === 'AbortError' || TIMEOUT_RE.test(errorText(e))
}

export function formatStatusError(port: number, e: unknown): string {
  const err = errorText(e)
  if (isTimeoutError(e))
    return JSON.stringify({
      running: false,
      url: base(port) + '/',
      error: err,
      code: 'backend_timeout',
      next_action:
        '先 blender_viewport op=doctor（有界，≤10s）确认 addon/bpy 是否可用；不要反复 op=start —— 超时不等于后端没起',
      hint: '这次没在预算内拿到 /status 回执：可能是 addon/bpy 往返卡住（Blender 在渲染/模态操作）或本次等待被宿主打断；/health 通就说明后端进程在',
    })
  const refused = REFUSED_RE.test(err)
  return JSON.stringify({
    running: false,
    url: base(port) + '/',
    error: err,
    code: 'backend_unreachable',
    next_action: refused
      ? '用 blender_viewport op=start 拉起后端；仍失败看 op=doctor'
      : '用 blender_viewport op=doctor 先判因（看 /health）；确实没起再用 op=start',
    hint: refused
      ? '连接被拒绝：9877 没有进程在监听 —— 用 blender_viewport op=start 拉起后端'
      : '未判定为连接拒绝：先 op=doctor 看 /health，再决定是否 op=start',
  })
}

/** 错误文本带上 cause（Node fetch 把 ECONNREFUSED 藏在 TypeError('fetch failed').cause 里）。 */
function errorText(e: unknown): string {
  if (e instanceof Error) {
    const cause = (e as Error & { cause?: unknown }).cause
    return cause === undefined ? e.message : e.message + ': ' + errorText(cause)
  }
  return String(e)
}
