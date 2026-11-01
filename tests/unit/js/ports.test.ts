import { describe, expect, it } from 'vitest'
import { parsePortArg } from '../../../runtime/ports.js'

describe('parsePortArg legacy semantics', () => {
  it('returns null only when the flag is absent', () => {
    expect(parsePortArg([])).toBeNull()
    expect(parsePortArg(['--other', '9877'])).toBeNull()
  })

  it('passes the next argv token through Number(), like the pre-F1.3 code', () => {
    expect(parsePortArg(['--port', '9877'])).toBe(9877)
    expect(parsePortArg(['--port', '0'])).toBe(0)
    expect(parsePortArg(['--port', '-1'])).toBe(-1)
    expect(parsePortArg(['--port', '12.5'])).toBe(12.5)
    expect(parsePortArg(['--port', 'abc'])).toBeNaN()
    expect(parsePortArg(['--port'])).toBeNaN()
  })

  it('supports a custom flag token and ignores substrings', () => {
    expect(parsePortArg(['--http-port', '9000'], '--http-port')).toBe(9000)
    expect(parsePortArg(['--x--port', '9000'])).toBeNull()
  })
})
