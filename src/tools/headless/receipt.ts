/** blender_rt_headless 回执层：作业化/同步回执/缺字段诊断/shots 附图（行为与 v2.6.2 一致）。 */
import type { HostContext } from '../../host/context.js'
import { attachPaths } from '../../utils/image.js'
import {
  headlessReceipt,
  promotedReceipt,
  startJobThenMaybeWait,
  type ToolReceipt,
} from '../../headless/receipts/index.js'
import { asArray, asRecord, text } from '../../contracts/dynamic.js'

const LF = String.fromCharCode(10)

export async function runAsJob(
  port: number,
  body: Record<string, unknown>,
  waitS: number | null,
  asJob: boolean,
  serverBudget: number,
  autoJobMs: number,
): Promise<unknown> {
  const r2 = await startJobThenMaybeWait(port, body, waitS)
  if (r2.text) return { text: text(r2.text) }
  const why = asJob
    ? 'as_job=true'
    : '预计预算 ' +
      String(Math.round(serverBudget / 1000)) +
      's ≥ DSH_HEADLESS_AUTO_JOB_MS=' +
      String(autoJobMs) +
      'ms'
  if (r2.finished && r2.job)
    return headlessReceipt(r2.job, {
      mode: 'job',
      jobId: text(r2.jobId),
      notes: ['已后台化并在等待窗口内完成（' + why + '）—— 这就是完整结果，不必再 collect'],
    })
  return promotedReceipt(
    r2.job,
    text(r2.jobId),
    why + '：已后台化，等待窗口内没跑完（**不是失败**）',
  )
}

export function missingResultText(
  res: Record<string, unknown>,
  body: Record<string, unknown>,
): { text: string } {
  const keys = Object.keys(res || {}).filter((k) => k.indexOf('__') !== 0)
  const raw = String((res && res.raw) || '').slice(0, 2000)
  const lines = [
    'HEADLESS 结果缺字段（不是"失败"）· HTTP ' +
      String(res.__status) +
      ' · 后端回的键：' +
      (keys.length ? keys.join(', ') : '(空)'),
  ]
  if (Number(res.__heartbeats) > 0)
    lines.push('（流式回执：' + String(res.__heartbeats) + ' 条心跳，但没取到结果行）')
  if (raw) lines.push('--- 原始响应（前 2KB）---' + LF + raw)
  if (body.script)
    lines.push(
      '脚本内容长度=' + String(String(body.script).length) + ' 字符（内容见后端临时脚本目录）',
    )
  lines.push('预期日志：' + String(body.outdir || '(默认工作目录)') + '\\dsh_headless_*.stdout.log')
  lines.push(
    '跟进：blender_viewport(op="status") 看 runs 台账（v0.8.10 起每次无头运行都有 id/pid/日志/产物）。',
  )
  return { text: lines.join(LF) }
}

export function syncReceipt(
  bodyRes: Record<string, unknown>,
  res: Record<string, unknown>,
): ToolReceipt {
  const out = headlessReceipt(bodyRes, { mode: 'sync', jobId: null, notes: [] })
  // v0.8.9 边界提示：本次走了流式回执（>15 s，心跳 N 条）
  const beats = Number((res && res.__heartbeats) || 0)
  if (beats > 0)
    out.text += LF + '（本次耗时 > 15 s，后端走了流式回执：' + String(beats) + ' 条心跳后收到结果）'
  return out
}

export async function attachShots(
  ctx: HostContext,
  out: ToolReceipt,
  args: Record<string, unknown>,
): Promise<unknown[]> {
  const shots = asRecord(out.envelope.shots)
  const rows = asArray(shots.rows)
  const paths = rows.map((x) => text(asRecord(x).path)).filter(Boolean)
  const wantSheet = String(args.attach_images) === 'sheet'
  const picked = wantSheet ? [] : paths.filter((p: string) => !/sheet|montage/i.test(p)).slice(0, 3)
  const att = await attachPaths(ctx, picked, 'headless-shot', wantSheet ? 1 : 3)
  if (att.failed.length) out.text += LF + '⚠ 附图失败：' + att.failed.slice(0, 2).join(' · ')
  if (!att.images.length)
    out.text +=
      LF + '（attach_images 已开，但没有可附的图或读取失败；用 read_image(path) 看 shots 路径）'
  return att.images
}
