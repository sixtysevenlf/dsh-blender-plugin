// Locate the bundled Windows hidden-desktop helper.
//
// Resolution order is explicit: an environment override for development, the
// staged vendor directory that release packaging ships, then the local build
// output under tools/. A missing override never falls back silently: it is the
// answer, so a developer who mistypes the path sees a config failure instead of
// a different build being used.
import fs from 'node:fs'
import path from 'node:path'
import { PKG_ROOT } from '../config.js'

export const HELPER_EXE = 'dsh-blender-hidden-desktop.exe'

export interface HelperResolution {
  path: string | null
  source: string
  checked: string[]
  detail: string
}

export interface HelperResolveOptions {
  env?: NodeJS.ProcessEnv
  pkgRoot?: string
}

export function isHelperFile(file: string): boolean {
  try {
    return fs.statSync(file).isFile()
  } catch (e) {
    return false
  }
}

export function resolveHiddenDesktopHelper(options: HelperResolveOptions = {}): HelperResolution {
  const env = options.env || process.env
  const root = options.pkgRoot || PKG_ROOT
  const checked: string[] = []
  const override = String(env.DSH_HIDDEN_DESKTOP_HELPER || '').trim()
  if (override) {
    const source = 'env:DSH_HIDDEN_DESKTOP_HELPER'
    checked.push(source + '=' + override)
    if (isHelperFile(override)) {
      return { path: override, source, checked, detail: override + ' (' + source + ')' }
    }
    return {
      path: null,
      source,
      checked,
      detail: 'DSH_HIDDEN_DESKTOP_HELPER points to a missing file: ' + override,
    }
  }
  const candidates = [
    {
      file: path.join(root, 'vendor', 'win-hidden-desktop', HELPER_EXE),
      source: 'vendor/win-hidden-desktop',
    },
    {
      file: path.join(root, 'tools', 'win-hidden-desktop', 'out', HELPER_EXE),
      source: 'tools/win-hidden-desktop/out',
    },
  ]
  for (const candidate of candidates) {
    checked.push(candidate.file)
    if (isHelperFile(candidate.file)) {
      return {
        path: candidate.file,
        source: candidate.source,
        checked,
        detail: candidate.file + ' (' + candidate.source + ')',
      }
    }
  }
  return {
    path: null,
    source: 'none',
    checked,
    detail: 'hidden-desktop helper not found (checked ' + checked.join(', ') + ')',
  }
}
