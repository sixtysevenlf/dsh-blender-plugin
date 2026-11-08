/** blender_op action=plan 的回执收尾：receipt.v2 信封 + 产物附图。 */
import type { HostContext } from '../../host/context.js'
import { attachPaths, planImagePaths } from '../../utils/image.js'
import { asRecord, isRecord, text } from '../../contracts/dynamic.js'

/** plan 回执统一收尾：信封 + 附图（调用方已经拿到 op 结果）。 */
export async function finishPlanResponse(
  ctx: HostContext,
  op: string,
  res: unknown,
  argsIn: unknown,
): Promise<Record<string, unknown>> {
  const txt = typeof res === 'string' ? res : String(JSON.stringify(res, null, 1) ?? '')
  const CAP = 12000
  const env = planEnvelope(op, res)
  const args = asRecord(argsIn)
  const attachWanted =
    args.attach_images !== undefined
      ? !!args.attach_images
      : [
          'img_digest',
          'qc_compare',
          'visualreport_report',
          'qc_visual_report',
          'shapesolve_solve',
        ].indexOf(op) >= 0
  let images: unknown[] = []
  let attachNote = ''
  if (attachWanted) {
    const att = await attachPaths(
      ctx,
      planImagePaths(res),
      'plan-' + op.replace(/[^a-z0-9_]+/gi, '_'),
      2,
    )
    images = att.images
    if (att.used.length)
      attachNote =
        String.fromCharCode(10) +
        '附图：' +
        String(att.used.length) +
        ' 张（' +
        att.used.join(' · ') +
        '）'
    if (att.failed.length)
      attachNote += String.fromCharCode(10) + '⚠ 部分图未附：' + att.failed.slice(0, 2).join(' · ')
  }
  const head =
    'OP ' +
    op +
    ' ok · ' +
    String(txt.length) +
    ' chars · verdict=' +
    text(env.verdict) +
    (env.summary ? ' · ' + text(env.summary) : '') +
    attachNote +
    String.fromCharCode(10)
  return {
    text:
      head +
      (txt.length > CAP
        ? txt.slice(0, CAP) +
          String.fromCharCode(10) +
          '…[已截断，共 ' +
          String(txt.length) +
          ' 字符；结构化全量见后端 results/ 目录或用 blender_run(kind=headless, out_json=…) 落盘]'
        : txt),
    envelope: env,
    ...(images.length ? { images: images } : {}),
  }
}

/** 弱模型契约 v2：verdict-first 信封（单行、≤2k）——plan 与 headless 共用口径 */
export function planEnvelope(op: string, res: unknown): Record<string, unknown> {
  const r = isRecord(res) ? res : { value: res }
  const verdict =
    r.verdict ||
    r.state ||
    (r.ok === false ? 'fail' : r.ok === true ? (r.clean === false ? 'degraded' : 'pass') : null)
  let defects: unknown[] = []
  for (const k of ['top_defects', 'offenders', 'defects', 'failures', 'errors']) {
    if (Array.isArray(r[k])) {
      defects = r[k] as unknown[]
      break
    }
  }
  const limit = (t: unknown, n: number) => text(t).slice(0, n)
  const top = defects.slice(0, 5).map((d, i) => {
    if (!isRecord(d)) return { id: 'D' + String(i + 1), code: limit(d, 80) }
    return {
      id: d.id || 'D' + String(i + 1),
      code: limit(d.code || d.kind || d.class || d.error || 'issue', 60),
      severity: d.severity || null,
      object: d.object || d.a || d.name || null,
      measured:
        d.measured ||
        (d.volume_mm3 !== undefined
          ? { volume_mm3: d.volume_mm3 }
          : d.depth_mm !== undefined
            ? { depth_mm: d.depth_mm }
            : null),
      fix_hint: d.fix_hint || d.repair || null,
    }
  })
  const next = (Array.isArray(r.next) ? r.next : Array.isArray(r.repairs) ? r.repairs : [])
    .slice(0, 3)
    .map((x: unknown) =>
      typeof x === 'string'
        ? { op: limit(x, 120) }
        : {
            op: limit(asRecord(x).op || asRecord(x).name || 'next', 80),
            args: asRecord(x).args || null,
            why: limit(asRecord(x).why || '', 120),
          },
    )
  const env: Record<string, unknown> = {
    contract: 'receipt.v2',
    op: op,
    verdict: verdict,
    summary: limit(r.summary || r.note || r.error || r.hint || '', 160),
    top_defects: top,
    next: next,
    images: planImagePaths(r).slice(0, 2),
    provenance: { ms: typeof r.ms === 'number' ? r.ms : null },
  }
  const counts = r.counts || (r.totals ? r.totals : null)
  if (isRecord(counts)) env.counts = counts
  if (isRecord(r.detail))
    env.detail = {
      path: r.detail.path || null,
      bytes: r.detail.bytes || null,
      id: r.detail.id || null,
    }
  // 信封硬预算：超过 2k 就只保留最有用的三块（verdict / top3 / next2）
  try {
    if (JSON.stringify(env).length > 2000) {
      env.top_defects = top.slice(0, 3)
      env.next = next.slice(0, 2)
      delete env.counts
      const detail = asRecord(env.detail)
      if (detail.bytes && Number(detail.bytes) > 0) {
        /* keep path only */
      } else delete env.detail
    }
  } catch {
    /* ignore */
  }
  return env
}
