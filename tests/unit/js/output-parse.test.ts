import { afterEach, describe, expect, it, vi } from 'vitest'
import fs from 'node:fs'
import {
  clipMiddle,
  extractLoop,
  extractTag,
  jsonFromStdout,
  slipFixPath,
} from '../../../runtime/core/output/parse.js'

// F4.1: F1.1 split these pure helpers out of runtime/core/output.ts; they are
// imported directly here so a regression in one phase cannot hide behind the
// tool-level tests that only exercise the assembled output path.

afterEach(() => vi.restoreAllMocks())

describe('output/parse extractTag', () => {
  it('parses the JSON payload after the marker', () => {
    expect(extractTag({ result: 'noise\nLOOP {"i": 3}' }, 'LOOP')).toEqual({ i: 3 })
    expect(extractLoop({ result: 'HEAD\nLOOP [1, 2]' })).toEqual([1, 2])
  })

  it('names the missing marker and keeps the first 200 characters', () => {
    expect(() => extractTag({ result: 'only text' }, 'HEADLESS')).toThrow(
      /HEADLESS 无回包: only text/,
    )
    expect(() => extractTag('plain string', 'DSH')).toThrow(/DSH 无回包/)
  })
})

describe('output/parse jsonFromStdout', () => {
  it('accepts the three shapes the backend actually returns', () => {
    expect(jsonFromStdout({ result: '{"ok":true,"n":1}' })).toEqual({ ok: true, n: 1 })
    expect(jsonFromStdout('prefix {"a":[1]} suffix')).toEqual({ a: [1] })
    expect(jsonFromStdout({ ok: true, n: 2 })).toEqual({ ok: true, n: 2 })
  })

  it('returns null when no object literal is present', () => {
    expect(jsonFromStdout({ result: 'no json here' })).toBeNull()
    expect(jsonFromStdout('')).toBeNull()
    expect(jsonFromStdout(null)).toBeNull()
  })
})

describe('output/parse clipMiddle', () => {
  it('keeps short output intact', () => {
    expect(clipMiddle('short', 10, 10)).toBe('short')
    expect(clipMiddle(undefined, 10, 10)).toBe('')
  })

  it('keeps head and tail with an explicit omission marker', () => {
    const text = 'A'.repeat(40) + 'B'.repeat(40)
    const out = clipMiddle(text, 10, 10)
    expect(out.startsWith('A'.repeat(10))).toBe(true)
    expect(out.endsWith('B'.repeat(10))).toBe(true)
    expect(out).toContain('…[省略 60 字符；全文见 logs 路径]…')
    expect(out.length).toBeLessThan(text.length)
  })
})

describe('output/parse slipFixPath', () => {
  it('leaves absolute, UNC and Windows paths alone', () => {
    expect(slipFixPath('/home/x.py')).toBe('/home/x.py')
    expect(slipFixPath('\\\\server\\share\\x.py')).toBe('\\\\server\\share\\x.py')
    expect(slipFixPath('D:\\work\\x.py')).toBe('D:\\work\\x.py')
    expect(slipFixPath('')).toBe('')
  })

  it('only rewrites the known root names when the corrected path exists', () => {
    const exists = vi.spyOn(fs, 'existsSync').mockReturnValue(true)
    const notes: string[] = []
    expect(slipFixPath('home/someone/DSH/测试/x.py', notes)).toBe('/home/someone/DSH/测试/x.py')
    expect(notes).toHaveLength(1)
    expect(notes[0]).toContain('WSL 绝对路径漏了开头的 "/"')
    expect(exists).toHaveBeenCalled()

    const notes2: string[] = []
    expect(slipFixPath('relative/path.py', notes2)).toBe('relative/path.py')
    expect(notes2).toHaveLength(0)
  })

  it('does not rewrite when the candidate does not exist', () => {
    vi.spyOn(fs, 'existsSync').mockReturnValue(false)
    const notes: string[] = []
    expect(slipFixPath('home/x.py', notes)).toBe('home/x.py')
    expect(notes).toHaveLength(0)
  })
})
