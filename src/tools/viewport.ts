import { vTool } from '../contracts/tool.js'
import { HERE, HOLDER, HOST_API, PLUGIN_VERSION, SERVER_PATH, T } from '../constants.js'
import { base, childAlive, getChild, isPaused, killBackendByCmd, killChildHandle, setPaused, sleep, startBackend } from '../host/backend.js'
import { backendJson, backendPost } from '../host/http.js'
import { schemaResolve } from '../contracts/tool.js'
import { tuiHostSummary } from '../tui-host.js'
import { probeHttp, waitHttp } from '../../lib/runtime/backend_probe.js'

export function registerViewport(ctx: any, port: number): void {
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
              description: 'status | doctor | who | lease | release | start | stop | restart | launch'
            },
            holder: { type: 'string', description: 'lease/release 的持有者名（默认本插件进程 pid 标识）' },
            ttl_ms: { type: 'integer', description: 'lease 有效期毫秒，默认 600000；写操作会自动续期' },
            force: { type: 'boolean', description: 'lease 时抢占别人的租约（默认 false，被占用返回 409）' },
            scope: {
              type: 'string',
              description:
                '【lease】写作用域（第二维）：collection:COL_Lights 或 prefix:LGT- ，逗号分隔；声明式，会出现在 who/lease/409 回执里（装配层用 K.route_collections() 真正归位）'
            },
            wait_ms: { type: 'integer', description: '【launch】等 addon 端口打开的上限，默认 90000' },
            file: { type: 'string', description: '【launch】启动时要打开的 .blend（WSL/Windows 路径都收）' },
            exe: { type: 'string', description: '【launch】显式指定 blender.exe（覆盖配置探测）' },
            addon_module: {
              type: 'string',
              description:
                '【launch】要 enable 的 addon 模块名（逗号分隔）；默认自动扫名字带 mcp / blender_bridge / dshbb 的模块（旧名与新名都能扫到）'
            },
            addon_file: { type: 'string', description: '【launch】按文件 import 的 addon .py 绝对路径（extension 布局兜底）' },
            dry_run: { type: 'boolean', description: '【launch】true = 只回报将执行的命令，不真的启动' }
          },
          output: {
            schema: { type: 'string' },
            render: (_args: unknown, value: unknown) => [{ type: 'text', text: String(value) }]
          },
          timeoutMs: T.viewport,
          async execute(args: any) {
            const op = String((args && args.op) || 'status')
            if (op === 'start') {
              setPaused(false)
              const url = base(port) + '/health'
              if (await probeHttp(url, 800)) return '后端已在运行：' + base(port) + '/（看护已启用）'
              const how = await startBackend(port)
              // S7：等待改成「轮询到真能服务」（15 s），不再「睡固定窗口后探测一次」——
              // 之前后端要 6 s 以上才起得来时会误报「启动失败」。
              if (await waitHttp(url, 15000, 300)) return '后端已启动（' + how + '）：' + base(port) + '/（看护已启用）'
              return '启动失败：' + how + '（15 s 内 /health 仍不通；可先 op=restart 清残留进程，或检查 ' + SERVER_PATH + ' 与 node）'
            }
            if (op === 'restart') {
              setPaused(false)
              killChildHandle()
              killBackendByCmd(port)
              await sleep(500)
              const how2 = await startBackend(port)
              if (await waitHttp(base(port) + '/health', 15000, 300)) return '后端已重启（' + how2 + '）：' + base(port) + '/'
              return '重启失败：' + how2 + '（15 s 内 /health 仍不通）'
            }
            if (op === 'stop') {
              setPaused(true)
              const notes: string[] = []
              if (killChildHandle()) notes.push('child-句柄已杀')
              const killed = killBackendByCmd(port)
              if (killed.length) notes.push('残留后端已杀 pid=' + killed.join(','))
              if (!notes.length) return '没有在跑的后端（127.0.0.1:' + String(port) + '）；看护已暂停'
              await sleep(400)
              return '已停止后端：' + notes.join(' · ') + '（看护已暂停，op=start 恢复）'
            }
            if (op === 'who' || op === 'lease' || op === 'release') {
              try {
                if (op === 'who') {
                  const w: any = await backendJson(port, '/who', 15000)
                  const lz = (w && w.lease) || {}
                  const m = (w && w.metrics) || {}
                  return [
                    '租约：' +
                      (lz.active
                        ? '持有者 ' +
                          String(lz.holder) +
                          '（剩余 ' +
                          String(Math.round(Number(lz.expiresInMs || 0) / 1000)) +
                          's，续期 ' +
                          String(lz.renewals || 0) +
                          ' 次）'
                        : '空闲（谁都能写）'),
                    '本会话 holder：' + HOLDER + (lz.active && lz.holder === HOLDER ? '（就是本会话）' : ''),
                    '写作用域：' + (lz.scope ? JSON.stringify(lz.scope) : '未声明（= 不限制）'),
                    '通道：calls=' +
                      String(m.calls || 0) +
                      ' errors=' +
                      String(m.errors || 0) +
                      ' timeouts=' +
                      String(m.timeouts || 0) +
                      ' inflight=' +
                      String(m.inflight || 0) +
                      ' lastCmd=' +
                      String(m.lastCmd || '-'),
                    '统计：' + JSON.stringify((w && w.stats) || {})
                  ].join('\n')
                }
                const body: any = { holder: String((args && args.holder) || HOLDER) }
                if (args && args.ttl_ms) body.ttlMs = Number(args.ttl_ms)
                if (op === 'lease' && args && args.force) body.force = true
                if (op === 'lease' && args && args.scope) body.scope = String(args.scope)
                const r: any = await backendPost(port, op === 'lease' ? '/lease' : '/release', body, 20000)
                if (op === 'lease' && r && r.error === 'leased') {
                  return (
                    '租约被占用：holder=' +
                    String(r.holder) +
                    '，剩余 ' +
                    String(Math.round(Number(r.expiresInMs || 0) / 1000)) +
                    's\n' +
                    String(r.hint || '') +
                    '\n（确认对方在跑就知道为什么被挡；要强抢再加 force=true）'
                  )
                }
                return (op === 'lease' ? 'LEASE ' : 'RELEASE ') + JSON.stringify(r)
              } catch (e: any) {
                return op + ' 失败：' + String((e && e.message) || e) + '（后端没起？blender_viewport op=start）'
              }
            }
            // v0.9.4：一键拉起 GUI Blender + 自动 Connect addon（agent 没有人手点 N 面板）
            if (op === 'launch') {
              try {
                const body: any = {}
                if (args && args.wait_ms) body.waitMs = Number(args.wait_ms)
                if (args && args.file) body.file = String(args.file)
                if (args && args.exe) body.exe = String(args.exe)
                if (args && args.addon_module) body.addonModule = String(args.addon_module)
                if (args && args.addon_file) body.addonFile = String(args.addon_file)
                if (args && args.dry_run) body.dryRun = true
                const r: any = await backendPost(port, '/launch', body, Math.max(30000, Number((args && args.wait_ms) || 90000) + 30000))
                const lines: string[] = []
                lines.push(
                  (r && r.ok ? 'LAUNCH ok' : 'LAUNCH 未成功') +
                    (r && r.already ? ' · 已经在监听（幂等，未重复启动）' : r && r.dryRun ? ' · dryRun（没启动）' : '') +
                    (r && r.launched ? ' · 已 spawn pid=' + String(r.pid) : '') +
                    ' · addon 端口 ' +
                    String((r && r.addonPort) || '-') +
                    ' · 等待 ' +
                    String((r && r.waitedMs) || 0) +
                    'ms'
                )
                if (r && r.exe) lines.push('blender.exe：' + String(r.exe) + (r.configFrom ? '（来源 ' + String(r.configFrom) + '）' : ''))
                if (r && r.error) lines.push('错误：' + String(r.error))
                if (r && r.hint) lines.push('提示：' + String(r.hint))
                if (r && Array.isArray(r.steps)) for (const s of r.steps) lines.push('· ' + String(s))
                const b = r && r.boot
                if (b) {
                  lines.push(
                    'boot 结论（GUI 侧自己写的 launch-status.json）：loaded_by=' +
                      String(b.loaded_by || '-') +
                      ' · server_running=' +
                      String(b.server_running) +
                      ' · enabled=' +
                      JSON.stringify(b.enabled || [])
                  )
                  if (Array.isArray(b.errors) && b.errors.length) lines.push('boot 错误：' + b.errors.slice(0, 4).join(' | '))
                  if (b.env) lines.push('boot 环境：' + JSON.stringify(b.env))
                  if (Array.isArray(b.addon_modules_seen) && b.addon_modules_seen.length)
                    lines.push('GUI 里可见的 addon 模块：' + b.addon_modules_seen.slice(0, 20).join(', '))
                } else if (r && (r.launched || r.dryRun)) {
                  lines.push('boot 状态文件：' + String(r.statusFile || '?') + '（没读到：可能 Blender 还没走到脚本，或 exe 路径不对）')
                }
                const doc = r && r.doctor
                if (doc && doc.addon)
                  lines.push('启动后体检：' + String((doc.addon && doc.addon.summary) || JSON.stringify(doc.addon)).slice(0, 400))
                if (r && r.bootScript) lines.push('boot 脚本：' + String(r.bootScript) + '（想手工排查就直接跑它）')
                return lines.join(String.fromCharCode(10))
              } catch (e: any) {
                return (
                  'launch 失败：' +
                  String((e && e.message) || e) +
                  '（后端没起？先 blender_viewport op=start —— 那是管**后端**进程的，本 op 才是管 Blender 的）'
                )
              }
            }
            if (op === 'doctor') {
              try {
                const d: any = await backendJson(port, '/doctor')
                const m = (d && d.metrics) || {}
                const diag = (d && d.diagnosis) || m.lastDiagnosis || null
                const lines: string[] = []
                lines.push('诊断：' + String((d && d.kind) || (diag && diag.kind) || (d && d.ok ? 'ok' : 'unknown')))
                lines.push('宿主 API：' + HOST_API) // v0.9.2（R2）：DSH 升级后一眼看出插件挂的是哪份 dsh-tools
                lines.push(tuiHostSummary()) // v2.6.1：dsh-TUI 宿主半边接上了哪些接缝
                lines.push('插件：v' + PLUGIN_VERSION + ' @ ' + HERE)
                lines.push(
                  'schemastery：' +
                    (schemaResolve.via
                      ? '已解析（' + schemaResolve.via + '）'
                      : '❌ ' + String(schemaResolve.error || '未解析') + ' —— 工具未注册（降级模式）')
                )
                if (diag && diag.summary) lines.push('结论：' + diag.summary)
                if (diag && diag.fix) lines.push('修法：' + diag.fix)
                if (d && d.addon) lines.push('addon：' + JSON.stringify(d.addon))
                const led = (d && d.ledger) || null
                if (led)
                  lines.push(
                    '工作目录：' +
                      String((led.workDir && led.workDir.wsl) || '?') +
                      (led.workDir && led.workDir.writable ? '（可写）' : '（⚠️ **不可写** —— ' + String(led.lastError || '?') + '）')
                  )
                if (led && !led.writable && led.hint) lines.push('修法：' + String(led.hint))
                lines.push('计数：' + JSON.stringify(d && d.stats ? d.stats : {}))
                lines.push(
                  '指标：calls=' +
                    String(m.calls || 0) +
                    ' errors=' +
                    String(m.errors || 0) +
                    ' timeouts=' +
                    String(m.timeouts || 0) +
                    ' inflight=' +
                    String(m.inflight || 0) +
                    ' lastCmd=' +
                    String(m.lastCmd || '-') +
                    (m.lastCmdAgeMs === null || m.lastCmdAgeMs === undefined
                      ? ''
                      : ' (' + String(Math.round(Number(m.lastCmdAgeMs) / 1000)) + 's 前)') +
                    ' views=' +
                    String(m.views || 0) +
                    ' headlessRuns=' +
                    String(m.headlessRuns || 0)
                )
                return lines.join('\n')
              } catch (e: any) {
                return '体检失败：' + String((e && e.message) || e) + '（后端可能没起：blender_viewport op=start）'
              }
            }
            try {
              const s = await backendJson(port, '/status')
              const views = s && s.region && s.region.views ? s.region.views : []
              const v = views[0] || null
              return JSON.stringify({
                running: true,
                paused: isPaused(),
                child: getChild() ? (childAlive() ? 'alive pid=' + String(getChild().pid) : 'dead pid=' + String(getChild().pid)) : 'none',
                watchdog: '15s',
                endpoint: base(port),
                viewportRegion: v ? v.region : null,
                shading: v ? v.shading : null,
                window: s && s.region ? s.region.window : null,
                stats: s.stats,
                diagnosis: s.diagnosis || null,
                addonError: s.addonError || null, // v0.9.3：addon 没连上也要能看到本地信息（台账/工作目录）
                plugin: s.plugin || null, // v0.8.10（A0）：进程内 lib 的版本 + runtime 指纹
                pluginVersion: s.plugin ? s.plugin.version : null,
                hostApi: HOST_API, // v0.9.2（R2）：插件侧解析到的宿主 dsh-tools
                toolVersion: PLUGIN_VERSION,
                // v1.0.1+S11（issue #10）：包名形态解析走哪条通道 / 为什么失败 —— 这类静默整体消失必须留现场
                schemaResolve: { via: schemaResolve.via || null, error: schemaResolve.error || null, tried: schemaResolve.tried },
                recentRuns: (s.runs || []).map((x: any) => ({
                  id: x.id,
                  status: x.status,
                  pid: x.pid,
                  ms: x.ms,
                  outdir: x.outdir,
                  artifacts: x.artifactCount,
                  expectOk: x.expect ? x.expect.ok : null
                })),
                ledger: s.ledger || null, // v0.8.10（D4）：磁盘台账（后端重启后仍可查）
                // v0.9.3：workDir 可写性自证（只读挂载上写台账/日志会静默失败 —— 这里必须说出来）
                workDir: (s.ledger && s.ledger.workDir) || null,
                workDirWritable: !!(s.ledger && s.ledger.writable),
                workDirWarning: s.ledger && !s.ledger.writable ? String(s.ledger.hint || s.ledger.lastError || '') : null
              })
            } catch (e: any) {
              return JSON.stringify({
                running: false,
                url: base(port) + '/',
                error: String((e && e.message) || e),
                hint: '用 blender_viewport op=start 拉起'
              })
            }
          }
        })
      ),
    '@dsh-external/dsh-blender-plugin: tool'
  )
}
