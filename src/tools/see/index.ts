/** blender_see：GUI 视点捕获（viewpoint=viewport|camera|custom，mode=frame|sequence）。 */
import { vTool } from '../../contracts/tool.js'
import type { HostContext } from '../../host/context.js'
import { ANY_SCHEMA } from '../schema-diagnostic.js'
import { T } from '../../constants.js'
import { renderOne } from '../../ui/cards.js'
import { requireReadySession } from '../shared/session.js'
import { executeSee } from './execute.js'

function buildSeeTool(ctx: HostContext, port: number) {
  return vTool({
    name: 'blender_see',
    description:
      '【看 GUI】只服务 GUI 会话：viewpoint=viewport（当前视口）/ camera（当前帧相机）/ custom（from+look_at 自定义机位），mode=frame（默认单帧）或 sequence（固定机位序列帧，只 viewport/custom）。' +
      '固定档位 aspect=1:1（544/768/1024/1280）、4:3（640/960/1280）、16:9（1024/1152/1280），默认 16:9+1024；只给 aspect 取该比例最小档；非法值直接报错不夹取。' +
      'camera 只取当前帧求值（不改 scene.camera / 帧 / 选择 / 用户视口）；PANO 不支持、DOF/运动模糊只给 preview_only。' +
      'sequence 不执行 code、不改场景、失败不回落无头 worker。headless 渲染请用 blender_run(kind=render)。',
    parameters: {
      viewpoint: {
        type: 'string',
        description:
          'viewport（默认，当前 GUI 视口）| camera（当前帧相机）| custom（from+look_at）',
      },
      mode: { type: 'string', description: 'frame（默认，单帧）| sequence（固定机位序列帧）' },
      camera: {
        oneOf: [{ type: 'boolean' }, { type: 'string' }],
        description:
          'viewpoint=camera 时可选相机名；省略或 true = scene.camera。其他 viewpoint 不接受',
      },
      aspect: {
        type: 'string',
        description: '1:1 | 4:3 | 16:9（默认 16:9）。viewport/custom/sequence 用；camera 不接受',
      },
      max_size: {
        type: 'integer',
        description:
          '固定档位：1:1 544/768/1024/1280；4:3 640/960/1280；16:9 1024/1152/1280；camera 544/768/1024/1280。默认 1024',
      },
      from: { type: 'string', description: '【custom】相机位置 "x,y,z"（世界坐标）' },
      look_at: { type: 'string', description: '【custom】看向的点 "x,y,z"' },
      lens: { type: 'number', description: '【custom】焦距 mm，默认 50' },
      ortho: { type: 'boolean', description: '【custom】正交投影（配合 ortho_scale）' },
      ortho_scale: { type: 'number', description: '【custom】正交尺度（贴长边），默认 10' },
      full: {
        type: 'boolean',
        description: 'frame+viewport：整窗口/区域截图（固定 1024 长边，看 Blender UI 时用）',
      },
      area: { type: 'integer', description: 'full=true 时的区域序号，默认 0（第 1 个区域）' },
      shading: {
        type: 'string',
        description: '【custom/sequence】临时着色 WIREFRAME/SOLID/MATERIAL/RENDERED，出图后还原',
      },
      overlays: { type: 'boolean', description: '【custom/sequence】临时开关覆盖物，出图后还原' },
      diagnostics: {
        type: 'boolean',
        description: '【frame+custom】强制跑覆盖率/空帧诊断（默认按需）',
      },
      seconds: { type: 'number', description: '【sequence】采样时长，0.2–20 秒，默认 2' },
      fps: {
        type: 'number',
        description: '【sequence】采样频率，0.5–8，默认 4；帧数 round(seconds*fps) ≤32',
      },
      force: { type: 'boolean', description: '同画面默认不重复附图；true 强制重发' },
      session_id: {
        type: 'string',
        description: '目标 GUI 会话 id；多实例必填，blender_session action=list 可看',
      },
    },
    output: { schema: ANY_SCHEMA, render: renderOne },
    isConcurrencySafe: () => true,
    timeoutMs: T.watch,
    async execute(args: Record<string, unknown>) {
      const ready = await requireReadySession(port, undefined, String(args.session_id || '').trim())
      if (!ready.ok) return ready.failure
      return executeSee(ctx, port, args, ready.session)
    },
  })
}

export function registerSee(ctx: HostContext, port: number): void {
  ctx.effect(() => ctx.tools.register(buildSeeTool(ctx, port)), 'dsh-blender-plugin: see')
}
