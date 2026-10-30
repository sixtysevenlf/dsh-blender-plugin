import { vTool } from '../contracts/tool.js'
import { ANY_SCHEMA } from './schema-diagnostic.js'
import { T } from '../constants.js'
import { ensureBackend } from '../host/backend.js'
import { backendPost, leasedText, splitArgs } from '../host/http.js'
import { renderStructured, clipText, safeStringify } from '../ui/cards.js'
import { headlessReceipt, promotedReceipt } from '../headless/receipts.js'
import { detectFileAlias } from '../../lib/runtime/file_guard.js'

export function registerJob(ctx: any, port: number): void {
  ctx.effect(
    () =>
      ctx.tools.register(
        vTool({
          name: 'blender_rt_job',
          description:
            '【作业层】长活后台化：op=start 立刻返回 jobId（不占客户端连接、不会被工具超时掐断）；status / collect / **wait** / kill / list。' +
            '**wait 一次拿到结构化结果（默认 120 s/次，上限 600 s）—— 别再连发 status**（会撞宿主的重复调用检测）。' +
            '与 headless 的分工：预期 <100 s 用 headless 直接拿结果；更长或已知要跑很久 → headless 的 as_job=true，或本工具 op=start。' +
            'run 与 job **同一 id 空间**：headless 的 runId（run-…）也能用 status/collect/wait/kill 收；日志落 <outdir>/jobs/<id>/。' +
            '参数细节见 blender_rt_plan(op="catalog", args={tool:"rt_job"})。',
          parameters: {
            op: { type: 'string', required: true, description: 'start | status | collect | wait | kill | list' },
            id: { type: 'string', description: 'status/collect/wait/kill 的 id（job-… 或 headless 的 run-…）' },
            script: { type: 'string', description: 'Python 源码（与 headless 同一套前导与结果契约）' },
            script_file: { type: 'string', description: '直接跑 .py 文件（Windows/WSL 路径都收）' },
            args: { type: 'string', description: '命令行参数（空格分隔或数组）；脚本里读 sys.argv / K.args' },
            env: { type: 'json', description: '额外环境变量 {KEY:"VALUE"}' },
            file: { type: 'string', description: '要打开的 .blend（回执带 inputFile{size,mtime,md5}）' },
            outdir: { type: 'string', description: '产物目录（WSL 路径也收）；日志在其 jobs/<id>/ 下' },
            timeout_ms: {
              type: 'integer',
              description: '作业上限，默认 3600000，上限 24 h；wait 时是本次等待上限（默认 120000，上限 600000）'
            },
            engine: { type: 'string', description: 'eevee（默认）/ cycles / keep' },
            preload: { type: 'string', description: '预载 runtime 模块（逗号分隔）→ K.dsh_*_api' },
            factory_startup: { type: 'boolean', description: '默认 true = --factory-startup' },
            bootstrap: { type: 'boolean', description: '默认 true = 注入持久内核 K' },
            workdir: { type: 'string', description: '脚本内 chdir + sys.path 首位' },
            include_noise: { type: 'boolean', description: '产物清单是否含噪音文件（默认 false 过滤）' },
            out_json: { type: 'string', description: '结构化结果落盘路径（>4KB 自动落 results/）' },
            tail: { type: 'integer', description: 'collect/wait 的 stdout/stderr 尾部字符数（默认 4000）' }
          },
          output: { schema: ANY_SCHEMA, render: renderStructured },
          isConcurrencySafe: () => true,
          timeoutMs: T.job,
          async execute(args: any) {
            if (!(await ensureBackend(port))) return { text: '后端不可用（127.0.0.1:' + String(port) + '）' }
            const op = String((args && args.op) || 'list')
            // F-09 一致性：op=start/run 的 file= 与 rt_headless 同一套拦截（引擎 job 路径已有 validateFileOpt 兜底）
            if (op === 'start' || op === 'run') {
              const aliasKeys = detectFileAlias(args)
              if (aliasKeys.length)
                return {
                  text:
                    'blender_rt_job 没有参数 ' +
                    aliasKeys.join(', ') +
                    '；打开 .blend 请用 file=。（收到的键：' +
                    Object.keys(args || {}).join(', ') +
                    '）'
                }
              if (args && typeof args.file === 'string' && !args.file.trim())
                return {
                  text:
                    'file= 传了空字符串 —— 本次不会打开任何 .blend' + '（脚本会拿到出厂空场景）；要么给真实 .blend 路径，要么就不要传 file='
                }
            }
            const body: any = { op: op }
            if (args && args.id) body.id = String(args.id)
            if (args && args.script !== undefined) body.script = String(args.script)
            if (args && args.script_file) body.scriptFile = String(args.script_file)
            if (args && args.args) body.args = splitArgs(args.args)
            if (args && args.env && typeof args.env === 'object') body.env = args.env
            if (args && args.file) body.file = String(args.file)
            if (args && args.outdir) body.outdir = String(args.outdir)
            if (args && args.engine) body.engine = String(args.engine)
            if (args && args.preload) body.preload = String(args.preload)
            if (args && args.workdir) body.workdir = String(args.workdir)
            if (args && args.out_json) body.outJson = String(args.out_json)
            if (args && args.include_noise !== undefined) body.includeNoise = !!args.include_noise
            if (args && args.factory_startup !== undefined) body.factoryStartup = !!args.factory_startup
            if (args && args.bootstrap !== undefined) body.bootstrap = !!args.bootstrap
            if (args && args.tail) body.tail = Number(args.tail)
            if (args && args.timeout_ms) body.timeoutMs = Number(args.timeout_ms)
            // v0.9.3（D6.2）：op=wait 会阻塞 → 给足 HTTP 预算（服务端默认 120 s/次，上限 600 s）
            const budget = op === 'wait' ? Math.min(600000, Number(args && args.timeout_ms) || 120000) + 30000 : 60000
            const r = await backendPost(port, '/job', body, budget)
            const lt = leasedText(r)
            if (lt) return { text: lt }
            if (!r || r.ok !== true) return { text: 'JOB ' + op + ' 失败 · ' + String((r && (r.error || r.raw)) || 'unknown') }
            if (op === 'list') {
              const js: any[] = (r && r.jobs) || []
              if (!js.length) return { text: '没有作业' }
              const lines = js.map(
                (j: any) =>
                  '  ' +
                  String(j.id) +
                  ' · ' +
                  String(j.status) +
                  (j.stageName ? ' · stage=' + String(j.stageName) : '') +
                  (typeof j.ms === 'number' ? ' · ' + String(Math.round(j.ms / 1000)) + 's' : '') +
                  (j.fromLedger ? ' · (磁盘台账)' : '') +
                  ' · 产物 ' +
                  String((j.artifacts || []).length) +
                  ' 项'
              )
              return { text: '作业（' + String(js.length) + '）：' + String.fromCharCode(10) + lines.join(String.fromCharCode(10)) }
            }
            const j: any = (r && r.job) || {}
            // ---- 统一回执：run/job 同一套字段（D2）
            const ctx2: any = {
              mode: 'job',
              jobId: String(j.id || '').indexOf('job-') === 0 ? j.id : null,
              runId: j.runId || null,
              notes: [],
              hint: null
            }
            if (op === 'start') {
              ctx2.notes.push('已后台化（pid ' + String(j.pid) + '，日志目录 ' + String(j.logDir || '') + '）')
              ctx2.hint = '用 op=wait id=' + String(j.id) + ' 阻塞等结果，或 op=collect 看进展（stage/idleMs）；op=kill 中止'
              const out0 = promotedReceipt(j, String(j.id), 'op=start 已接受')
              out0.envelope.mode = 'job-start'
              out0.envelope.notes = ctx2.notes
              out0.envelope.hint = ctx2.hint
              return out0
            }
            if (op === 'kill') {
              const t =
                j.alreadyFinished || j.status === 'unknown'
                  ? 'JOB kill · ' + String(j.id) + ' · ' + String(j.status) + '（' + String(j.note || '已结束/不存在，无需 kill') + '）'
                  : 'JOB kill · ' + String(j.id) + ' · status=' + String(j.status) + '（已发 SIGKILL）'
              return {
                text: t,
                envelope: {
                  kind: 'job',
                  mode: 'job-kill',
                  id: j.id || null,
                  status: j.status || null,
                  ok: true,
                  alreadyFinished: !!j.alreadyFinished,
                  note: j.note || null,
                  fromLedger: !!j.fromLedger
                }
              }
            }
            if (j.recovered) {
              return {
                text:
                  'JOB ' +
                  op +
                  ' · ' +
                  String(j.id || '') +
                  ' · status=' +
                  String(j.status) +
                  String.fromCharCode(10) +
                  '（已完成 · 磁盘产物对账 recovered from ' +
                  String(j.recoveredFrom || '?') +
                  '）' +
                  String.fromCharCode(10) +
                  String(j.hint || ''),
                envelope: {
                  kind: 'job',
                  mode: 'job-' + op,
                  id: j.id || null,
                  status: j.status || null,
                  ok: true,
                  recovered: true,
                  recoveredFrom: j.recoveredFrom || null,
                  recoveredEvidence: j.recoveredEvidence || null,
                  resultJson: j.resultJson || null,
                  resultPath: j.resultPath || null,
                  stdoutTail: clipText(j.stdoutTail, 1000, 1000)
                }
              }
            }
            if (j.status === 'stale' || j.fromLedger) {
              return {
                text:
                  'JOB ' +
                  op +
                  ' · ' +
                  String(j.id || '') +
                  ' · status=' +
                  String(j.status) +
                  String.fromCharCode(10) +
                  '（磁盘台账记录）' +
                  String(j.staleReason || '') +
                  String.fromCharCode(10) +
                  String(j.hint || ''),
                envelope: {
                  kind: 'ledger',
                  mode: 'job-' + op,
                  id: j.id || null,
                  status: j.status || null,
                  ok: true,
                  fromLedger: true,
                  staleReason: j.staleReason || null,
                  pid: j.pid || null,
                  pidAlive: !!j.pidAlive,
                  resultJson: j.resultJson || null,
                  resultPath: j.resultPath || null,
                  stdoutTail: clipText(j.stdoutTail, 1000, 1000)
                }
              }
            }
            if (op === 'collect' && j.status === 'running') {
              ctx2.notes.push(
                '任务仍在跑（不是失败）：stage=' +
                  safeStringify(j.stage || j.stageName) +
                  (j.progress ? ' · progress=' + safeStringify(j.progress) : '') +
                  (j.heartbeat ? ' · 心跳 ' + String(Math.round(Number(Date.now() - j.heartbeat.at || 0) / 1000)) + 's 前' : '') +
                  ' · 最近输出 ' +
                  String(Math.round(Number(j.idleMs || 0) / 1000)) +
                  's 前'
              )
              const out1 = promotedReceipt(j, String(j.id), 'op=collect：任务还在跑')
              out1.envelope.mode = 'collect-running'
              out1.envelope.notes = ctx2.notes
              return out1
            }
            if (op === 'wait' && j.waitTimedOut) {
              const out2 = promotedReceipt(
                j,
                String(j.id),
                'op=wait：等待窗口（' + String(Math.round(Number(j.waitedMs || 0) / 1000)) + 's）到点，任务仍在跑'
              )
              out2.envelope.mode = 'wait-timeout'
              return out2
            }
            const notes: string[] = []
            if (op === 'wait') notes.push('op=wait 等到终态（等了 ' + String(Math.round(Number(j.waitedMs || 0) / 1000)) + 's）')
            if (j.kind === 'headless' || String(j.id || '').indexOf('run-') === 0)
              notes.push('这是 blender_rt_headless 的运行台账（run 与 job 同一 id 空间）')
            return headlessReceipt(j, { mode: 'job', jobId: ctx2.jobId, runId: j.runId || null, notes: notes })
          }
        })
      ),
    '@dsh-external/dsh-blender-plugin: rt-job'
  )
}
