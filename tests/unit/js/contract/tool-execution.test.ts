import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'
import * as plugin from '../../../../src/index'
import { createFakeContext, type CapturedTool } from '../../../helpers/fake-context'
import { startFakeBackend } from '../../../helpers/fake-backend.mjs'

let backend: Awaited<ReturnType<typeof startFakeBackend>>
let fake: ReturnType<typeof createFakeContext>
let tools: CapturedTool[]

function tool(name: string): CapturedTool {
  const t = tools.find((x) => x.name === name)
  if (!t) throw new Error('missing tool ' + name)
  return t
}

async function run(name: string, args: unknown): Promise<any> {
  return await (tool(name).execute as any)(args, {})
}

function requestsSince(mark: number): any[] {
  return backend.requests.slice(mark)
}

function requestSince(mark: number, method: string, path: string): any | undefined {
  return requestsSince(mark).find((r: any) => r.method === method && r.path === path)
}

/** No visible GUI session: the decision-required path, never a spawn. */
function hideSessions(): void {
  backend.setHandler('POST /lease', () => ({
    status: 200,
    headers: { 'content-type': 'application/json' },
    body: Buffer.from(JSON.stringify({ ok: true, sessions: [] })),
  }))
}

const QUEUED_JOB = {
  id: 'job-1',
  kind: 'render',
  state: 'queued',
  slot: 'gpu',
  priority: 0,
  createdAt: Date.now(),
  updatedAt: Date.now(),
  owner: { hostId: 'host-1', dshSessionId: 'ds-1', agentId: 'main' },
}

beforeAll(async () => {
  backend = await startFakeBackend()
  fake = createFakeContext()
  const attachments = {
    imageLimits: { mediaTypes: ['image/png'] },
    async saveImage({ data, name }: { data: Uint8Array; name: string }) {
      return {
        attachmentId: 'att-' + name,
        mediaType: 'image/png',
        bytes: data.length,
        width: 1,
        height: 1,
        name,
      }
    },
  }
  fake.ctx.get = ((name: string) => (name === 'attachments' ? attachments : undefined)) as any
  plugin.apply(fake.ctx, { autoStart: false, tui: false, port: backend.port })
  tools = fake.tools
})

afterAll(async () => {
  fake.dispose()
  await backend.close()
})

afterEach(() => {
  backend.setHandler('POST /lease')
})

function twoReadySessions() {
  return {
    status: 200,
    headers: { 'content-type': 'application/json' },
    body: Buffer.from(
      JSON.stringify({
        ok: true,
        sessions: [
          {
            id: 'sess-a',
            kind: 'user-gui',
            status: 'ready',
            instanceId: 'inst-a',
            owner: { hostId: 'host-1', dshSessionId: 'ds-1', agentId: 'main' },
            agents: [],
          },
          {
            id: 'sess-b',
            kind: 'user-gui',
            status: 'ready',
            instanceId: 'inst-b',
            owner: { hostId: 'host-1', dshSessionId: 'ds-1', agentId: 'main' },
            agents: [],
          },
        ],
      }),
    ),
  }
}

describe('model tool execution semantics (six-tool surface)', () => {
  it('blender_session lists sessions through the session route', async () => {
    const mark = backend.requests.length
    const out = await run('blender_session', { action: 'list' })
    expect(out.text).toContain('SESSION list · 1 个会话')
    expect(out.text).toContain('sess-1')
    expect(requestSince(mark, 'POST', '/lease')?.body.action).toBe('list')
  })

  it('refuses a virtual-gui start before touching the backend unless the host allowed it', async () => {
    const mark = backend.requests.length
    const out = await run('blender_session', { action: 'start', kind: 'virtual-gui' })
    expect(out.envelope.error.code).toBe('virtual_gui_not_allowed')
    expect(requestsSince(mark)).toEqual([])
  })

  it('blender_session attaches with an explicit instance id', async () => {
    const mark = backend.requests.length
    const out = await run('blender_session', { action: 'attach', instance_id: 'inst-1' })
    expect(out.text).toContain('SESSION attach ok')
    expect(out.text).toContain('session=sess-1')
    const req = requestSince(mark, 'POST', '/lease')
    expect(req?.body).toMatchObject({ action: 'attach', instanceId: 'inst-1' })
  })

  it('blender_session action=txn keeps the txn route and renders its receipt', async () => {
    const mark = backend.requests.length
    const out = await run('blender_session', { action: 'txn', op: 'list' })
    expect(out.text).toContain('TXN list ok')
    expect(out.text).toContain('目录：/tmp/fake')
    const body = requestSince(mark, 'POST', '/txn')?.body
    expect(body.op).toBe('list')
    expect(body.write_scope).toBe('exclusive')
  })

  it('blender_op plan catalog runs without a GUI session', async () => {
    const mark = backend.requests.length
    const out = await run('blender_op', { action: 'plan', op: 'catalog' })
    expect(out.text).toContain('OP catalog ok')
    expect(out.envelope?.verdict).toBe('pass')
    expect(requestSince(mark, 'POST', '/plan')?.body.op).toBe('catalog')
    expect(
      requestsSince(mark).some((r: any) => r.path === '/lease' && r.body?.action === 'list'),
    ).toBe(false)
  })

  it('blender_op dry_run validates a write op without a session or write scope', async () => {
    hideSessions()
    const mark = backend.requests.length
    const out = await run('blender_op', {
      action: 'plan',
      op: 'gate_deliver',
      args: { dry_run: true },
    })
    expect(out.text).toContain('OP gate_deliver ok')
    const body = requestSince(mark, 'POST', '/plan')?.body
    expect(body.op).toBe('gate_deliver')
    expect(body.write_scope).toBeUndefined()
    expect(body.sessionId).toBeUndefined()
    expect(
      requestsSince(mark).some((r: any) => r.path === '/lease' && r.body?.action === 'list'),
    ).toBe(false)
    backend.setHandler('POST /lease')
  })

  it('blender_op rejects an executing op without a ready session', async () => {
    hideSessions()
    const out = await run('blender_op', { action: 'plan', op: 'gate_deliver' })
    expect(out.text).toContain('Blender 未附着')
    expect(out.text).toContain('session_required')
    expect(out.envelope?.stage).toBe('session')
    backend.setHandler('POST /lease')
  })

  it('blender_op routes a write op to the named session with its declared scope', async () => {
    backend.setHandler('POST /lease', twoReadySessions)
    const mark = backend.requests.length
    const out = await run('blender_op', {
      action: 'plan',
      op: 'gate_deliver',
      args: { dry_run: false },
      session_id: 'sess-b',
      write_scope: 'collection:COL_Lights',
    })
    expect(out.text).toContain('OP gate_deliver ok')
    expect(requestSince(mark, 'POST', '/plan')?.body).toMatchObject({
      op: 'gate_deliver',
      sessionId: 'sess-b',
      write_scope: 'collection:COL_Lights',
    })
  })

  it('blender_op never guesses between two ready sessions for a write op', async () => {
    backend.setHandler('POST /lease', twoReadySessions)
    const mark = backend.requests.length
    const out = await run('blender_op', { action: 'plan', op: 'gate_deliver' })
    expect(out.text).toContain('session_ambiguous')
    expect(requestsSince(mark).some((r: any) => r.path === '/plan')).toBe(false)
  })

  it('blender_py executes code in the attached GUI session and returns a frame', async () => {
    const mark = backend.requests.length
    const out = await run('blender_py', { code: 'print(1)' })
    expect(out.text).toContain('PY ok')
    expect(out.text).toContain('fake stdout')
    expect(out.image?.attachmentId).toContain('py-see-')
    expect(requestSince(mark, 'POST', '/act')?.body).toMatchObject({
      code: 'print(1)',
      py: true,
      sessionId: 'sess-1',
    })
  })

  it('blender_py refuses to run without a session and never starts headless', async () => {
    hideSessions()
    const mark = backend.requests.length
    const t0 = Date.now()
    const out = await run('blender_py', { code: 'x = 1' })
    expect(Date.now() - t0).toBeLessThan(3000)
    expect(out.text).toContain('Blender 未附着')
    expect(out.text).toContain('session_required')
    expect(out.text).toContain('blender_session action=attach')
    expect(out.text).toContain('virtual-gui')
    expect(requestsSince(mark).some((r: any) => r.path === '/headless' || r.path === '/act')).toBe(
      false,
    )
    backend.setHandler('POST /lease')
  })

  it('blender_py refuses to pick one of several ready sessions', async () => {
    backend.setHandler('POST /lease', twoReadySessions)
    const mark = backend.requests.length
    const out = await run('blender_py', { code: 'print(1)' })
    expect(out.text).toContain('session_ambiguous')
    expect(out.text).toContain('sess-a')
    expect(out.text).toContain('sess-b')
    expect(requestsSince(mark).some((r: any) => r.path === '/act')).toBe(false)
  })

  it('blender_py routes to an explicitly named session instance', async () => {
    backend.setHandler('POST /lease', twoReadySessions)
    const mark = backend.requests.length
    const out = await run('blender_py', { code: 'print(1)', session_id: 'sess-b' })
    expect(out.text).toContain('PY ok')
    expect(requestSince(mark, 'POST', '/act')?.body).toMatchObject({
      instanceId: 'inst-b',
      sessionId: 'sess-b',
    })
  })

  it('blender_see captures a viewport frame at a fixed tier', async () => {
    const mark = backend.requests.length
    const out = await run('blender_see', {})
    expect(out.text).toContain('SEE viewport · 1024x576')
    expect(out.image?.attachmentId).toContain('see-viewport-')
    expect(requestSince(mark, 'POST', '/view')?.body).toMatchObject({
      mode: 'current',
      width: 1024,
      height: 576,
    })
  })

  it('blender_see captures the evaluated camera without moving it', async () => {
    const mark = backend.requests.length
    const out = await run('blender_see', { viewpoint: 'camera', force: true })
    expect(out.text).toContain('SEE camera')
    expect(out.text).toContain('camera=Camera')
    expect(out.text).toContain('frame=1')
    expect(out.text).toContain('未改 scene.camera')
    expect(requestSince(mark, 'POST', '/view')?.body.mode).toBe('camera')
  })

  it('blender_see samples a short sequence of viewport frames', async () => {
    const out = await run('blender_see', {
      mode: 'sequence',
      viewpoint: 'viewport',
      seconds: 0.2,
      fps: 5,
    })
    expect(out.text).toContain('SEE sequence(viewport) · 0.2s @5fps → 1 帧')
    expect(out.text).toContain('timeline')
  })

  it('blender_see accepts camera=true as the current scene camera', async () => {
    const mark = backend.requests.length
    const out = await run('blender_see', { viewpoint: 'camera', camera: true })
    expect(out.text).toContain('SEE camera')
    expect(requestSince(mark, 'POST', '/view')?.body.mode).toBe('camera')
  })

  it('blender_see publishes the first sequence frame once as a fixed pose', async () => {
    backend.setHandler('POST /lease', (body: any) => {
      if (body.action === 'list')
        return {
          status: 200,
          headers: { 'content-type': 'application/json' },
          body: Buffer.from(
            JSON.stringify({
              ok: true,
              sessions: [
                {
                  id: 'sess-1',
                  kind: 'user-gui',
                  status: 'ready',
                  instanceId: 'inst-1',
                  owner: null,
                  agents: [],
                },
              ],
            }),
          ),
        }
      return {
        status: 200,
        headers: { 'content-type': 'application/json' },
        body: Buffer.from(JSON.stringify({ ok: true, action: body.action })),
      }
    })
    const mark = backend.requests.length
    const out = await run('blender_see', {
      mode: 'sequence',
      viewpoint: 'viewport',
      seconds: 0.2,
      fps: 5,
    })
    expect(out.text).toContain('SEE sequence(viewport)')
    const published = requestsSince(mark).filter(
      (r: any) => r.path === '/lease' && r.body?.action === 'update_view',
    )
    expect(published).toHaveLength(1)
    expect(published[0].body.sessionId).toBe('sess-1')
    expect(published[0].body.view).toMatchObject({ viewpoint: 'viewport' })
    backend.setHandler('POST /lease')
  })

  it('blender_see rejects removed and ambiguous parameters before capturing', async () => {
    const viewSize = await run('blender_see', { view_size: '1280x720' })
    expect(viewSize.text).toContain('view_size')
    expect(viewSize.text).toContain('invalid_params')
    const cameraFrom = await run('blender_see', { viewpoint: 'camera', from: '1,2,3' })
    expect(cameraFrom.text).toContain('camera')
    expect(cameraFrom.text).toContain('invalid_params')
    const seq = await run('blender_see', {
      mode: 'sequence',
      viewpoint: 'camera',
      seconds: 2,
      fps: 4,
    })
    expect(seq.text).toContain('sequence 只支持 viewpoint=viewport 或 custom')
    expect(seq.text).toContain('invalid_params')
    const inferred = await run('blender_see', {
      mode: 'sequence',
      from: '1,2,3',
      look_at: '0,0,0',
      seconds: 2,
      fps: 4,
    })
    expect(inferred.text).toContain('必须显式给 viewpoint')
    expect(inferred.text).toContain('invalid_params')
    const viewportFrom = await run('blender_see', { viewpoint: 'viewport', from: '1,2,3' })
    expect(viewportFrom.text).toContain('不接受 from/look_at')
    expect(viewportFrom.text).toContain('invalid_params')
    const seqForce = await run('blender_see', {
      mode: 'sequence',
      viewpoint: 'viewport',
      force: true,
      seconds: 2,
      fps: 4,
    })
    expect(seqForce.text).toContain('sequence 不支持 force')
    expect(seqForce.text).toContain('invalid_params')
  })

  it('blender_see keeps sequence frames as attachment refs and rejects a fixed viewpoint', async () => {
    const bad = await run('blender_see', {
      mode: 'sequence',
      viewpoint: 'viewport',
      from: '1,2,3',
      look_at: '0,0,0',
      seconds: 0.2,
      fps: 5,
    })
    expect(bad.text).toContain('不接受 from/look_at')
    expect(bad.text).toContain('invalid_params')

    // Explicit false must read as "off", not as a present field.
    const ok = await run('blender_see', {
      mode: 'sequence',
      viewpoint: 'viewport',
      full: false,
      seconds: 0.2,
      fps: 5,
    })
    expect(ok.text).toContain('SEE sequence(viewport)')
    expect(Array.isArray(ok.images)).toBe(true)
    expect(ok.images[0].attachmentId).toContain('see-seq-')
  })

  it('blender_see treats camera=false as the scene camera, not a camera named false', async () => {
    const mark = backend.requests.length
    const out = await run('blender_see', { viewpoint: 'camera', camera: false })
    expect(out.text).toContain('SEE camera')
    const body = requestSince(mark, 'POST', '/view')?.body
    expect(String(body?.camera ?? '')).not.toBe('false')
  })

  it('blender_run submits a declarative render job to the queue', async () => {
    backend.setHandler('POST /job', () => ({
      status: 200,
      headers: { 'content-type': 'application/json' },
      body: Buffer.from(JSON.stringify({ ok: true, op: 'submit', job: QUEUED_JOB })),
    }))
    const mark = backend.requests.length
    const out = await run('blender_run', {
      action: 'submit',
      kind: 'render',
      file: '',
      frames: [1, 2],
    })
    expect(out.text).toContain('RUN job-1 · render · queued')
    expect(out.text).toContain('next  blender_run action=wait id=job-1')
    const body = requestSince(mark, 'POST', '/job')?.body
    expect(body).toMatchObject({ surface: 'queue', op: 'submit', kind: 'render' })
    backend.setHandler('POST /job')
  })

  it('blender_run refuses script parameters on a render submit', async () => {
    const mark = backend.requests.length
    const out = await run('blender_run', {
      action: 'submit',
      kind: 'render',
      script: 'print(1)',
    })
    expect(out.text).toContain('render 不接受 script/script_file')
    expect(out.text).toContain('kind=headless')
    expect(requestsSince(mark).some((r: any) => r.path === '/job')).toBe(false)
  })

  it('blender_run requires a script or file for a headless submit', async () => {
    const out = await run('blender_run', { action: 'submit', kind: 'headless' })
    expect(out.text).toContain('headless 需要 script / script_file / file 之一')
  })

  it('blender_run lists queued jobs', async () => {
    backend.setHandler('POST /job', () => ({
      status: 200,
      headers: { 'content-type': 'application/json' },
      body: Buffer.from(JSON.stringify({ ok: true, jobs: [QUEUED_JOB] })),
    }))
    const out = await run('blender_run', { action: 'list' })
    expect(out.text).toContain('RUN list · 1 个作业')
    expect(out.text).toContain('job-1')
    backend.setHandler('POST /job')
  })

  it('blender_inspect help separates executable commands from gated ones', async () => {
    const out = await run('blender_inspect', {})
    expect(out.text).toContain('INSPECT help · 1 个可执行只读命令 · 1 个被 gate 关闭')
    expect(out.text).toContain('get_scene_info')
    expect(out.text).toContain('被 gate 关闭（enabled=false，当前不可执行）')
    expect(out.text).toContain('DISABLED(polyhaven)')
    expect(out.text).toContain('内部传输命令不列出')
    expect(out.text).not.toContain('get_viewport_screenshot')
  })

  it('blender_inspect executes one read command', async () => {
    const mark = backend.requests.length
    const out = await run('blender_inspect', { command: 'get_scene_info', args: { a: 1 } })
    expect(out.text).toContain('INSPECT get_scene_info ok')
    expect(requestSince(mark, 'POST', '/cmd')?.body).toMatchObject({
      name: 'get_scene_info',
      params: { a: 1 },
      sessionId: 'sess-1',
      instanceId: 'inst-1',
    })
  })

  it('blender_inspect routes a read command to the named session instance', async () => {
    backend.setHandler('POST /lease', twoReadySessions)
    const mark = backend.requests.length
    const out = await run('blender_inspect', {
      command: 'get_scene_info',
      session_id: 'sess-b',
    })
    expect(out.text).toContain('INSPECT get_scene_info ok')
    expect(requestSince(mark, 'POST', '/cmd')?.body).toMatchObject({
      sessionId: 'sess-b',
      instanceId: 'inst-b',
    })
  })

  it('blender_inspect routes legacy write commands and internals to the right tool', async () => {
    const write = await run('blender_inspect', { command: 'set_texture' })
    expect(write.text).toContain('wrong_tool')
    expect(write.text).toContain('blender_op')
    const internal = await run('blender_inspect', { command: 'execute_code' })
    expect(internal.text).toContain('wrong_tool')
    expect(internal.text).toContain('blender_py')
  })

  it('blender_inspect suggests a close command name for a typo', async () => {
    const out = await run('blender_inspect', { command: 'get_scene_inf' })
    expect(out.text).toContain('unknown_command')
    expect(out.text).toContain('did_you_mean')
    expect(out.text).toContain('get_scene_info')
  })

  it('blender_op action=loop keeps the internal loop route and GUI target', async () => {
    const mark = backend.requests.length
    const out = await run('blender_op', {
      action: 'loop',
      op: 'start',
      spec: { iterations: 3 },
      target: 'gui',
    })
    expect(out.text).toContain('LOOP start')
    expect(requestSince(mark, 'POST', '/loop')?.body).toMatchObject({
      op: 'start',
      spec: { iterations: 3 },
      sessionId: 'sess-1',
      instanceId: 'inst-1',
      write_scope: 'exclusive',
    })
  })

  it('blender_op action=opt and action=preset keep their routes', async () => {
    let mark = backend.requests.length
    const opt = await run('blender_op', { action: 'opt', op: 'analyze' })
    expect(opt.text).toContain('OPT analyze')
    expect(requestSince(mark, 'POST', '/opt')?.body).toMatchObject({
      op: 'opt_analyze',
      sessionId: 'sess-1',
      write_scope: 'exclusive',
    })
    mark = backend.requests.length
    const preset = await run('blender_op', {
      action: 'preset',
      op: 'list',
      session_id: 'sess-1',
      write_scope: 'prefix:LGT-',
    })
    expect(preset.text).toContain('PRESET list ok')
    expect(requestSince(mark, 'POST', '/preset')?.body).toMatchObject({
      op: 'list',
      sessionId: 'sess-1',
      write_scope: 'prefix:LGT-',
    })
  })

  it('blender_run submit keeps kind=headless and returns a job id immediately', async () => {
    const mark = backend.requests.length
    const out = await run('blender_run', {
      action: 'submit',
      kind: 'headless',
      script: 'print(\'HEADLESS {"a": 1}\')',
      outdir: '/tmp/out',
    })
    expect(out.text).toContain('RUN')
    const body = requestSince(mark, 'POST', '/job')?.body
    expect(body).toMatchObject({
      surface: 'queue',
      op: 'submit',
      kind: 'headless',
      outdir: '/tmp/out',
    })
    expect(body.script).toContain('HEADLESS')
  })

  it('blender_run tune uses the legacy perf route, not the queue', async () => {
    const mark = backend.requests.length
    const out = await run('blender_run', { action: 'tune', op: 'status' })
    expect(out.text).toContain('RUN tune status')
    expect(requestSince(mark, 'POST', '/perf')?.body.op).toBe('status')
    expect(requestSince(mark, 'POST', '/job')).toBeUndefined()
  })
})
