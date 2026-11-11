import { describe, it, expect } from 'vitest'
import {
  EDITOR_SETTINGS_CONTENT,
  EDITOR_SETTINGS_KEYS,
  planEditorConfig,
} from '../../../../scripts/editor-config.mjs'

const BASE = {
  settingsPath: '.vscode/settings.json',
  settingsText: '',
  exists: false,
  checkOnly: false,
  ci: false,
}

describe('editor settings plan', () => {
  it('skips in CI', () => {
    const plan = planEditorConfig({ ...BASE, ci: true })
    expect(plan.action).toBe('skip-ci')
    expect(plan.content).toBeUndefined()
  })

  it('reports missing settings as optional under --check', () => {
    const plan = planEditorConfig({ ...BASE, checkOnly: true })
    expect(plan.action).toBe('check-missing')
    expect(plan.content).toBeUndefined()
  })

  it('accepts a settings file that already has both keys', () => {
    const settingsText = JSON.stringify({
      'js/ts.tsdk.path': 'node_modules/typescript/lib',
      'js/ts.tsdk.promptToUseWorkspaceVersion': true,
    })
    const plan = planEditorConfig({ ...BASE, exists: true, settingsText, checkOnly: true })
    expect(plan.action).toBe('check-ok')
  })

  it('names the missing key when the existing file is partial', () => {
    const settingsText = JSON.stringify({ 'js/ts.tsdk.path': 'node_modules/typescript/lib' })
    const plan = planEditorConfig({ ...BASE, exists: true, settingsText, checkOnly: true })
    expect(plan.action).toBe('check-partial')
    expect(plan.message).toContain('js/ts.tsdk.promptToUseWorkspaceVersion')
  })

  it('creates valid JSONC-safe content when the file does not exist', () => {
    const plan = planEditorConfig(BASE)
    expect(plan.action).toBe('create')
    expect(plan.content).toBe(EDITOR_SETTINGS_CONTENT)
    expect(plan.content.endsWith('\n')).toBe(true)
    const parsed = JSON.parse(plan.content)
    expect(Object.keys(parsed).sort()).toEqual([...EDITOR_SETTINGS_KEYS].sort())
    expect(parsed['js/ts.tsdk.path']).toBe('node_modules/typescript/lib')
    expect(parsed['js/ts.tsdk.promptToUseWorkspaceVersion']).toBe(true)
    expect(plan.content).not.toContain('typescript.tsdk')
    expect(plan.content).not.toContain('typescript.enablePromptUseWorkspaceTsdk')
  })

  it('never overwrites an existing file and points at the manual keys', () => {
    const plan = planEditorConfig({
      ...BASE,
      exists: true,
      settingsText: '{\n  // local settings\n}\n',
    })
    expect(plan.action).toBe('manual')
    expect(plan.content).toBeUndefined()
    expect(plan.message).toContain('js/ts.tsdk.path')
    expect(plan.message).toContain('js/ts.tsdk.promptToUseWorkspaceVersion')
  })
})
