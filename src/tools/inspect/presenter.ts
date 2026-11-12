/** blender_inspect 的呈现层：只读命令 help 与单命令回执。 */
import { asRecord, text } from '../../contracts/dynamic.js'

const LF = String.fromCharCode(10)
const CLIP = 3000

export interface CommandRow {
  name: string
  description: string
  access: string
  gate: string | null
  enabled: boolean
  pause_safe: boolean
  network: boolean
  args: string[]
}

export function rowsOf(body: Record<string, unknown>): CommandRow[] {
  const raw = Array.isArray(body.commands) ? body.commands : []
  const rows: CommandRow[] = []
  for (const item of raw) {
    const r = asRecord(item)
    if (!r.name) continue
    rows.push({
      name: String(r.name),
      description: text(r.description || ''),
      access: text(r.access || 'read'),
      gate: r.gate ? String(r.gate) : null,
      enabled: r.enabled !== false,
      pause_safe: r.pause_safe === true,
      network: r.network === true,
      args: Array.isArray(r.args) ? r.args.map((x) => String(x)) : [],
    })
  }
  return rows
}

function commandLine(r: CommandRow): string {
  const flags = []
  if (r.pause_safe) flags.push('pause_safe')
  if (r.network) flags.push('network')
  return (
    '· ' +
    r.name +
    '(' +
    (r.args.length ? r.args.join(', ') : '无参') +
    ')' +
    (flags.length ? ' · ' + flags.join(' · ') : '') +
    (r.description ? ' · ' + r.description : '')
  )
}

export function formatHelp(rows: CommandRow[], scene: unknown, file: unknown): string {
  const readable = rows.filter((r) => r.access === 'read')
  const enabled = readable.filter((r) => r.enabled)
  const disabled = readable.filter((r) => !r.enabled)
  const internal = rows.length - readable.length
  const lines = [
    'INSPECT help · ' +
      String(enabled.length) +
      ' 个可执行只读命令' +
      (disabled.length ? ' · ' + String(disabled.length) + ' 个被 gate 关闭' : '') +
      (internal > 0 ? '（另有 ' + String(internal) + ' 个内部传输命令不列出、不可执行）' : '') +
      (scene ? ' · 场景 ' + text(scene) : '') +
      (file ? ' · 文件 ' + text(file) : ''),
  ]
  for (const r of enabled.sort((a, b) => a.name.localeCompare(b.name))) lines.push(commandLine(r))
  if (disabled.length) {
    // A gated command is NOT executable: listing it with the runnable ones made
    // the help read as if blender_inspect could call it.
    lines.push('')
    lines.push('被 gate 关闭（enabled=false，当前不可执行）：')
    for (const r of disabled.sort((a, b) => a.name.localeCompare(b.name)))
      lines.push(
        '· ' +
          r.name +
          '(' +
          (r.args.length ? r.args.join(', ') : '无参') +
          ') · DISABLED(' +
          String(r.gate || 'gate') +
          ')',
      )
  }
  lines.push('执行：blender_inspect command=<name> args={...}（只读；写操作请用对应工具）')
  return lines.join(LF)
}

export function formatCommandResult(name: string, r: Record<string, unknown>): string {
  const ms = r.ms === undefined ? '' : String(r.ms) + 'ms'
  const result = r.result === undefined ? r : r.result
  const payload = typeof result === 'string' ? result : JSON.stringify(result)
  return (
    'INSPECT ' +
    name +
    ' ok' +
    (ms ? ' · ' + ms : '') +
    ' · ' +
    String(payload.length) +
    ' chars' +
    LF +
    (payload.length > CLIP ? payload.slice(0, CLIP) + ' …' : payload)
  )
}

export function inspectHelp(): string {
  return [
    'blender_inspect：只读命令目录与执行。',
    '  help（默认）：列出可执行的只读命令（name(args) / enabled / pause_safe / description）',
    '  command=<name> args={...}：执行一条只读命令（等同旧 blender_inspect 的只读面）',
    '内部传输命令（execute_code / get_viewport_screenshot / send_*）不列出、不可执行：',
    '  执行 Python 用 blender_py；截图/看视口用 blender_see；作业用 blender_run。',
  ].join(LF)
}
