import { vTool } from '../contracts/tool.js'
import { HERE, HOLDER, HOST_API, PLUGIN_VERSION, SERVER_PATH, T } from '../constants.js'
import {
  base,
  childAlive,
  getChild,
  isPaused,
  killBackendByCmd,
  killChildHandle,
  setPaused,
  sleep,
  startBackend,
} from '../host/backend.js'
import { backendJson, backendPost } from '../host/http.js'
import { schemaResolve } from '../contracts/tool.js'
import { tuiHostSummary } from '../tui-host.js'
import { probeHttp, waitHttp } from '../../lib/runtime/backend_probe.js'
import { asArray, asRecord, errorMessage, text } from '../contracts/dynamic.js'
import type { HostContext } from '../host/context.js'

const NL = String.fromCharCode(10)

export function registerViewport(ctx: HostContext, port: number): void {
  ctx.effect(
    () =>
      ctx.tools.register(
        vTool({
          name: 'blender_viewport',
          description:
            '实时通道后端（127.0.0.1:' +
            String(port) +
            '）运维入口：**status**（健康/视口区域/计数/版本）、**doctor**（真跑一次 bpy 往返的三级体检：未连 / 主线程忙 / addon 线程卡死 + 修法）、' +
            '**start / stop / restart**（后端进程与 15 s 看护）、**who / lease / release**（写租约；被别人持有时写路由 409，只读 op 豁免）、' +
            '**launch**（一键拉起 GUI Blender 并自动 Connect addon：写 boot 脚本 → detached spawn → **轮询 addon 端口**（唯一可信判据）→ 顺手 doctor；幂等）。' +
            '参数细节见 blender_rt_plan(op="catalog", args={tool:"viewport"})。',
          parameters: {
            op: {
              type: 'string',
              required: true,
              description:
                'status | doctor | who | lease | release | start | stop | restart | launch',
            },
            holder: {
              type: 'string',
              description: 'lease/release 的持有者名（默认本插件进程 pid 标识）',
            },
            ttl_ms: {
              type: 'integer',
              description: 'lease 有效期毫秒，默认 600000；写操作会自动续期',
            },
            force: {
              type: 'boolean',
              description: 'lease 时抢占别人的租约（默认 false，被占用返回 409）',
            },
            scope: {
              type: 'string',
              description:
                '【lease】写作用域（第二维）：collection:COL_Lights 或 prefix:LGT- ，逗号分隔；声明式，会出现在 who/lease/409 回执里（装配层用 K.route_collections() 真正归位）',
            },
            wait_ms: {
              type: 'integer',
              description: '【launch】等 addon 端口打开的上限，默认 90000',
            },
            file: {
              type: 'string',
              description: '【launch】启动时要打开的 .blend（WSL/Windows 路径都收）',
            },
            exe: { type: 'string', description: '【launch】显式指定 blender.exe（覆盖配置探测）' },
            addon_module: {
              type: 'string',
              description:
                '【launch】要 enable 的 addon 模块名（逗号分隔）；默认自动扫名字带 mcp / blender_bridge / dshbb 的模块（旧名与新名都能扫到）',
            },
            addon_file: {
              type: 'string',
              description: '【launch】按文件 import 的 addon .py 绝对路径（extension 布局兜底）',
            },
            dry_run: {
              type: 'boolean',
              description: '【launch】true = 只回报将执行的命令，不真的启动',
            },
          },
          output: {
            schema: { type: 'string' },
            render: (_args: unknown, value: unknown) => [{ type: 'text', text: String(value) }],
          },
          timeoutMs: T.viewport,
          async execute(args: Record<string, unknown>) {
            const op = text(args.op) || 'status'
            if (op === 'start') {
              setPaused(false)
              const url = base(port) + '/health'
              if (await probeHttp(url, 800))
                return '后端已在运行：' + base(port) + '/（看护已启用）'
              const how = await startBackend(port)
              // S7：等待改成「轮询到真能服务」（15 s），不再「睡固定窗口后探测一次」——
              // 之前后端要 6 s 以上才起得来时会误报「启动失败」。
              if (await waitHttp(url, 15000, 300))
                return '后端已启动（' + how + '）：' + base(port) + '/（看护已启用）'
              return (
                '启动失败：' +
                how +
                '（15 s 内 /health 仍不通；可先 op=restart 清残留进程，或检查 ' +
                SERVER_PATH +
                ' 与 node）'
              )
            }
            if (op === 'restart') {
              setPaused(false)
              killChildHandle()
              killBackendByCmd(port)
              await sleep(500)
              const how2 = await startBackend(port)
              if (await waitHttp(base(port) + '/health', 15000, 300))
                return '后端已重启（' + how2 + '）：' + base(port) + '/'
              return '重启失败：' + how2 + '（15 s 内 /health 仍不通）'
            }
            if (op === 'stop') {
              setPaused(true)
              const notes: string[] = []
              if (killChildHandle()) notes.push('child-句柄已杀')
              const killed = killBackendByCmd(port)
              if (killed.length) notes.push('残留后端已杀 pid=' + killed.join(','))
              if (!notes.length)
                return '没有在跑的后端（127.0.0.1:' + String(port) + '）；看护已暂停'
              await sleep(400)
              return '已停止后端：' + notes.join(' · ') + '（看护已暂停，op=start 恢复）'
            }
            if (op === 'who' || op === 'lease' || op === 'release') {
              try {
                if (op === 'who') {
                  const w = await backendJson(port, '/who', 15000)
                  const lz = asRecord(w.lease)
                  const m = asRecord(w.metrics)
                  return [
                    '租约：' +
                      (lz.active
                        ? '持有者 ' +
                          text(lz.holder) +
                          '（剩余 ' +
                          String(Math.round(Number(lz.expiresInMs || 0) / 1000)) +
                          's，续期 ' +
                          text(lz.renewals || 0) +
                          ' 次）'
                        : '空闲（谁都能写）'),
                    '本会话 holder：' +
                      HOLDER +
                      (lz.active && lz.holder === HOLDER ? '（就是本会话）' : ''),
                    '写作用域：' + (lz.scope ? JSON.stringify(lz.scope) : '未声明（= 不限制）'),
                    '通道：calls=' +
                      text(m.calls || 0) +
                      ' errors=' +
                      text(m.errors || 0) +
                      ' timeouts=' +
                      text(m.timeouts || 0) +
                      ' inflight=' +
                      text(m.inflight || 0) +
                      ' lastCmd=' +
                      text(m.lastCmd || '-'),
                    '统计：' + JSON.stringify(w.stats || {}),
                  ].join(NL)
                }
                const body: Record<string, unknown> = { holder: text(args.holder) || HOLDER }
                if (args.ttl_ms) body.ttlMs = Number(args.ttl_ms)
                if (op === 'lease' && args.force) body.force = true
                if (op === 'lease' && args.scope) body.scope = text(args.scope)
                const r = await backendPost(
                  port,
                  op === 'lease' ? '/lease' : '/release',
                  body,
                  20000,
                )
                if (op === 'lease' && r.error === 'leased') {
                  return (
                    '租约被占用：holder=' +
                    text(r.holder) +
                    '，剩余 ' +
                    String(Math.round(Number(r.expiresInMs || 0) / 1000)) +
                    's' +
                    NL +
                    text(r.hint) +
                    NL +
                    '（确认对方在跑就知道为什么被挡；要强抢再加 force=true）'
                  )
                }
                return (op === 'lease' ? 'LEASE ' : 'RELEASE ') + JSON.stringify(r)
              } catch (e: unknown) {
                return op + ' 失败：' + errorMessage(e) + '（后端没起？blender_viewport op=start）'
              }
            }
            // v0.9.4：一键拉起 GUI Blender + 自动 Connect addon（agent 没有人手点 N 面板）
            if (op === 'launch') {
              try {
                const body: Record<string, unknown> = {}
                if (args.wait_ms) body.waitMs = Number(args.wait_ms)
                if (args.file) body.file = text(args.file)
                if (args.exe) body.exe = text(args.exe)
                if (args.addon_module) body.addonModule = text(args.addon_module)
                if (args.addon_file) body.addonFile = text(args.addon_file)
                if (args.dry_run) body.dryRun = true
                const r = await backendPost(
                  port,
                  '/launch',
                  body,
                  Math.max(30000, Number(args.wait_ms || 90000) + 30000),
                )
                const lines: string[] = []
                lines.push(
                  (r.ok ? 'LAUNCH ok' : 'LAUNCH 未成功') +
                    (r.already
                      ? ' · 已经在监听（幂等，未重复启动）'
                      : r.dryRun
                        ? ' · dryRun（没启动）'
                        : '') +
                    (r.launched ? ' · 已 spawn pid=' + text(r.pid) : '') +
                    ' · addon 端口 ' +
                    text(r.addonPort || '-') +
                    ' · 等待 ' +
                    text(r.waitedMs || 0) +
                    'ms',
                )
                if (r.exe)
                  lines.push(
                    'blender.exe：' +
                      text(r.exe) +
                      (r.configFrom ? '（来源 ' + text(r.configFrom) + '）' : ''),
                  )
                if (r.error) lines.push('错误：' + text(r.error))
                if (r.hint) lines.push('提示：' + text(r.hint))
                const steps = asArray(r.steps)
                for (const s of steps) lines.push('· ' + text(s))
                const b = asRecord(r.boot)
                if (r.boot) {
                  lines.push(
                    'boot 结论（GUI 侧自己写的 launch-status.json）：loaded_by=' +
                      text(b.loaded_by || '-') +
                      ' · server_running=' +
                      text(b.server_running) +
                      ' · enabled=' +
                      JSON.stringify(b.enabled || []),
                  )
                  const bootErrors = asArray(b.errors)
                  if (bootErrors.length)
                    lines.push('boot 错误：' + bootErrors.slice(0, 4).map(text).join(' | '))
                  if (b.env) lines.push('boot 环境：' + JSON.stringify(b.env))
                  const seen = asArray(b.addon_modules_seen)
                  if (seen.length)
                    lines.push('GUI 里可见的 addon 模块：' + seen.slice(0, 20).map(text).join(', '))
                } else if (r.launched || r.dryRun) {
                  lines.push(
                    'boot 状态文件：' +
                      text(r.statusFile || '?') +
                      '（没读到：可能 Blender 还没走到脚本，或 exe 路径不对）',
                  )
                }
                const doc = asRecord(r.doctor)
                const addon = asRecord(doc.addon)
                if (doc.addon)
                  lines.push(
                    '启动后体检：' +
                      (addon.summary ? text(addon.summary) : JSON.stringify(addon)).slice(0, 400),
                  )
                if (r.bootScript)
                  lines.push('boot 脚本：' + text(r.bootScript) + '（想手工排查就直接跑它）')
                return lines.join(NL)
              } catch (e: unknown) {
                return (
                  'launch 失败：' +
                  errorMessage(e) +
                  '（后端没起？先 blender_viewport op=start —— 那是管**后端**进程的，本 op 才是管 Blender 的）'
                )
              }
            }
            if (op === 'doctor') {
              try {
                const d = await backendJson(port, '/doctor')
                const metrics = asRecord(d.metrics)
                const diag = d.diagnosis ? asRecord(d.diagnosis) : asRecord(metrics.lastDiagnosis)
                const lines: string[] = []
                lines.push('诊断：' + text(d.kind || diag.kind || (d.ok ? 'ok' : 'unknown')))
                lines.push('宿主 API：' + HOST_API) // v0.9.2（R2）：DSH 升级后一眼看出插件挂的是哪份 dsh-tools
                lines.push(tuiHostSummary()) // v2.6.1：dsh-TUI 宿主半边接上了哪些接缝
                lines.push('插件：v' + PLUGIN_VERSION + ' @ ' + HERE)
                lines.push(
                  'schemastery：' +
                    (schemaResolve.via
                      ? '已解析（' + schemaResolve.via + '）'
                      : '❌ ' +
                        String(schemaResolve.error || '未解析') +
                        ' —— 工具未注册（降级模式）'),
                )
                if (diag.summary) lines.push('结论：' + text(diag.summary))
                if (diag.fix) lines.push('修法：' + text(diag.fix))
                if (d.addon) lines.push('addon：' + JSON.stringify(d.addon))
                const ledger = d.ledger ? asRecord(d.ledger) : null
                const workDir = ledger ? asRecord(ledger.workDir) : null
                if (ledger)
                  lines.push(
                    '工作目录：' +
                      text(workDir?.wsl || '?') +
                      (workDir?.writable
                        ? '（可写）'
                        : '（⚠️ **不可写** —— ' + text(ledger.lastError || '?') + '）'),
                  )
                if (ledger && !ledger.writable && ledger.hint)
                  lines.push('修法：' + text(ledger.hint))
                lines.push('计数：' + JSON.stringify(d.stats || {}))
                lines.push(
                  '指标：calls=' +
                    text(metrics.calls || 0) +
                    ' errors=' +
                    text(metrics.errors || 0) +
                    ' timeouts=' +
                    text(metrics.timeouts || 0) +
                    ' inflight=' +
                    text(metrics.inflight || 0) +
                    ' lastCmd=' +
                    text(metrics.lastCmd || '-') +
                    (metrics.lastCmdAgeMs === null || metrics.lastCmdAgeMs === undefined
                      ? ''
                      : ' (' + String(Math.round(Number(metrics.lastCmdAgeMs) / 1000)) + 's 前)') +
                    ' views=' +
                    text(metrics.views || 0) +
                    ' headlessRuns=' +
                    text(metrics.headlessRuns || 0),
                )
                return lines.join(NL)
              } catch (e: unknown) {
                return (
                  '体检失败：' + errorMessage(e) + '（后端可能没起：blender_viewport op=start）'
                )
              }
            }
            try {
              const s = await backendJson(port, '/status')
              const region = s.region ? asRecord(s.region) : null
              const views = region ? asArray(region.views) : []
              const v = views.length ? asRecord(views[0]) : null
              const ledger = s.ledger ? asRecord(s.ledger) : null
              const plugin = s.plugin ? asRecord(s.plugin) : null
              const runs = asArray(s.runs)
              const child = getChild()
              return JSON.stringify({
                running: true,
                paused: isPaused(),
                child: child
                  ? childAlive()
                    ? 'alive pid=' + String(child.pid)
                    : 'dead pid=' + String(child.pid)
                  : 'none',
                watchdog: '15s',
                endpoint: base(port),
                viewportRegion: v ? v.region : null,
                shading: v ? v.shading : null,
                window: region ? region.window : null,
                stats: s.stats,
                diagnosis: s.diagnosis || null,
                addonError: s.addonError || null, // v0.9.3：addon 没连上也要能看到本地信息（台账/工作目录）
                plugin: s.plugin || null, // v0.8.10（A0）：进程内 lib 的版本 + runtime 指纹
                pluginVersion: plugin ? plugin.version : null,
                hostApi: HOST_API, // v0.9.2（R2）：插件侧解析到的宿主 dsh-tools
                toolVersion: PLUGIN_VERSION,
                // v1.0.1+S11（issue #10）：包名形态解析走哪条通道 / 为什么失败 —— 这类静默整体消失必须留现场
                schemaResolve: {
                  via: schemaResolve.via || null,
                  error: schemaResolve.error || null,
                  tried: schemaResolve.tried,
                },
                recentRuns: runs.map((raw) => {
                  const x = asRecord(raw)
                  const expect = asRecord(x.expect)
                  return {
                    id: x.id,
                    status: x.status,
                    pid: x.pid,
                    ms: x.ms,
                    outdir: x.outdir,
                    artifacts: x.artifactCount,
                    expectOk: x.expect ? expect.ok : null,
                  }
                }),
                ledger: s.ledger || null, // v0.8.10（D4）：磁盘台账（后端重启后仍可查）
                // v0.9.3：workDir 可写性自证（只读挂载上写台账/日志会静默失败 —— 这里必须说出来）
                workDir: ledger ? ledger.workDir || null : null,
                workDirWritable: !!(ledger && ledger.writable),
                workDirWarning:
                  ledger && !ledger.writable ? text(ledger.hint || ledger.lastError || '') : null,
              })
            } catch (e: unknown) {
              return JSON.stringify({
                running: false,
                url: base(port) + '/',
                error: errorMessage(e),
                code: 'backend_unreachable',
                next_action: '用 blender_viewport op=start 拉起后端；仍失败看 op=doctor',
                hint: '用 blender_viewport op=start 拉起',
              })
            }
          },
        }),
      ),
    '@dsh-external/dsh-blender-plugin: tool',
  )
}
