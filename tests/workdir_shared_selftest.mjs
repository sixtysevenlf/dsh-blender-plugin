#!/usr/bin/env node
/**
 * tests/workdir_shared_selftest.mjs —— v0.9.6（D3）：工作目录必须放在 Windows 侧读得到的地方
 *
 * 背景（现场实测 2026-09-26，lease_stale_selftest 的 D2 曾因此失败）：
 *   隔离工作目录取 os.tmpdir()（WSL 的 /tmp 是**独立 tmpfs 挂载**）。Node 侧写脚本成功，
 *   Windows 的 blender.exe 却打不开，报
 *     OSError: Python file "\\wsl.localhost\<distro>\tmp\…\dsh_headless_*.py" could not be opened
 *   → 无头回执 resultJson=null。看起来像"租约/通道坏了"，其实是**目录不共享**；而 Node 侧
 *   fs.existsSync 永远说"在"（它在 WSL 命名空间里看），所以这个失败是静默的。
 *
 * 本测试不需要 Blender（真跑 Blender 的端到端在 lease_stale_selftest 的 D2/E 段）：
 *   A wslPathShared：按挂载表判定 Windows 可见性（/tmp、/run、/dev/shm = false；发行版根文件系统 = true）
 *   B describeConfig().workDir 必须带 shared 字段（自证入口）
 *   C requested=不共享 → writeHeadlessScript 必须换到共享目录，并给出 substituted/note
 *   D requested=共享   → 不许乱换（substituted=false，used=requested）
 *
 * 用法：node tests/workdir_shared_selftest.mjs
 */
import { spawnSync } from 'node:child_process'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { wslPathShared, describeConfig } from '../runtime/config.mjs'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const PKG = path.join(HERE, '..')

let pass = 0, fail = 0
const failures = []
function ok(name, cond, detail) {
  if (cond) { pass++; console.log('  ✓ ' + name) }
  else { fail++; failures.push(name); console.log('  ✗ ' + name + (detail ? (' — ' + String(detail).slice(0, 240)) : '')) }
}

/** 在子进程里以指定 DSH_BLENDER_WORKDIR 调 writeHeadlessScript，回它的 staging */
function stageWith(workdir) {
  const code = [
    "import { writeHeadlessScript } from './runtime/engine.mjs';",
    "import fs from 'node:fs';",
    "const r = writeHeadlessScript('print(\"probe\")');",
    "console.log(JSON.stringify({ staging: r.staging, win: r.win, wsl: r.wsl, exists: fs.existsSync(r.wsl) }));",
  ].join('\n')
  const r = spawnSync(process.execPath, ['--input-type=module', '-e', code], {
    cwd: PKG, encoding: 'utf8',
    env: Object.assign({}, process.env, { DSH_BLENDER_WORKDIR: workdir }),
  })
  const line = String(r.stdout || '').trim().split('\n').filter(Boolean).pop() || ''
  try { return JSON.parse(line) } catch (e) { return { error: String((e && e.message) || e), stdout: String(r.stdout).slice(-400), stderr: String(r.stderr).slice(-400) } }
}

console.log('== workDir 共享性自证 ==')
console.log('平台 ' + process.platform + ' · home ' + os.homedir())

// ── A：挂载表判定
const a1 = wslPathShared('/tmp/dsh-x')
const a2 = wslPathShared(path.join(os.homedir(), 'dsh-x'))
const a3 = wslPathShared('/dev/shm/dsh-x')
if (process.platform === 'win32') {
  ok('A1 Windows 平台恒判共享（平台无此问题）', a1.shared === true, JSON.stringify(a1))
} else {
  // /tmp 在不同发行版可能是独立挂载、也可能就在根文件系统上 ⇒ 不该硬编码结论，只验「判定自洽」
  ok('A1 判定自洽（shared 能被 why 解释：distro-root-fs ⇒ 共享；独立挂载 ⇒ 不共享）',
    a1.shared === true ? a1.why === 'distro-root-fs'
      : (a1.shared === false && typeof a1.why === 'string' && a1.why.length > 0),
    JSON.stringify(a1))
  ok('A2 home 下的路径判为共享（发行版根文件系统 → \\\\wsl.localhost\\<distro>\\…）',
    a2.shared === true && a2.why === 'distro-root-fs', JSON.stringify(a2))
  ok('A3 /dev/shm 同判不共享（另一个独立挂载）', a3.shared === false, JSON.stringify(a3))
  ok('A4 相对路径判不了 → null（不硬猜）', wslPathShared('relative/x').shared === null, JSON.stringify(wslPathShared('relative/x')))
  ok('A5 判定必须带 why（可解释，不是布尔玄学）', typeof a1.why === 'string' && a1.why.length > 0, JSON.stringify(a1))
}

// ── B：自证入口
const cfg = describeConfig({})
ok('B1 describeConfig().workDir.shared 存在（/doctor 能报）',
  Object.prototype.hasOwnProperty.call(cfg.workDir || {}, 'shared'), JSON.stringify(cfg.workDir))

// ── C：requested 不共享 → 必须换（且说清）
const sharedProbe = path.join(PKG, 'tmp', 'workdir-shared-probe')
// 探针目录**动态选到真正的非共享挂载**（本机 /dev/shm 是独立挂载）⇒ 替换分支才真的被跑到；
// 若本机没有非共享挂载，就退回 /tmp 并走「已共享 ⇒ 不得替换」那条分支（同样有断言）
const nonSharedProbe = (process.platform !== 'win32' && wslPathShared('/dev/shm/dsh-x').shared === false)
  ? '/dev/shm/dsh-workdir-probe'
  : '/tmp/dsh-workdir-probe'
const c = stageWith(nonSharedProbe)
if (c.error) {
  ok('C1 子进程能跑 writeHeadlessScript', false, JSON.stringify(c))
} else {
  const st = c.staging || {}
  ok('C1 脚本文件真的写出来了', c.exists === true, c.wsl)
  // 契约按 requestedShared 分支验（两条分支都是硬断言，不因机器而异）
  const reqShared = st.requestedShared === true
  ok('C2 替换契约：requested 不共享 ⇒ 必须换成共享目录；已共享 ⇒ 必须不替换',
    reqShared ? (st.substituted === false && st.used === st.requested && st.usedShared === true)
      : (st.substituted === true && st.usedShared === true && st.used !== st.requested),
    JSON.stringify(st))
  ok('C3 回执带可读说明（替换时报原因+落点；已共享时报检查通过）',
    typeof st.note === 'string' && st.note.length > 0
    && (reqShared ? st.note.indexOf('共享') >= 0
      : (st.note.indexOf(st.used) >= 0 && st.note.indexOf('不保证可见') >= 0)), st.note)
  ok('C4 requestedShared 被记进回执（布尔、可对拍）',
    typeof st.requestedShared === 'boolean' && typeof st.usedShared === 'boolean', JSON.stringify(st))
  ok('C5 回执里的 Windows 路径 = 替换后目录（usedWin 与 win 一致，别让调用方拿到旧 UNC）',
    !!st.usedWin && st.usedWin === c.win && String(c.win).indexOf(st.used.split(path.sep).pop()) >= 0,
    JSON.stringify({ win: c.win, usedWin: st.usedWin, used: st.used }))
}

// ── D：requested 已共享 → 不许多余替换
fs.mkdirSync(sharedProbe, { recursive: true })
const d = stageWith(sharedProbe)
if (d.error) {
  ok('D1 子进程能跑 writeHeadlessScript（共享 requested）', false, JSON.stringify(d))
} else {
  const st = d.staging || {}
  ok('D1 共享 workDir 不被替换（substituted=false）', st.substituted === false, JSON.stringify(st))
  ok('D2 脚本就落在 requested 目录里', st.used === sharedProbe && String(d.wsl).indexOf(sharedProbe) === 0,
    JSON.stringify({ used: st.used, wsl: d.wsl }))
  ok('D3 note 说明共享性检查通过', typeof st.note === 'string' && st.note.indexOf('通过') >= 0, st.note)
}

// 清理本测试造出来的目录
for (const p of [sharedProbe, path.join(PKG, 'tmp', 'rt-probe'), path.join(PKG, 'runtime', 'tmp')]) {
  try { fs.rmSync(p, { recursive: true, force: true }) } catch (e) { /* ignore */ }
}

console.log('')
console.log('─'.repeat(64))
console.log('workdir-shared-selftest：' + pass + ' 通过 / ' + fail + ' 失败')
if (fail) { for (const f of failures) console.log('  - ' + f); process.exit(1) }
console.log('OK：不共享的 workDir 会被换成共享目录并在回执里点名；共享的不会被乱动。')
process.exit(0)
