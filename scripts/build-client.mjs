#!/usr/bin/env node
/**
 * build-client.mjs —— 客户端半边构建：`src/client/index.js` → `lib/client.js`。
 *
 * 为什么是"拷贝"而不是打包：契约 §0 明确**不新增 npm 依赖、不引入打包器**
 * （本机到 github.com 不通，装新依赖不可靠）。`src/client/index.js` 本身就是
 * 合法的 `window.__ModuleLoader__.load({id, factory})` bundle，构建只做三件事：
 *   ① 把源文件原样写到产物路径（可 `--out` 改道，便于离线验证不碰 `lib/`）；
 *   ② 处理 `//# sourceMappingURL=`（去掉悬空引用；仅当同名 `.map` 真存在才重新指向它）；
 *   ③ 写回执（字节数 / sha256 前 12 位），让"产物是不是最新的"一眼可查。
 *
 * 由 `scripts/build.mjs all` 在 tsc 之后调用；`scripts/build.sh` 只是
 * deprecated 的 DSH_CHECKOUT 兼容入口。也可单独跑：
 *   node scripts/build-client.mjs                     # 写 lib/client.js
 *   node scripts/build-client.mjs --src X --out Y       # 换源/换产物（离线验证用）
 *   node scripts/build-client.mjs --out /tmp/c.js    # 写别处（离线验证用）
 *   node scripts/build-client.mjs --check             # 只校验产物是否最新（不写）
 *
 * 退出码：0 = 成功（或 `--check` 判定最新）；1 = 源缺失 / 产物过期 / 写失败。
 */
import fs from 'node:fs'
import path from 'node:path'
import crypto from 'node:crypto'
import { fileURLToPath } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const ROOT = path.join(HERE, '..')
const DEFAULT_SRC = path.join(ROOT, 'src', 'client', 'index.js')
const DEFAULT_OUT = path.join(ROOT, 'lib', 'client.js')

const argv = process.argv.slice(2)
function flagValue(name) {
  const i = argv.indexOf(name)
  if (i < 0) return null
  const v = argv[i + 1]
  if (v === undefined || v.startsWith('--')) {
    console.error('build-client: ' + name + ' 需要一个路径参数')
    process.exit(1)
  }
  return v
}
const CHECK_ONLY = argv.includes('--check')
const srcArg = flagValue('--src')
const outArg = flagValue('--out')
const SRC = srcArg ? path.resolve(process.cwd(), srcArg) : DEFAULT_SRC
const OUT = outArg ? path.resolve(process.cwd(), outArg) : DEFAULT_OUT

// ── 读源 ────────────────────────────────────────────────────────────────────
if (!fs.existsSync(SRC)) {
  console.error('build-client: 源文件不存在：' + path.relative(ROOT, SRC))
  console.error('  客户端半边应位于 src/client/index.js（契约 §2.2）。')
  process.exit(1)
}
let code = fs.readFileSync(SRC, 'utf8')

// ── sourceMappingURL 处理 ───────────────────────────────────────────────────
// 源文件里若带 `//# sourceMappingURL=...`（比如从别处拷来），一律先摘掉：
// 产物路径变了，旧引用就是悬空的（浏览器控制台会报 404，且 DevTools 会去拉错文件）。
const MAP_COMMENT_RE = /^[ \t]*\/\/[#@][ \t]*sourceMappingURL=[^\n]*$/gm
const stripped = code.replace(MAP_COMMENT_RE, '').replace(/\n{3,}$/, '\n')
const removedMapRefs = (code.match(MAP_COMMENT_RE) || []).length
code = stripped

// 只有当同名 `.map` 真实存在时才重新挂上引用（本仓库目前不产出 map，故通常不带）。
const mapPath = OUT + '.map'
let mapRef = null
if (fs.existsSync(mapPath)) {
  mapRef = path.basename(mapPath)
  if (!code.endsWith('\n')) code += '\n'
  code += '\n//# sourceMappingURL=' + mapRef + '\n'
}

const sha = crypto.createHash('sha256').update(code, 'utf8').digest('hex')
const bytes = Buffer.byteLength(code, 'utf8')
const rel = path.relative(ROOT, OUT) || OUT
const relSrc = path.relative(ROOT, SRC) || SRC

// ── --check：只比对产物是否最新 ─────────────────────────────────────────────
if (CHECK_ONLY) {
  if (!fs.existsSync(OUT)) {
    console.error('build-client --check: 产物不存在 → ' + rel)
    console.error('  修法：node scripts/build-client.mjs')
    process.exit(1)
  }
  const cur = fs.readFileSync(OUT, 'utf8')
  if (cur !== code) {
    console.error('build-client --check: 产物已过期 → ' + rel)
    console.error(
      '  期望 sha256 ' +
        sha.slice(0, 12) +
        ' / ' +
        bytes +
        ' B，实际 ' +
        crypto.createHash('sha256').update(cur, 'utf8').digest('hex').slice(0, 12) +
        ' / ' +
        Buffer.byteLength(cur, 'utf8') +
        ' B',
    )
    console.error('  修法：node scripts/build-client.mjs')
    process.exit(1)
  }
  console.log(
    'build-client --check: 最新 ✅  ' + rel + '  sha256 ' + sha.slice(0, 12) + ' / ' + bytes + ' B',
  )
  process.exit(0)
}

// ── 写产物 ──────────────────────────────────────────────────────────────────
try {
  fs.mkdirSync(path.dirname(OUT), { recursive: true })
  fs.writeFileSync(OUT, code, 'utf8')
} catch (e) {
  console.error('build-client: 写产物失败：' + (e && e.message ? e.message : String(e)))
  process.exit(1)
}

console.log('build-client: ' + relSrc + ' → ' + rel)
console.log('  · ' + bytes + ' B  sha256 ' + sha.slice(0, 12))
if (removedMapRefs > 0) console.log('  · 摘掉 ' + removedMapRefs + ' 处悬空 sourceMappingURL 引用')
console.log('  · sourceMappingURL：' + (mapRef ? mapRef : '（无 .map，未挂引用）'))
