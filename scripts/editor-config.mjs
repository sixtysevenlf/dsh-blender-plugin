/**
 * scripts/editor-config.mjs — pure planning for the optional VS Code settings bootstrap.
 *
 * `npm run setup:dev` writes `.vscode/settings.json` only when the file is missing: it belongs
 * to the developer, may be JSONC with comments, and is gitignored. The decision is a pure
 * function so the unit tests can cover every branch without touching a real editor.
 */

export const EDITOR_SETTINGS_KEYS = ['js/ts.tsdk.path', 'js/ts.tsdk.promptToUseWorkspaceVersion']

export const EDITOR_SETTINGS_CONTENT =
  JSON.stringify(
    {
      'js/ts.tsdk.path': 'node_modules/typescript/lib',
      'js/ts.tsdk.promptToUseWorkspaceVersion': true,
    },
    null,
    2,
  ) + '\n'

function hasKey(text, key) {
  return typeof text === 'string' && text.includes('"' + key + '"')
}

/**
 * Decide what `setup-dev` should do about editor settings.
 *
 * @param {{ settingsPath?: string, settingsText?: string, exists?: boolean, checkOnly?: boolean, ci?: boolean }} options
 * @returns {{ action: 'skip-ci' | 'check-ok' | 'check-missing' | 'check-partial' | 'create' | 'manual', content?: string, message: string }}
 */
export function planEditorConfig(options) {
  const input = options || {}
  const label = input.settingsPath || '.vscode/settings.json'
  const exists = Boolean(input.exists)
  const checkOnly = Boolean(input.checkOnly)

  if (input.ci) return { action: 'skip-ci', message: 'CI: skip editor settings' }

  const missing = EDITOR_SETTINGS_KEYS.filter(function (key) {
    return !hasKey(input.settingsText, key)
  })

  if (checkOnly) {
    if (!exists) {
      return {
        action: 'check-missing',
        message:
          'editor settings: optional, not created (' +
          label +
          '); run npm run setup:dev to create it',
      }
    }
    if (missing.length === 0) {
      return {
        action: 'check-ok',
        message: 'editor settings: ' + label + ' points at the workspace TypeScript',
      }
    }
    return {
      action: 'check-partial',
      message:
        'editor settings: ' + label + ' is missing ' + missing.join(', ') + '; add them manually',
    }
  }

  if (!exists) {
    return {
      action: 'create',
      content: EDITOR_SETTINGS_CONTENT,
      message:
        'editor settings: created ' +
        label +
        ' (VS Code: "TypeScript: Select TypeScript Version" -> "Use Workspace Version")',
    }
  }

  if (missing.length === 0) {
    return {
      action: 'manual',
      message: 'editor settings: ' + label + ' already configures the workspace TypeScript',
    }
  }
  return {
    action: 'manual',
    message:
      'editor settings: ' +
      label +
      ' exists; add these keys manually: ' +
      missing.join(', ') +
      '\n  ' +
      EDITOR_SETTINGS_CONTENT.trim().replace(/\n/g, '\n  '),
  }
}
