#!/usr/bin/env node
/**
 * tests/launch_selftest.mjs —— v0.9.4 启动器（blender_viewport op="launch"）集成自检
 *
 * 外部反馈《M1A1 分件建模》P1：「文档写的是按 N → MCP for Blender → Connect，但 agent 没有人手。」
 * 这个测试用**假 blender**（一个只负责在 addon 端口上监听、并活下去的脚本）验证启动链路：
 *   spawn exe → 轮询端口 → already 幂等 → dryRun → 失败路径（exe 不存在/端口始终不开）
 * 全程**不碰用户真正开着的那个 Blender**（独立后端 + 独立端口 + 独立工作目录 + 假 exe）。
 *
 * 用法：node tests/launch_selftest.mjs
 */
import { spawn } from 'node:child_process'
import fs from 'node:fs'
import net from 'node:net'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const PKG = path.join(HERE, '..')
const HTTP_PORT = Number(process.env.DSH_SELFTEST_HTTP_PORT || 9891)
const ADDON_PORT = Number(process.env.DSH_SELFTEST_ADDON_PORT || 9892)
const TMP = fs.mkdtempSync(path.join(os.tmpdir(), 'dsh-launch-selftest-'))
const MOCK = path.join(TMP, 'fake-blender.sh')
const MOCK_ARGS = path.join(TMP, 'mock-args.txt')

let pass = 0, fail = 0
const failures = []
function ok(name, cond, detail) {
  if (cond) { pass++; console.log('  ✓ ' + name) }
  else { fail++; failures.push(name + (detail ? (' — ' + detail) : '')); console.log('  ✗ ' + name + (detail ? (' — ' + detail) : '')) }
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

async function post(p, body, timeoutMs) {
  const ctl = new AbortController()
  const t = setTimeout(() => ctl.abort(), timeoutMs || 120000)
  try {
    const r = await fetch('http://127.0.0.1:' + String(p) + '/launch', {
      method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body || {}), signal: ctl.signal,
    })
    return await r.json()
  } finally { clearTimeout(t) }
}
async function getJson(p, pathname) {
  try { return await (await fetch('http://127.0.0.1:' + String(p) + pathname)).json() } catch (e) { return null }
}
function portOpen(port) {
  return new Promise((resolve) => {
    const s = net.connect({ host: '127.0.0.1', port })
    const done = (v) => { try { s.destroy() } catch (e) {} resolve(v) }
    s.once('connect', () => done(true))
    s.once('error', () => done(false))
    setTimeout(() => done(false), 800)
  })
}

// 假 blender：写下 argv（可断言启动参数）→ 在 addon 端口监听 → 活着
fs.writeFileSync(MOCK, [
  '#!/usr/bin/env bash',
  'echo "$@" > "' + MOCK_ARGS + '"',
  'node -e \'const net=require("net");const p=Number(process.env.DSH_MOCK_ADDON_PORT||' + String(ADDON_PORT) + ');',
  'net.createServer((c)=>c.end()).listen(p,"127.0.0.1",()=>console.log("fake addon "+p));',
  'setInterval(()=>{},1<<30);\'',
].join(String.fromCharCode(10)), 'utf8')
fs.chmodSync(MOCK, 0o755)

console.log('== 启动器集成自检 ==')
console.log('假 blender：' + MOCK)
console.log('隔离后端：http://127.0.0.1:' + String(HTTP_PORT) + ' · addon 端口 ' + String(ADDON_PORT) + ' · 工作目录 ' + TMP)

const backend = spawn(process.execPath, [path.join(PKG, 'runtime', 'server.mjs'), '--port', String(HTTP_PORT)], {
  env: Object.assign({}, process.env, {
    DSH_BLENDER_WORKDIR: TMP,
    DSH_BLENDER_HTTP_PORT: String(HTTP_PORT),
    DSH_BLENDER_ADDON_PORT: String(ADDON_PORT),
    DSH_BLENDER_EXE: MOCK,
    DSH_MOCK_ADDON_PORT: String(ADDON_PORT),
    DSH_BLENDER_HOLDER: 'launch-selftest',
  }),
  stdio: ['ignore', 'pipe', 'pipe'],
})
let backendLog = ''
backend.stdout.on('data', (d) => { backendLog += d.toString('utf8') })
backend.stderr.on('data', (d) => { backendLog += d.toString('utf8') })

function cleanup() {
  try { backend.kill('SIGKILL') } catch (e) {}
}
process.on('exit', cleanup)

// 等后端起来
let up = false
for (let i = 0; i < 40; i++) { if (await getJson(HTTP_PORT, '/health')) { up = true; break } await sleep(300) }
ok('隔离后端已就绪', up, 'backend log: ' + backendLog.slice(-300))
if (!up) { console.log('\n后端没起来，后续用例跳过'); process.exit(1) }

// ① 正常启动：假 blender 起来 + 端口开 → ok
const r1 = await post(HTTP_PORT, { addonPort: ADDON_PORT, waitMs: 20000 }, 40000)
ok('launch ok=true', r1 && r1.ok === true, JSON.stringify(r1).slice(0, 400))
ok('launch launched=true（真的 spawn 了）', !!r1 && r1.launched === true)
ok('launch 报告了 pid', !!r1 && r1.pid > 0, 'pid=' + String(r1 && r1.pid))
ok('launch 探测到 addon 端口已监听', (await portOpen(ADDON_PORT)) === true)
ok('launch 用了配置里的 blender.exe', !!r1 && String(r1.exe) === MOCK, 'exe=' + String(r1 && r1.exe))
ok('launch 回执带 steps', !!r1 && Array.isArray(r1.steps) && r1.steps.length >= 2, JSON.stringify(r1 && r1.steps))
ok('launch 回执带 human hint', !!r1 && typeof r1.hint === 'string' && r1.hint.length > 10)
ok('launch 在端口开了之后顺手跑了 doctor', !!r1 && 'doctor' in r1)

// ② boot 脚本真的落盘了，且内容是对的（agent 可照抄手工跑）
const bootFile = r1 && r1.bootScript
ok('回执给出 boot 脚本路径', typeof bootFile === 'string' && bootFile.length > 0, String(bootFile))
let bootSrc = ''
try {
  const bootWsl = path.join(TMP, String(bootFile).split(/[\\/]/).pop())
  bootSrc = fs.readFileSync(bootWsl, 'utf8')
} catch (e) { bootSrc = '' }
ok('boot 脚本已写入工作目录', bootSrc.length > 200, 'bytes=' + String(bootSrc.length))
ok('boot 脚本会 enable addon', bootSrc.indexOf('addon_utils.enable') >= 0)
ok('boot 脚本带直接 import 兜底（Blender 5.x extension 布局）', bootSrc.indexOf('spec_from_file_location') >= 0)
ok('boot 脚本把结论写进 launch-status.json', bootSrc.indexOf('DSH_BLENDER_LAUNCH_STATUS') >= 0)

// ③ 假 blender 收到了 --python <boot>（启动参数连接得上）
let mockArgs = ''
try { mockArgs = fs.readFileSync(MOCK_ARGS, 'utf8') } catch (e) { mockArgs = '' }
ok('假 blender 收到 --python boot 脚本', mockArgs.indexOf('--python') >= 0 && mockArgs.indexOf('.py') > 0, 'argv=' + mockArgs.slice(0, 160))

// ④ 幂等：已经在监听就不要再 spawn 一个（多 agent 反复调用不会堆 Blender）
const r2 = await post(HTTP_PORT, { addonPort: ADDON_PORT, waitMs: 5000 }, 20000)
ok('第二次 launch 报 already=true', !!r2 && r2.already === true, JSON.stringify(r2).slice(0, 300))
ok('第二次 launch 没有 launch 新进程', !!r2 && r2.launched === false)

// ⑤ dry_run：只给命令，不启动（此时端口仍开着 → 应该先报 already，所以先关掉假 blender 再测）
//    这里不做 kill 假进程（它是 detached 的），改用另一个空闲端口测 dryRun
const FREE_PORT = ADDON_PORT + 7
const r3 = await post(HTTP_PORT, { addonPort: FREE_PORT, waitMs: 3000, dryRun: true }, 20000)
ok('dryRun 报 dryRun=true 且没启动', !!r3 && r3.dryRun === true && r3.launched === false, JSON.stringify(r3).slice(0, 300))
ok('dryRun 给出将要执行的参数', !!r3 && Array.isArray(r3.args) && r3.args.length >= 2, JSON.stringify(r3 && r3.args))
ok('dryRun 后端口仍然没开（确实没启动）', (await portOpen(FREE_PORT)) === false)

// ⑥ 失败路径：exe 不存在 → 要给出可读错误 + 修法，而不是静默超时
const r4 = await post(HTTP_PORT, { addonPort: FREE_PORT + 1, waitMs: 2500, exe: path.join(TMP, 'not-there-blender.exe') }, 30000)
ok('exe 不存在时 ok=false', !!r4 && r4.ok === false, JSON.stringify(r4).slice(0, 300))
ok('exe 不存在时给出 error 或等待超时的可读结论', !!r4 && (typeof r4.error === 'string' || typeof r4.hint === 'string'))
ok('失败时也回传了等待时长（可诊断）', !!r4 && typeof r4.waitedMs === 'number')

// ⑦ health 里有启动计数（运维可观测）
const h = await getJson(HTTP_PORT, '/health')
ok('后端 stats 记录了 launches', !!h && h.stats && Number(h.stats.launches) >= 3, 'launches=' + String(h && h.stats && h.stats.launches))

console.log('')
console.log('─'.repeat(64))
console.log('launch-selftest：' + pass + ' 通过 / ' + fail + ' 失败')
if (fail) { for (const f of failures) console.log('  - ' + f); cleanup(); process.exit(1) }
console.log('OK：启动器链路（spawn → 端口判据 → 幂等 → dryRun → 失败可读）全通。')
cleanup()
process.exit(0)

