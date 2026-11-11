/**
 * scripts/editor-config.mjs — pure planning for the optional VS Code settings bootstrap.
 *
 * `npm run setup:dev` creates `.vscode/settings.json` when it is missing and adds
 * only missing keys when it already exists. The file belongs to the developer and
 * may be JSONC, so merging preserves comments and never rewrites values that are
 * already present. The decision is a pure function so unit tests cover every branch.
 */

const EDITOR_SETTINGS = {
  'js/ts.tsdk.path': 'node_modules/typescript/lib',
  'js/ts.tsdk.promptToUseWorkspaceVersion': true,
  'editor.defaultFormatter': 'esbenp.prettier-vscode',
}

export const EDITOR_SETTINGS_KEYS = Object.keys(EDITOR_SETTINGS)

export const EDITOR_SETTINGS_CONTENT = JSON.stringify(EDITOR_SETTINGS, null, 2) + '\n'

function hasKey(text, key) {
  return typeof text === 'string' && text.includes('"' + key + '"')
}

/** Skip whitespace and JSONC comments. Returns the next significant index. */
function skipTrivia(text, start) {
  let index = start
  while (index < text.length) {
    const ch = text[index]
    if (ch === ' ' || ch === '\t' || ch === '\n' || ch === '\r') {
      index += 1
      continue
    }
    if (ch === '/' && text[index + 1] === '/') {
      index += 2
      while (index < text.length && text[index] !== '\n') index += 1
      continue
    }
    if (ch === '/' && text[index + 1] === '*') {
      index += 2
      while (index < text.length && !(text[index] === '*' && text[index + 1] === '/')) index += 1
      if (index >= text.length) return text.length
      index += 2
      continue
    }
    break
  }
  return index
}

/** Read one JSON string starting at the opening quote. */
function readString(text, start) {
  let index = start + 1
  let value = ''
  while (index < text.length) {
    const ch = text[index]
    if (ch === '\\') {
      const next = text[index + 1]
      if (next === undefined) return null
      if (next === 'u') {
        const hex = text.slice(index + 2, index + 6)
        if (!/^[0-9a-fA-F]{4}$/.test(hex)) return null
        value += String.fromCharCode(parseInt(hex, 16))
        index += 6
        continue
      }
      value += next
      index += 2
      continue
    }
    if (ch === '"') return { value: value, end: index + 1 }
    value += ch
    index += 1
  }
  return null
}

/**
 * Find the top-level object and its direct keys.
 *
 * The scanner is intentionally small: it only needs to distinguish JSONC
 * comments/strings from structure so a missing key can be inserted without
 * touching the rest of the file. Returns null for anything that is not a single
 * top-level object, or for an object with a property on the opening line (that
 * case falls back to the manual snippet instead of risking a broken comma).
 */
function topLevelObject(text) {
  if (typeof text !== 'string') return null
  let index = skipTrivia(text, 0)
  if (text[index] !== '{') return null
  const open = index
  const keys = new Set()
  let depth = 1
  index += 1
  while (index < text.length) {
    index = skipTrivia(text, index)
    if (index >= text.length) return null
    const ch = text[index]
    if (ch === '"') {
      const string = readString(text, index)
      if (!string) return null
      const after = skipTrivia(text, string.end)
      if (depth === 1 && text[after] === ':') keys.add(string.value)
      index = string.end
      continue
    }
    if (ch === '{' || ch === '[') {
      depth += 1
      index += 1
      continue
    }
    if (ch === '}' || ch === ']') {
      depth -= 1
      index += 1
      if (depth === 0) {
        if (skipTrivia(text, index) !== text.length) return null
        return { open: open, close: index - 1, keys: keys }
      }
      continue
    }
    if (ch === ',' || ch === ':') {
      index += 1
      continue
    }
    index += 1
  }
  return null
}

/**
 * Add missing keys to an existing JSONC object.
 *
 * New properties go before the first existing property so a trailing comment on
 * the last property cannot swallow them. Existing values, comments and line
 * endings are preserved. Returns null when the file cannot be edited safely.
 */
function mergeEditorSettings(text, missing) {
  const object = topLevelObject(text)
  if (!object) return null
  const newline = text.includes('\r\n') ? '\r\n' : '\n'
  const entries = missing.map(function (key) {
    return JSON.stringify(key) + ': ' + JSON.stringify(EDITOR_SETTINGS[key])
  })
  const hasProperties = object.keys.size > 0
  const firstNewline = text.indexOf('\n', object.open)

  if (firstNewline === -1) {
    const suffix = hasProperties ? ', ' : ' '
    return (
      text.slice(0, object.open + 1) + entries.join(', ') + suffix + text.slice(object.open + 1)
    )
  }

  const afterOpen = skipTrivia(text, object.open + 1)
  if (afterOpen < firstNewline && text[afterOpen] === '"') return null

  const insertAt = firstNewline + 1
  const nextLineEnd = text.indexOf('\n', insertAt)
  const nextLine = text.slice(insertAt, nextLineEnd === -1 ? text.length : nextLineEnd)
  const nextTrimmed = nextLine.trimStart()
  let indent = /^[ \t]*/.exec(nextLine)[0]
  if (!nextTrimmed || nextTrimmed.startsWith('}')) {
    const lineStart = text.lastIndexOf('\n', object.open) + 1
    const openIndent = /^[ \t]*/.exec(text.slice(lineStart, object.open))[0]
    indent = openIndent + '  '
  }
  const lines = entries.map(function (entry) {
    return indent + entry + (hasProperties ? ',' : '')
  })
  return text.slice(0, insertAt) + lines.join(newline) + newline + text.slice(insertAt)
}

/**
 * Decide what `setup-dev` should do about editor settings.
 *
 * @param {{ settingsPath?: string, settingsText?: string, exists?: boolean, checkOnly?: boolean, ci?: boolean }} options
 * @returns {{ action: 'skip-ci' | 'check-ok' | 'check-missing' | 'check-partial' | 'create' | 'merge' | 'manual', content?: string, message: string }}
 */
export function planEditorConfig(options) {
  const input = options || {}
  const label = input.settingsPath || '.vscode/settings.json'
  const exists = Boolean(input.exists)
  const checkOnly = Boolean(input.checkOnly)
  const object = exists ? topLevelObject(input.settingsText) : null

  if (input.ci) return { action: 'skip-ci', message: 'CI: skip editor settings' }

  const missing = EDITOR_SETTINGS_KEYS.filter(function (key) {
    return object ? !object.keys.has(key) : !hasKey(input.settingsText, key)
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
        message: 'editor settings: ' + label + ' configures the workspace TypeScript and formatter',
      }
    }
    return {
      action: 'check-partial',
      message:
        'editor settings: ' +
        label +
        ' is missing ' +
        missing.join(', ') +
        '; run npm run setup:dev to add them',
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
      message:
        'editor settings: ' + label + ' already configures the workspace TypeScript and formatter',
    }
  }

  const content = mergeEditorSettings(input.settingsText, missing)
  if (content !== null) {
    return {
      action: 'merge',
      content: content,
      message:
        'editor settings: added ' +
        missing.join(', ') +
        ' to ' +
        label +
        ' (existing values and comments preserved)',
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
