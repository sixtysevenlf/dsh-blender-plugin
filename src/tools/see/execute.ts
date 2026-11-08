/**
 * blender_see：GUI 视点捕获（frame 单帧 / sequence 序列帧）。
 *
 * 规则来自 tool-surface spec：viewport / camera / custom 三视角，固定档位
 * 1:1、4:3、16:9；camera 只 frame；sequence 只 viewport/custom 且不执行 code、
 * 不改场景、失败不回落 worker。headless 渲染归 blender_run(kind=render)。
 */
import { BACKEND_HINT } from '../../constants.js'
import type { HostContext } from '../../host/context.js'
import { ensureBackend } from '../../host/backend/index.js'
import { toolError } from '../errors.js'
import { sessionIdOf } from '../shared/session.js'
import { captureCamera, captureFull, captureView } from './capture.js'
import { executeSequence } from './sequence.js'
import { validate, type FramePlan, type SequencePlan } from './validate.js'

function sequenceOptions(plan: SequencePlan): Parameters<typeof executeSequence>[2] {
  return {
    viewpoint: plan.viewpoint,
    from: plan.from,
    lookAt: plan.lookAt,
    lens: plan.lens,
    ortho: plan.ortho,
    orthoScale: plan.orthoScale,
    shading: plan.shading,
    overlays: plan.overlays,
    width: plan.dims.width,
    height: plan.dims.height,
    seconds: plan.seconds,
    fps: plan.fps,
    force: plan.force,
  }
}

async function captureFrame(
  ctx: HostContext,
  port: number,
  plan: FramePlan,
  args: Record<string, unknown>,
  sessionId?: string,
): Promise<unknown> {
  if (plan.viewpoint === 'camera') return captureCamera(ctx, port, plan, args.max_size, sessionId)
  if (plan.full) return captureFull(ctx, port, plan, sessionId)
  return captureView(ctx, port, plan, sessionId)
}

export async function executeSee(
  ctx: HostContext,
  port: number,
  args: Record<string, unknown>,
  session: Record<string, unknown>,
): Promise<unknown> {
  if (!(await ensureBackend(port)))
    return toolError(
      'gateway_unreachable',
      'Gateway 不可用（127.0.0.1:' + String(port) + '）',
      BACKEND_HINT,
    )
  const plan = validate(args)
  if ('text' in plan && 'envelope' in plan) return plan
  if (plan.kind === 'sequence')
    return executeSequence(ctx, port, sequenceOptions(plan as SequencePlan))
  return captureFrame(ctx, port, plan as FramePlan, args, sessionIdOf(session))
}
