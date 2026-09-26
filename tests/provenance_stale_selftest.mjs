#!/usr/bin/env node
/**
 * tests/provenance_stale_selftest.mjs —— v0.9.6（D3）：**加载版本自证**必须靠内容指纹，不能靠 mtime
 *
 * 现场踩到（2026-09-26）：catalog 回"26 个 family / 161 个 op"，但磁盘上 vehicle 一族早就在了 ——
 * 旧自证是 `size + '-' + mtime` 的字符串，两个方向都证明不了：
 *   · touch 一下 mtime 就变，内容没变   → 假 stale；
 *   · 同一秒内重写（或 copy 保时间戳）  → 假 fresh。
 *
 * 本测试**不改动真源码**：把 runtime/*.mjs 拷到临时目录，对**副本**做三件事：
 *   A 刚加载 → verdict=current
 *   B 只改 mtime（utimes，内容逐字节不变）→ 仍必须 current（这正是"mtime 不能冒充加载版本"）
 *   C 真改内容（动 PLAN_CATALOG 一族名 + 另一次只加注释）→ verdict=stale，且
 *     `verify:true` 起干净进程读磁盘当裁判：只加注释时目录指纹不变（matchesLoadedCatalog=true），
 *     动了目录内容时干净进程的指纹与进程内不一致（matchesLoadedCatalog=false）→ catalog 文本点名 FAIL。
 *
 * 用法：node tests/provenance_stale_selftest.mjs
 */
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { createHash } from 'node:crypto'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const PKG = path.join(HERE, '..')
const RT = path.join(PKG, 'runtime')

let pass = 0, fail = 0
const failures = []
function ok(name, cond, detail) {
  if (cond) { pass++; console.log('  ✓ ' + name) }
  else { fail++; failures.push(name); console.log('  ✗ ' + name + (detail ? (' — ' + String(detail).slice(0, 260)) : '')) }
}

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'dsh-prov-stale-'))
const COPY_RT = path.join(tmp, 'runtime')
fs.mkdirSync(COPY_RT, { recursive: true })
for (const f of fs.readdirSync(RT)) {
  if (f.endsWith('.mjs')) fs.copyFileSync(path.join(RT, f), path.join(COPY_RT, f))
}
fs.writeFileSync(path.join(tmp, 'package.json'), JSON.stringify({ name: 'dsh-provenance-probe', version: '0.0.0-probe' }), 'utf8')
const COPY_ENGINE = path.join(COPY_RT, 'engine.mjs')
const md5 = (p) => createHash('md5').update(fs.readFileSync(p)).digest('hex')
const origMd5 = md5(COPY_ENGINE)

console.log('== 加载版本自证（内容指纹 vs mtime）==')
console.log('副本：' + COPY_ENGINE)

const m = await import(pathToFileURL(COPY_ENGINE).href + '?probe=' + Date.now())
const orig = fs.readFileSync(COPY_ENGINE, 'utf8')

// ── A：刚加载 = current
const pr0 = m.engineProvenance({})
ok('A1 刚加载 → verdict=current、stale=false', pr0.verdict === 'current' && pr0.stale === false, JSON.stringify(pr0))
ok('A2 loadedHash === currentHash（同为 sha256:12 内容指纹）',
  !!pr0.loadedHash && pr0.loadedHash === pr0.currentHash && /^[0-9a-f]{12}$/.test(pr0.loadedHash), pr0.loadedHash)
ok('A3 回执写明判定口径是内容哈希、不含 mtime', String(pr0.note).indexOf('mtime') >= 0 && pr0.hashAlgo === 'sha256:12', pr0.note)
ok('A4 catalog 文本是"目录自证：…一致"，没有 FAIL 告警',
  m.catalogPayload({}).text.indexOf('目录自证') > 0 && m.catalogPayload({}).text.indexOf('目录自证 FAIL') < 0)

// ── B：只动 mtime（内容逐字节不变）→ 不许判 stale
const t = new Date(Date.now() + 3600 * 1000)
fs.utimesSync(COPY_ENGINE, t, t)
ok('B0 前置：mtime 确实变了、内容没变',
  md5(COPY_ENGINE) === origMd5 && fs.statSync(COPY_ENGINE).mtimeMs > Date.now(), String(md5(COPY_ENGINE)))
const pr1 = m.engineProvenance({})
ok('B1 只改 mtime → 仍然 current（mtime 不能冒充加载版本）', pr1.verdict === 'current' && pr1.stale === false,
  JSON.stringify({ verdict: pr1.verdict, cur: pr1.currentHash, loaded: pr1.loadedHash }))
ok('B2 currentMtime 会被如实报出来（参考字段，但不参与判定）',
  !!pr1.currentMtime && pr1.currentMtime !== pr0.currentMtime, pr1.currentMtime + ' vs ' + pr0.currentMtime)

// ── C1：只加注释 → 源码指纹变（stale），但**目录内容没变**（干净进程 catalogHash 相同）
fs.writeFileSync(COPY_ENGINE, orig + '\n// provenance-stale-probe\n', 'utf8')
const pr2 = m.engineProvenance({ verify: true })
ok('C1 内容真改了 → verdict=stale、stale=true', pr2.verdict === 'stale' && pr2.stale === true, JSON.stringify(pr2).slice(0, 220))
ok('C2 staleReason 里同时给出 loaded 与 current 两个指纹（可对拍）',
  String(pr2.staleReason).indexOf(pr2.loadedHash) >= 0 && String(pr2.staleReason).indexOf(pr2.currentHash) >= 0, pr2.staleReason)
ok('C3 干净进程读磁盘看到了这份改动（diskVerdict.ok）', !!(pr2.diskVerdict && pr2.diskVerdict.ok === true), JSON.stringify(pr2.diskVerdict).slice(0, 200))
ok('C4 只是注释 → 目录内容指纹不变 → matchesLoadedCatalog=true（stale 的是源码，不是目录）',
  pr2.diskVerdict.matchesLoadedCatalog === true, JSON.stringify(pr2.diskVerdict).slice(0, 200))

// ── C2：真动目录内容（改一族名）→ 干净进程的目录指纹必须与本进程不一致，并让 catalog 文本点名 FAIL
const mutated = fs.readFileSync(COPY_ENGINE, 'utf8').replace("{ f: 'sculpt',", "{ f: 'sculptx',")
ok('C5 前置：副本里确实替换了一族名', mutated !== fs.readFileSync(COPY_ENGINE, 'utf8'), 'replace 未命中')
fs.writeFileSync(COPY_ENGINE, mutated, 'utf8')
// 本进程内存里的 PLAN_CATALOG 仍是旧的一份（正是"后端没重启"的现场）
const catOld = m.catalogPayload({ verify: true })
ok('C6 干净进程看到磁盘上的新目录（family 数 27、sculptx 已在）',
  catOld.provenance.diskVerdict.families === 27 && catOld.provenance.diskVerdict.matchesLoadedCatalog === false,
  JSON.stringify(catOld.provenance.diskVerdict).slice(0, 260))
ok('C7 本进程 catalog 的 catalogHash 与磁盘不同 → 判定"本进程目录是旧版"',
  catOld.provenance.catalogHash !== catOld.provenance.diskVerdict.catalogHash,
  catOld.provenance.catalogHash + ' vs ' + catOld.provenance.diskVerdict.catalogHash)
ok('C8 文本给出 ⚠ 目录自证 FAIL（不再拿旧目录冒充当前版本）', catOld.text.indexOf('目录自证 FAIL') > 0,
  catOld.text.split('\n').filter((l) => l.indexOf('自证') >= 0).join(' | ').slice(0, 260))
ok('C9 verified 失败说明是"以磁盘那份为准"的可执行结论',
  String(catOld.provenance.diskVerdict.note).indexOf('重启后端') >= 0 || String(catOld.provenance.diskVerdict.note).indexOf('以干净进程那份为准') >= 0,
  catOld.provenance.diskVerdict.note)

try { fs.rmSync(tmp, { recursive: true, force: true }) } catch (e) { /* ignore */ }

console.log('')
console.log('─'.repeat(64))
console.log('provenance-stale-selftest：' + pass + ' 通过 / ' + fail + ' 失败')
if (fail) { for (const f of failures) console.log('  - ' + f); process.exit(1) }
console.log('OK：mtime 骗不了它，内容变了也跑不掉；干净进程当裁判，catalog 文本会点名 FAIL。')
process.exit(0)
