import { vTool } from '../contracts/tool.js'
import { ANY_SCHEMA } from './schema-diagnostic.js'
import { IS_WIN, T } from '../constants.js'
import { ensureBackend } from '../host/backend.js'
import { backendPost, leasedText, splitArgs } from '../host/http.js'
import { attachPaths } from '../utils/image.js'
import { renderStructured } from '../ui/cards.js'
import {
  HEADLESS_WAIT_MS,
  headlessReceipt,
  newRunId,
  promotedFromTimeout,
  promotedReceipt,
  startJobThenMaybeWait
} from '../headless/receipts.js'
import { detectFileAlias } from '../../lib/runtime/file_guard.js'

export function registerHeadless(ctx: any, port: number): void {
  ctx.effect(
    () =>
      ctx.tools.register(
        vTool({
          name: 'blender_rt_headless',
          description:
            '【无头 Blender · 备用】独立进程跑脚本（blender -b）：GUI 不可用/批处理/长活用；不占 GUI、不动场景。' +
            (IS_WIN ? '⚠ 给 Windows 路径（D:\\x\\y.py）；' : '⚠ cwd 在 UNC 下，WSL 路径要带开头 /；') +
            'script_file 自动给 __file__ + sys.path。' +
            '批量几何 / 校验 / 渲染 / 不需要"人在回路看视口"的活都先走这里；"看一眼"用 rt_do/rt_see。' +
            'print("HEADLESS " + json.dumps(obj)) 回传（单行 JSON）。' +
            '默认 factory-startup + EEVEE 前导（engine="cycles"|"keep"|"none" 可改）。' +
            '**>100 s 或长渲染：as_job=true**（rt_job 收）；等待窗口到点回 promoted jobId，结果仍可收。' +
            'attach_images 可回图；shots 多视角；preload 后直调 K.dsh_*_api。' +
            '参数细节见 catalog(tool:rt_headless)。',
          parameters: {
            script: { type: 'string', description: 'Python 源码（注入 K + bpy/math/mathutils/Vector）' },
            script_file: { type: 'string', description: '直接跑一个 .py 文件（Windows/WSL 路径都行）；比 script= 省事' },
            file: { type: 'string', description: '要打开的 .blend（回执带 inputFile{size,mtime,md5}）；传 .py 会自动当脚本' },
            outdir: { type: 'string', description: '产物目录（WSL 路径也收）；跑完列出新文件，并追加到脚本 sys.argv' },
            as_job: { type: 'boolean', description: 'true = 立刻后台化成作业（预期 >100 s 的活推荐直接用）' },
            wait_s: { type: 'number', description: 'as_job 之后最多再等几秒，到点回 jobId' },
            timeout_ms: { type: 'integer', description: '子进程上限，默认 180000，上限 1800000（到点 SIGKILL）' },
            engine: { type: 'string', description: 'eevee（默认）| cycles | keep | none（省 1–1.5 s）' },
            preload: { type: 'string', description: '预载 runtime 模块（逗号分隔，如 "audit,qc,qc_render"）→ K.dsh_*_api' },
            shots: { type: 'json', description: '多视角出图 [{name,from,look_at,lens,res,samples}]' },
            out_json: { type: 'string', description: '结构化结果落盘路径（>4KB 也会自动落 results/）' },
            env: { type: 'json', description: '子进程环境变量 {KEY:"VALUE"}' },
            args: { type: 'string', description: '追加到 -- 之后的命令行参数（脚本里读 sys.argv / K.args）' },
            workdir: { type: 'string', description: '脚本内 chdir + sys.path 首位' },
            factory_startup: { type: 'boolean', description: '默认 true；false = 用用户启动文件与偏好' },
            use_user_config: { type: 'boolean', description: '透传 BLENDER_USER_CONFIG/SCRIPTS 给无头进程' },
            bootstrap: { type: 'boolean', description: '默认 true = 注入持久内核 K' },
            gpu: { type: 'string', description: '仅 cycles：auto | true（必须 GPU）| false' },
            include_noise: { type: 'boolean', description: '产物清单含 __pycache__/*.blend1' },
            attach_images: { type: 'json', description: 'shots 附图：true=3 张，"sheet"=拼图' },
            hard_exit: {
              type: 'boolean',
              description: 'done 后 os._exit(0) 跳过退出拆解；默认 true 规避 5.2.3 退出 AV，false=旧行为（atexit/handlers 照常）'
            }
          },
          output: { schema: ANY_SCHEMA, render: renderStructured },
          isConcurrencySafe: () => false,
          timeoutMs: T.headless,
          async execute(args: any) {
            if (!(await ensureBackend(port)))
              return { text: '后端不可用（127.0.0.1:' + String(port) + '）；无头通道由后端进程代管，先 blender_viewport op=start' }
            // F-09：工具端必须自己拦 ALIAS / 空串 —— 旧写法 `(args && args.file) || undefined` 在 body 组装前
            // 就把 falsy 剥成 undefined，引擎侧守卫对工具路径永不触发；参数名误写（blend= 等）静默丢参数＝空场景 ok=true。
            const aliasKeys = detectFileAlias(args)
            if (aliasKeys.length)
              return {
                text:
                  'blender_rt_headless 没有参数 ' +
                  aliasKeys.join(', ') +
                  '；打开 .blend 请用 file=。（收到的键：' +
                  Object.keys(args || {}).join(', ') +
                  '）'
              }
            const rawFile = args ? args.file : undefined
            if (typeof rawFile === 'string' && !rawFile.trim())
              return {
                text:
                  'file= 传了空字符串 —— 本次不会打开任何 .blend' + '（脚本会拿到出厂空场景）；要么给真实 .blend 路径，要么就不要传 file='
              }
            const runId = newRunId()
            const asJob = !!(args && args.as_job)
            const waitS = args && args.wait_s !== undefined && args.wait_s !== null && args.wait_s !== '' ? Number(args.wait_s) : null
            const body: any = {
              script: String((args && args.script) || ''),
              file: rawFile === undefined || rawFile === null ? undefined : rawFile,
              outdir: (args && args.outdir) || undefined,
              timeoutMs: args && args.timeout_ms ? Number(args.timeout_ms) : undefined,
              factoryStartup: !(args && args.factory_startup === false),
              bootstrap: !(args && args.bootstrap === false),
              preload: (args && args.preload) || undefined,
              gpu: (args && args.gpu) || undefined,
              engine: (args && args.engine) || undefined,
              useUserConfig: !!(args && args.use_user_config),
              includeNoise: !!(args && args.include_noise),
              expect: args && args.expect && typeof args.expect === 'object' ? args.expect : undefined,
              workdir: (args && args.workdir) || undefined,
              scriptFile: (args && args.script_file) || undefined,
              env: args && args.env && typeof args.env === 'object' ? args.env : undefined,
              outJson: (args && args.out_json) || undefined,
              shots: (args && args.shots) || undefined,
              hardExit: args && typeof args.hard_exit === 'boolean' ? args.hard_exit : undefined,
              runId: runId // v0.9.3（D1）：客户端生成 → 窗口到点时立刻有可用句柄
            }
            if (args && args.args) body.args = splitArgs(args.args)
            const serverBudget = 60000 + Number(body.timeoutMs || 180000)
            const autoJobMs = Number(process.env.DSH_HEADLESS_AUTO_JOB_MS) || 0
            // ---- v0.9.3（D1/F3）：路线决策
            //   as_job=true                → 直接后台化（可选 wait_s 再等一会儿）
            //   serverBudget >= autoJobMs  → 同上（DSH_HEADLESS_AUTO_JOB_MS 显式打开时）
            //   否则                        → 先同步等，窗口到点就回 promoted 回执（结果永不丢）
            const wantJobFirst = asJob || (autoJobMs > 0 && serverBudget >= autoJobMs)
            if (wantJobFirst) {
              const r2: any = await startJobThenMaybeWait(port, body, waitS)
              if (r2.text) return { text: r2.text }
              const why = asJob
                ? 'as_job=true'
                : '预计预算 ' + String(Math.round(serverBudget / 1000)) + 's ≥ DSH_HEADLESS_AUTO_JOB_MS=' + String(autoJobMs) + 'ms'
              if (r2.finished && r2.job) {
                return headlessReceipt(r2.job, {
                  mode: 'job',
                  jobId: r2.jobId,
                  notes: ['已后台化并在等待窗口内完成（' + why + '）—— 这就是完整结果，不必再 collect']
                })
              }
              return promotedReceipt(r2.job, r2.jobId, why + '：已后台化，等待窗口内没跑完（**不是失败**）')
            }
            let res: any = null
            try {
              // 预算 = 等待窗口 + 2 s 余量：到点就转 promoted 回执（+8s 会让每次长活都多等 8 秒，实测浪费）
              res = await backendPost(port, '/headless', body, Math.min(serverBudget, HEADLESS_WAIT_MS + 2000))
            } catch (e: any) {
              // v0.9.3（D1）：客户端窗口到点 / 宿主 exec.signal 取消 —— 服务端子进程仍在跑
              return await promotedFromTimeout(port, runId, e)
            }
            const lt = leasedText(res)
            if (lt) return { text: lt }
            if (!res || res.error)
              return {
                text:
                  'HEADLESS 失败 · ' +
                  String((res && (res.error || res.raw)) || 'unknown') +
                  (res && res.hint ? '\n' + String(res.hint) : '')
              }
            // 后端 /headless 的信封是 {ok, ms, result:<引擎回执>} —— 结构化字段在 result 里（v0.9.3 起与 job 快照同形）
            const bodyRes: any = res && res.result && typeof res.result === 'object' ? res.result : null
            // 注意：**不能**拿 "resultJson 为空" 当缺字段 —— 脚本末行不是 JSON 时 resultJson 本来就该是 null（D3）。
            // 这里只判"后端根本没给引擎回执"。
            if (!bodyRes || (!bodyRes.status && bodyRes.result === undefined && bodyRes.resultJson === undefined && !bodyRes.shots)) {
              const keys = Object.keys(res || {}).filter((k) => k.indexOf('__') !== 0)
              const raw = String((res && res.raw) || '').slice(0, 2000)
              const lines = [
                'HEADLESS 结果缺字段（不是"失败"）· HTTP ' +
                  String(res.__status) +
                  ' · 后端回的键：' +
                  (keys.length ? keys.join(', ') : '(空)')
              ]
              if (Number(res.__heartbeats) > 0) lines.push('（流式回执：' + String(res.__heartbeats) + ' 条心跳，但没取到结果行）')
              if (raw) lines.push('--- 原始响应（前 2KB）---' + String.fromCharCode(10) + raw)
              if (body.script) lines.push('脚本内容长度=' + String(String(body.script).length) + ' 字符（内容见后端临时脚本目录）')
              lines.push('预期日志：' + String(body.outdir || '(默认工作目录)') + '\\dsh_headless_*.stdout.log')
              lines.push('跟进：blender_viewport(op="status") 看 runs 台账（v0.8.10 起每次无头运行都有 id/pid/日志/产物）。')
              return { text: lines.join(String.fromCharCode(10)) }
            }
            const out = headlessReceipt(bodyRes, { mode: 'sync', jobId: null, notes: [] })
            // v0.8.9 边界提示：本次走了流式回执（>15 s，心跳 N 条）
            const beats = Number((res && res.__heartbeats) || 0)
            if (beats > 0)
              out.text += String.fromCharCode(10) + '（本次耗时 > 15 s，后端走了流式回执：' + String(beats) + ' 条心跳后收到结果）'
            // P0：shots 附图（默认不附；attach_images=true → 最多 3 张；"sheet" → 只附拼图）
            if (args && args.attach_images) {
              const rows = (out.envelope && out.envelope.shots && out.envelope.shots.rows) || []
              const paths = rows.map((x: any) => x && x.path).filter(Boolean)
              const wantSheet = String(args.attach_images) === 'sheet'
              const picked = wantSheet ? [] : paths.filter((p: string) => !/sheet|montage/i.test(p)).slice(0, 3)
              const att = await attachPaths(ctx, picked, 'headless-shot', wantSheet ? 1 : 3)
              if (att.images.length) out.images = att.images
              if (att.failed.length) out.text += String.fromCharCode(10) + '⚠ 附图失败：' + att.failed.slice(0, 2).join(' · ')
              if (!att.images.length)
                out.text += String.fromCharCode(10) + '（attach_images 已开，但没有可附的图或读取失败；用 read_image(path) 看 shots 路径）'
            }
            return out
          }
        })
      ),
    '@dsh-external/dsh-blender-plugin: rt-headless'
  )
}
