// stdout 标记解析 / 路径改写
import fs from 'node:fs'
import { winToWsl } from '../../config.js'

function extractTag(out: unknown, tag: string): unknown {
  const rec = out && typeof out === 'object' ? (out as Record<string, unknown>) : null
  const txt = rec && typeof rec.result === 'string' ? rec.result : ''
  const m = txt.match(new RegExp(tag + ' ([\\s\\S]*)'))
  if (!m) throw new Error(tag + ' 无回包: ' + txt.slice(0, 200))
  return JSON.parse(m[1])
}

function extractLoop(out: unknown): unknown {
  return extractTag(out, 'LOOP')
}

function jsonFromStdout(out: unknown): unknown {
  const rec = out && typeof out === 'object' ? (out as Record<string, unknown>) : null
  const txt =
    rec && typeof rec.result === 'string'
      ? rec.result
      : typeof out === 'string'
        ? out
        : JSON.stringify(out)
  const m = txt.match(/\{[\s\S]*\}/)
  return m ? JSON.parse(m[0]) : null
}

/** 路径映射见 config.mjs：winToWsl / wslToWin（Windows 上原样返回） */

/** 无头运行用的 python 脚本落到 Windows 可见目录；/mnt 不可写时退回包内 tmp + UNC */
/** v0.8.10：长输出"头+尾+省略标记"—— 旧版只回尾部，长 JSON 的头被吃掉 */
function clipMiddle(s: unknown, head: number, tail: number): string {
  const t = String(s == null ? '' : s)
  if (t.length <= head + tail) return t
  return (
    t.slice(0, head) +
    '\n…[省略 ' +
    String(t.length - head - tail) +
    ' 字符；全文见 logs 路径]…\n' +
    t.slice(-tail)
  )
}

/**
 * v0.9.6（现场反馈：WSL 绝对路径的"二次前缀"）：`home/.../DSH/测试/x.py` 这种**漏了开头 /** 的路径
 * 会被当相对路径接到 cwd 后面，得到 `...DSH\\home\\sixtyseven67\\DSH\\测试\\x.py`。
 * 判据：补一个 "/" 之后确实存在 → 纠正并记一笔（同时写后端 stderr，便于排错）。
 */
function slipFixPath(p: unknown, notes?: string[] | null): string {
  const s = String(p == null ? '' : p)
  if (!s || s.startsWith('/') || s.startsWith('\\\\') || /^[A-Za-z]:[\\/]/.test(s)) return s
  if (!/^(home|mnt|tmp|usr|opt|srv|var|etc|root|data)\//.test(s)) return s
  const cand = '/' + s
  let hit: boolean
  try {
    hit = fs.existsSync(winToWsl(cand)) || fs.existsSync(cand)
  } catch (e) {
    hit = false
  }
  if (!hit) return s
  const msg = 'WSL 绝对路径漏了开头的 "/"：' + s + ' → 已按 ' + cand + ' 处理'
  if (notes) notes.push(msg)
  try {
    process.stderr.write('[dsh-blender] ' + msg + String.fromCharCode(10))
  } catch (e) {}
  return cand
}

export { extractTag, extractLoop, jsonFromStdout, clipMiddle, slipFixPath }
