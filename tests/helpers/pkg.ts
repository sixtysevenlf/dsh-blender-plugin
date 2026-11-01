import path from 'node:path'
import { fileURLToPath } from 'node:url'

export const PKG_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..')

export function pkgPath(...parts: string[]) {
  return path.join(PKG_ROOT, ...parts)
}
