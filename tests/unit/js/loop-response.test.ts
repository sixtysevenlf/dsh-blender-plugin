import { describe, expect, it } from 'vitest'
import { loopSessionError, loopSessionText } from '../../../src/tools/loop/response.js'

describe('loop response v2.6.2 text contract', () => {
  it('keeps the legacy session failure text', () => {
    expect(loopSessionError('start', 'dsh-loop', 'boom')).toBe('LOOP session 失败 · boom')
  })

  it('keeps the session success text', () => {
    expect(loopSessionText('status', 'dsh-loop', { ok: true })).toBe(
      'LOOP status (session=dsh-loop) ok · {"ok":true}',
    )
  })
})
