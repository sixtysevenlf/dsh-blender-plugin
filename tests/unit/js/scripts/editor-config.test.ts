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

const ALL_SETTINGS = {
  'js/ts.tsdk.path': 'node_modules/typescript/lib',
  'js/ts.tsdk.promptToUseWorkspaceVersion': true,
  'editor.defaultFormatter': 'esbenp.prettier-vscode',
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

  it('accepts a settings file that already has every key', () => {
    const plan = planEditorConfig({
      ...BASE,
      exists: true,
      settingsText: JSON.stringify(ALL_SETTINGS),
      checkOnly: true,
    })
    expect(plan.action).toBe('check-ok')
  })

  it('names the missing key when the existing file is partial', () => {
    const settingsText = JSON.stringify({ 'js/ts.tsdk.path': 'node_modules/typescript/lib' })
    const plan = planEditorConfig({ ...BASE, exists: true, settingsText, checkOnly: true })
    expect(plan.action).toBe('check-partial')
    expect(plan.message).toContain('js/ts.tsdk.promptToUseWorkspaceVersion')
    expect(plan.message).toContain('editor.defaultFormatter')
  })

  it('creates valid JSON with the TypeScript and formatter keys when the file is absent', () => {
    const plan = planEditorConfig(BASE)
    expect(plan.action).toBe('create')
    expect(plan.content).toBe(EDITOR_SETTINGS_CONTENT)
    expect(plan.content.endsWith('\n')).toBe(true)
    const parsed = JSON.parse(plan.content)
    expect(Object.keys(parsed).sort()).toEqual([...EDITOR_SETTINGS_KEYS].sort())
    expect(parsed['js/ts.tsdk.path']).toBe('node_modules/typescript/lib')
    expect(parsed['js/ts.tsdk.promptToUseWorkspaceVersion']).toBe(true)
    expect(parsed['editor.defaultFormatter']).toBe('esbenp.prettier-vscode')
    expect(plan.content).not.toContain('typescript.tsdk')
    expect(plan.content).not.toContain('typescript.enablePromptUseWorkspaceTsdk')
  })

  it('merges missing keys into an existing JSONC file without losing comments or values', () => {
    const settingsText =
      '{\n  // local settings\n  "workbench.colorTheme": "Default Dark Modern"\n}\n'
    const plan = planEditorConfig({ ...BASE, exists: true, settingsText })
    expect(plan.action).toBe('merge')
    expect(plan.content).toContain('// local settings')
    expect(plan.content).toContain('"workbench.colorTheme": "Default Dark Modern"')
    const parsed = JSON.parse(plan.content.replace(/\/\/.*$/gm, ''))
    expect(parsed['workbench.colorTheme']).toBe('Default Dark Modern')
    expect(parsed['editor.defaultFormatter']).toBe('esbenp.prettier-vscode')
    expect(parsed['js/ts.tsdk.path']).toBe('node_modules/typescript/lib')
  })

  it('merges into a single-line object without duplicating an existing key', () => {
    const settingsText = '{"workbench.colorTheme":"Default Dark Modern"}'
    const plan = planEditorConfig({ ...BASE, exists: true, settingsText })
    expect(plan.action).toBe('merge')
    const parsed = JSON.parse(plan.content)
    expect(parsed['workbench.colorTheme']).toBe('Default Dark Modern')
    expect(parsed['editor.defaultFormatter']).toBe('esbenp.prettier-vscode')
  })

  it('never overwrites an existing file that already has every key', () => {
    const plan = planEditorConfig({
      ...BASE,
      exists: true,
      settingsText: JSON.stringify(ALL_SETTINGS, null, 2),
    })
    expect(plan.action).toBe('manual')
    expect(plan.content).toBeUndefined()
  })

  it('falls back to the manual snippet when the existing file cannot be edited safely', () => {
    const plan = planEditorConfig({
      ...BASE,
      exists: true,
      settingsText: 'not a settings object',
    })
    expect(plan.action).toBe('manual')
    expect(plan.content).toBeUndefined()
    expect(plan.message).toContain('editor.defaultFormatter')
  })
})
