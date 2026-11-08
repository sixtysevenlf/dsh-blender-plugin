/**
 * blender_inspect：只读命令目录 + 单条只读命令执行。
 *
 * 命令面真源：addon command registry（25 registered / 26 wire）。host 侧只保留
 * 目录（description/access/gate）与迁移提示，不复制参数表。
 */
import { BACKEND_HINT } from '../../constants.js'
import { asRecord, text } from '../../contracts/dynamic.js'
import { ensureBackend } from '../../host/backend/index.js'
import { backendJson, backendPost, jsonPayload, leasedText } from '../../host/http.js'
import { toolError } from '../errors.js'
import type { ToolFailure } from '../errors.js'
import { formatCommandResult, formatHelp, rowsOf, type CommandRow } from './presenter.js'

interface WrongToolTarget {
  tool: string
  hint: string
}

/** 旧写命令与内部传输命令：blender_inspect 一律拒绝，并指向正确的工具。 */
const WRONG_TOOL: Record<string, WrongToolTarget> = {
  export_scene: { tool: 'blender_op', hint: '导出用 blender_op 的 deliver_* / 导出算子' },
  set_texture: { tool: 'blender_op', hint: '材质/贴图用 blender_op 的 material_* 算子' },
  download_polyhaven_asset: {
    tool: 'blender_py',
    hint: '外部资产下载/导入不再走 addon 命令；用 blender_py 写脚本，或用 op 的资产算子',
  },
  get_polyhaven_asset_preview: {
    tool: 'blender_py',
    hint: '预览图下载不再走 addon 命令；需要图用 blender_see 看视口',
  },
  create_rodin_job: { tool: 'blender_py', hint: 'Hyper3D 生成不再走 addon 命令；只保留只读轮询' },
  import_generated_asset: {
    tool: 'blender_py',
    hint: '生成结果导入用 blender_py 脚本（bpy.ops / bpy.data）',
  },
  download_sketchfab_model: {
    tool: 'blender_py',
    hint: 'Sketchfab 下载不再走 addon 命令；搜索仍可用 search_sketchfab_models',
  },
  download_polypizza_model: {
    tool: 'blender_py',
    hint: 'Poly Pizza 下载不再走 addon 命令；搜索仍可用 search_polypizza_models',
  },
  create_hunyuan_job: {
    tool: 'blender_py',
    hint: 'Hunyuan3D 生成不再走 addon 命令；只保留只读轮询',
  },
  import_generated_asset_hunyuan: {
    tool: 'blender_py',
    hint: '生成结果导入用 blender_py 脚本（bpy.ops / bpy.data）',
  },
  get_telemetry_consent: {
    tool: 'none',
    hint: '3.1 零遥测：没有 telemetry consent 命令，也没有替代工具',
  },
  set_telemetry_consent: {
    tool: 'none',
    hint: '3.1 零遥测：没有 telemetry consent 命令，也没有替代工具',
  },
  drain_human_activity: {
    tool: 'blender_session',
    hint: '人改事件由 Gateway 事件流自动送达；查询用 blender_inspect command=get_activity_state',
  },
  execute_code: {
    tool: 'blender_py',
    hint: '执行 Python 用 blender_py（GUI 会话内，固定 exclusive）',
  },
  get_viewport_screenshot: {
    tool: 'blender_see',
    hint: '截图/看视口用 blender_see（viewport 视角）',
  },
  send_selection_to_dsh: {
    tool: 'none',
    hint: '这是 Blender N 面板按钮的 host 内部传输，模型面没有对应命令；选中用 get_selection',
  },
  send_collection_to_dsh: {
    tool: 'none',
    hint: '这是 Blender N 面板按钮的 host 内部传输，模型面没有对应命令；集合用 get_collections',
  },
}

function wrongTool(name: string, target: WrongToolTarget, detail?: string): ToolFailure {
  return toolError(
    'wrong_tool',
    'INSPECT ' + name + ' 不是只读模型命令（' + target.hint + '）',
    target.tool === 'none'
      ? '没有替代工具：' + target.hint
      : '改用 ' + target.tool + '：' + target.hint,
    detail,
  )
}

function levenshtein(a: string, b: string): number {
  const prev = new Array<number>(b.length + 1)
  const cur = new Array<number>(b.length + 1)
  for (let j = 0; j <= b.length; j++) prev[j] = j
  for (let i = 1; i <= a.length; i++) {
    cur[0] = i
    for (let j = 1; j <= b.length; j++)
      cur[j] = Math.min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1))
    for (let j = 0; j <= b.length; j++) prev[j] = cur[j]
  }
  return prev[b.length]
}

function didYouMean(name: string, rows: CommandRow[]): string[] {
  return rows
    .map((r) => ({ name: r.name, d: levenshtein(name, r.name) }))
    .filter((x) => x.d <= Math.max(2, Math.floor(name.length / 3)))
    .sort((a, b) => a.d - b.d)
    .slice(0, 3)
    .map((x) => x.name)
}

async function fetchRows(
  port: number,
): Promise<{ ok: true; body: Record<string, unknown> } | ToolFailure> {
  let body: Record<string, unknown>
  try {
    body = await backendJson(port, '/commands', 45000)
  } catch (e: unknown) {
    return toolError(
      'addon_unreachable',
      'INSPECT help 失败 · ' + text(e instanceof Error ? e.message : e),
      '先切回 Blender N 面板点 Connect（addon 主动连 Gateway 39877）；再 blender_session action=doctor',
    )
  }
  if (body.ok === false && !Array.isArray(body.commands))
    return toolError(
      'addon_unreachable',
      'INSPECT help 失败 · ' + text(body.error || body.raw || 'unknown'),
      'blender_session action=status 看 addon 是否 connected；未连接时让用户在 Blender N 面板点 Connect',
    )
  return { ok: true, body }
}

export async function executeInspect(
  port: number,
  args: Record<string, unknown>,
): Promise<unknown> {
  if (!(await ensureBackend(port)))
    return toolError(
      'gateway_unreachable',
      'Gateway 不可用（127.0.0.1:' + String(port) + '）',
      BACKEND_HINT,
    )
  const command = text(args.command).trim() || 'help'
  if (command === 'help') {
    const got = await fetchRows(port)
    if (!('ok' in got)) return got
    const rows = rowsOf(got.body)
    return { text: formatHelp(rows, got.body.scene, got.body.file) }
  }
  const target = WRONG_TOOL[command]
  if (target) return wrongTool(command, target)
  const got = await fetchRows(port)
  if (!('ok' in got)) return got
  const rows = rowsOf(got.body)
  const row = rows.find((r) => r.name === command)
  if (!row) {
    if (command === 'ping')
      return toolError(
        'unknown_command',
        'ping 只是 wire 级 liveness 探测，不是模型命令',
        'addon 连通性用 blender_session action=status / doctor',
      )
    const suggest = didYouMean(
      command,
      rows.filter((r) => r.access === 'read'),
    )
    return toolError(
      'unknown_command',
      'INSPECT 未知命令：' + command,
      suggest.length
        ? 'did_you_mean: ' + suggest.join(', ') + '；command=help 看完整列表'
        : 'command=help 看可执行命令列表',
    )
  }
  if (row.access === 'internal')
    return wrongTool(
      command,
      WRONG_TOOL[command] ?? { tool: 'none', hint: '内部传输命令，不面向模型' },
    )
  if (!row.enabled)
    return toolError(
      'command_disabled',
      'INSPECT ' + command + ' 被集成开关挡住（' + String(row.gate || 'gate') + '）',
      '在 Blender N 面板打开对应集成开关后重试；command=help 看哪些命令被禁用',
    )
  const params = jsonPayload(args.args)
  let r: Record<string, unknown>
  try {
    r = await backendPost(port, '/cmd', { name: command, params, timeoutMs: 120000 }, 300000)
  } catch (e: unknown) {
    return toolError('addon_unreachable', 'INSPECT ' + command + ' 失败 · ' + text(e), BACKEND_HINT)
  }
  const lt = leasedText(r)
  if (lt) return { text: lt }
  if (!r || r.ok !== true)
    return toolError(
      'backend_error',
      'INSPECT ' + command + ' 失败 · ' + text(r.error || r.raw || 'unknown'),
      '看参数名与 args（command=help 里有参数列表）；参数错时按命令自检修正',
    )
  const merged = { ...r, ms: asRecord(r).ms }
  return { text: formatCommandResult(command, merged) }
}
