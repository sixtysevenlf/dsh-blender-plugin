/** blender_rt_headless 执行层：参数校验/body 组装/同步或作业路由（行为与 v2.6.2 一致）。 */
import type { HostContext } from '../../host/context.js'
import { ensureBackend } from '../../host/backend/index.js'
import { backendPost, leasedText, splitArgs } from '../../host/http.js'
import { HEADLESS_WAIT_MS, newRunId, promotedFromTimeout } from '../../headless/receipts/index.js'
import { detectFileAlias } from '../../host/runtime-bridge.js'
import { toolError } from '../errors.js'
import { asRecord } from '../../contracts/dynamic.js'
import { attachShots, missingResultText, runAsJob, syncReceipt } from './receipt.js'

export async function executeHeadless(
  args: Record<string, unknown>,
  ctx: HostContext,
  port: number,
): Promise<unknown> {
  if (!(await ensureBackend(port)))
    return toolError(
      'backend_unreachable',
      '后端不可用（127.0.0.1:' + String(port) + '）',
      '无头通道由后端进程代管，先 blender_viewport op=start',
    )
  const bad = validateFileArgs(args)
  if (bad) return bad
  const runId = newRunId()
  const asJob = !!(args && args.as_job)
  const waitS =
    args && args.wait_s !== undefined && args.wait_s !== null && args.wait_s !== ''
      ? Number(args.wait_s)
      : null
  const body = buildHeadlessBody(args, runId)
  const serverBudget = 60000 + Number(body.timeoutMs || 180000)
  const autoJobMs = Number(process.env.DSH_HEADLESS_AUTO_JOB_MS) || 0
  // ---- v0.9.3（D1/F3）：路线决策
  //   as_job=true                → 直接后台化（可选 wait_s 再等一会儿）
  //   serverBudget >= autoJobMs  → 同上（DSH_HEADLESS_AUTO_JOB_MS 显式打开时）
  //   否则                        → 先同步等，窗口到点就回 promoted 回执（结果永不丢）
  const wantJobFirst = asJob || (autoJobMs > 0 && serverBudget >= autoJobMs)
  if (wantJobFirst) return runAsJob(port, body, waitS, asJob, serverBudget, autoJobMs)
  return runSync(ctx, args, port, body, runId, serverBudget)
}

function validateFileArgs(args: Record<string, unknown>): unknown {
  // F-09：工具端必须自己拦 ALIAS / 空串 —— 旧写法在 body 组装前
  // 就把 falsy 剥成 undefined，引擎侧守卫对工具路径永不触发。
  const aliasKeys = detectFileAlias(args)
  if (aliasKeys.length)
    return toolError(
      'invalid_params',
      'blender_rt_headless 没有参数 ' +
        aliasKeys.join(', ') +
        '；打开 .blend 请用 file=。（收到的键：' +
        Object.keys(args || {}).join(', ') +
        '）',
      '用 file=<.blend 路径> 打开场景；参数名是 file，不是 blend/path',
    )
  const rawFile = args ? args.file : undefined
  if (typeof rawFile === 'string' && !rawFile.trim())
    return toolError(
      'invalid_params',
      'file= 传了空字符串 —— 本次不会打开任何 .blend（脚本会拿到出厂空场景）',
      '给真实 .blend 路径，或不要传 file=',
    )
  return null
}

function buildHeadlessBody(args: Record<string, unknown>, runId: string): Record<string, unknown> {
  const rawFile = args ? args.file : undefined
  const body: Record<string, unknown> = {
    script: String(args.script || ''),
    file: rawFile === undefined || rawFile === null ? undefined : rawFile,
    outdir: args.outdir || undefined,
    timeoutMs: args.timeout_ms ? Number(args.timeout_ms) : undefined,
    factoryStartup: args.factory_startup !== false,
    bootstrap: args.bootstrap !== false,
    preload: args.preload || undefined,
    gpu: args.gpu || undefined,
    engine: args.engine || undefined,
    useUserConfig: !!args.use_user_config,
    includeNoise: !!args.include_noise,
    expect: args.expect && typeof args.expect === 'object' ? args.expect : undefined,
    workdir: args.workdir || undefined,
    scriptFile: args.script_file || undefined,
    env: args.env && typeof args.env === 'object' ? args.env : undefined,
    outJson: args.out_json || undefined,
    shots: args.shots || undefined,
    hardExit: args && typeof args.hard_exit === 'boolean' ? args.hard_exit : undefined,
    runId: runId, // v0.9.3（D1）：客户端生成 → 窗口到点时立刻有可用句柄
  }
  if (args.args) body.args = splitArgs(args.args)
  return body
}

async function runSync(
  ctx: HostContext,
  args: Record<string, unknown>,
  port: number,
  body: Record<string, unknown>,
  runId: string,
  serverBudget: number,
): Promise<unknown> {
  let res: Record<string, unknown> | null = null
  try {
    // 预算 = 等待窗口 + 2 s 余量：到点就转 promoted 回执
    res = await backendPost(
      port,
      '/headless',
      body,
      Math.min(serverBudget, HEADLESS_WAIT_MS + 2000),
    )
  } catch (e: unknown) {
    // v0.9.3（D1）：客户端窗口到点 / 宿主 exec.signal 取消 —— 服务端子进程仍在跑
    return await promotedFromTimeout(port, runId, e)
  }
  const lt = leasedText(res)
  if (lt) return { text: lt }
  if (!res || res.error)
    return toolError(
      'backend_error',
      'HEADLESS 失败 · ' +
        String((res && (res.error || res.raw)) || 'unknown') +
        (res && res.hint ? '\n' + String(res.hint) : ''),
      '检查 Blender 路径/Dsh 后端；详情见 blender_viewport op=doctor 与 headless 日志',
    )
  // 后端 /headless 的信封是 {ok, ms, result:<引擎回执>} —— 结构化字段在 result 里
  const bodyRes = res && res.result && typeof res.result === 'object' ? asRecord(res.result) : null
  // 注意：**不能**拿 "resultJson 为空" 当缺字段 —— 脚本末行不是 JSON 时 resultJson 本来就该是 null（D3）。
  if (
    !bodyRes ||
    (!bodyRes.status &&
      bodyRes.result === undefined &&
      bodyRes.resultJson === undefined &&
      !bodyRes.shots)
  )
    return missingResultText(res, body)
  const out = syncReceipt(bodyRes, res)
  if (args && args.attach_images) out.images = await attachShots(ctx, out, args)
  return out
}
