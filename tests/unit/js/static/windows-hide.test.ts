import { describe, expect, it } from 'vitest'
import fs from 'node:fs'
import path from 'node:path'
import { pkgPath } from '../../../helpers/pkg'

// Every child_process call in runtime code must pass windowsHide: true (or
// detached: true for the GUI launcher, which has no console). Static scan,
// because relying on reviewers to remember this rule fails.

const METHODS = ['spawn', 'spawnSync', 'execFile', 'execFileSync', 'exec', 'execSync', 'fork']

function isRegexStart(src: string, i: number): boolean {
  let j = i - 1
  while (j >= 0 && /\\s/.test(src[j])) j--
  if (j < 0) return true
  const c = src[j]
  if ('([{,;:=!&|?+-*%<>~^'.indexOf(c) >= 0) return true
  let k = j
  while (k >= 0 && /[\\w$]/.test(src[k])) k--
  return /^(return|typeof|instanceof|in|of|new|delete|void|do|else|case|yield|await)$/.test(src.slice(k + 1, j + 1))
}

function maskStringsAndComments(src: string): string {
  const blank = (s: string) => s.replace(/[^\n]/g, ' ')
  let out = ''
  let i = 0
  const n = src.length
  while (i < n) {
    const c = src[i]
    if (c === '/' && src[i + 1] === '/') {
      let j = i
      while (j < n && src[j] !== '\n') j++
      out += blank(src.slice(i, j)); i = j; continue
    }
    if (c === '/' && src[i + 1] === '*') {
      let j = i + 2
      while (j < n && !(src[j] === '*' && src[j + 1] === '/')) j++
      j = Math.min(n, j + 2)
      out += blank(src.slice(i, j)); i = j; continue
    }
    if (c === '/' && isRegexStart(src, i)) {
      let j = i + 1
      let inClass = false
      while (j < n) {
        if (src[j] === '\\') { j += 2; continue }
        if (src[j] === '\n') { j++; break }
        if (src[j] === '[') inClass = true
        else if (src[j] === ']') inClass = false
        else if (src[j] === '/' && !inClass) { j++; break }
        j++
      }
      out += blank(src.slice(i, j)); i = j; continue
    }
    if (c === "'" || c === '"' || c === '\x60') {
      let j = i + 1
      while (j < n) {
        if (src[j] === '\\') { j += 2; continue }
        if (src[j] === c) { j++; break }
        j++
      }
      out += blank(src.slice(i, j)); i = j; continue
    }
    out += c; i++
  }
  return out
}

function matchingClose(masked: string, openIdx: number): number {
  let depth = 0
  for (let i = openIdx; i < masked.length; i++) {
    if (masked[i] === '(') depth++
    else if (masked[i] === ')') { depth--; if (depth === 0) return i }
  }
  return -1
}

function runtimeFiles(): string[] {
  const files: string[] = []
  const walk = (dir: string) => {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, entry.name)
      if (entry.isDirectory()) { if (entry.name !== 'ext') walk(full) }
      else if (entry.name.endsWith('.mjs')) files.push(full)
    }
  }
  walk(pkgPath('runtime'))
  return files.sort()
}

describe('windowsHide static scan', () => {
  it('passes windowsHide (or detached) on every console-creating child_process call', () => {
    const fails: string[] = []
    for (const file of runtimeFiles()) {
      const src = fs.readFileSync(file, 'utf8')
      const masked = maskStringsAndComments(src)
      const direct = new Set<string>()
      for (const match of src.matchAll(/import\s*\{([^}]*)\}\s*from\s*['"]node:child_process['"]/g)) {
        for (const part of match[1].split(',')) {
          const bits = part.trim().split(/\s+as\s+/)
          const local = bits[bits.length - 1].trim()
          if (local) direct.add(local)
        }
      }
      const aliases = new Set<string>()
      for (const match of src.matchAll(/const\s+([A-Za-z_$][\w$]*)\s*=\s*await\s+import\s*\(\s*['"]node:child_process['"]\s*\)/g)) aliases.add(match[1])
      if (!direct.size && !aliases.size) continue
      const patterns: Array<{ re: RegExp; label: string }> = []
      for (const name of direct) patterns.push({ re: new RegExp('\\b' + name + '\\s*\\(', 'g'), label: name })
      for (const alias of aliases) patterns.push({ re: new RegExp('\\b' + alias + '\\s*\\.\\s*(?:' + METHODS.join('|') + ')\\s*\\(', 'g'), label: alias + '.*' })
      for (const { re, label } of patterns) {
        let match: RegExpExecArray | null
        while ((match = re.exec(masked)) !== null) {
          const before = match.index > 0 ? masked[match.index - 1] : ''
          if (/[.\w$]/.test(before)) continue
          const openIdx = match.index + match[0].length - 1
          const closeIdx = matchingClose(masked, openIdx)
          const line = String(src.slice(0, match.index).split('\n').length)
          if (closeIdx < 0) { fails.push(path.relative(pkgPath(), file) + ':' + line + ' ' + label + ' unbalanced'); continue }
          const call = src.slice(match.index, closeIdx + 1)
          if (!/windowsHide\s*:/.test(call) && !/detached\s*:\s*true/.test(call)) {
            fails.push(path.relative(pkgPath(), file) + ':' + line + ' ' + label + ' lacks windowsHide')
          }
        }
      }
    }
    expect(fails).toEqual([])
  })
})
