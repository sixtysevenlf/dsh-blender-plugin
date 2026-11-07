import { describe, expect, it } from 'vitest'
import { formatLaunch } from '../../../src/tools/viewport/presenter.js'

describe('launch addon install state', () => {
  it('prints the structured install issue and its fix from the boot status', () => {
    const out = formatLaunch({
      ok: false,
      launched: true,
      boot: {
        addon_issue: {
          code: 'addon_not_installed',
          detail: 'no dsh_blender_bridge module loaded',
          fix: 'Install addon/dsh_blender_bridge.zip, then relaunch GUI Blender.',
        },
      },
    })
    expect(out).toContain('addon 状态：addon_not_installed')
    expect(out).toContain('no dsh_blender_bridge module loaded')
    expect(out).toContain('修法：Install addon/dsh_blender_bridge.zip')
  })

  it('does not invent an issue when the boot status has none', () => {
    const out = formatLaunch({ ok: true, launched: true, boot: { addon_ok: true } })
    expect(out).not.toContain('addon 状态：')
  })
})
