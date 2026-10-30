import fs from 'node:fs'
import path from 'node:path'
import { PKG_ROOT } from './pkg'

/** Path inside the package checkout. */
export function repoPath(...parts: string[]) {
  return path.join(PKG_ROOT, ...parts)
}

/** Read a UTF-8 document, normalizing CRLF so line counting is platform independent. */
export function readText(relativePath: string) {
  return fs.readFileSync(repoPath(relativePath), 'utf8').replace(/\r\n/g, '\n')
}

/** Number of lines, ignoring a single trailing newline. */
export function lineCount(text: string) {
  const lines = text.split('\n')
  if (lines.length > 0 && lines[lines.length - 1] === '') lines.pop()
  return lines.length
}

/** Level-two headings, in document order. */
export function sectionHeadings(text: string) {
  return text.split('\n').filter((line) => /^##\s/.test(line))
}

/** Relative link targets from markdown; external URLs and anchors are skipped. */
export function relativeLinks(markdown: string) {
  const targets: string[] = []
  const link = /\[[^\]]*\]\(([^)]+)\)/g
  let match: RegExpExecArray | null
  while ((match = link.exec(markdown)) !== null) {
    const raw = match[1].trim().replace(/^<|>$/g, '')
    if (/^(https?:|mailto:|tel:|#)/i.test(raw)) continue
    const target = raw.split('#')[0]
    if (target) targets.push(target)
  }
  // Autolinks are not relative links and are intentionally ignored.
  return targets
}

/** True when every relative link in the document resolves from its directory. */
export function brokenRelativeLinks(relativePath: string) {
  const text = readText(relativePath)
  const directory = path.dirname(relativePath)
  const broken: string[] = []
  for (const target of relativeLinks(text)) {
    const resolved = path.resolve(repoPath(directory), target)
    if (!fs.existsSync(resolved)) broken.push(target)
  }
  return broken
}

/** Remove fenced code, inline code, and whole markdown links; used for prose language checks. */
export function proseOnly(markdown: string) {
  return markdown
    .replace(/```[\s\S]*?```/g, '')
    .replace(/`[^`]*`/g, '')
    .replace(/\[[^\]]*\]\([^)]*\)/g, '')
}

const CJK = /[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]/
export function hasCjk(text: string) {
  return CJK.test(text)
}

const SKIP_DIRS = new Set([
  '.git',
  'node_modules',
  'node_modules_symlink_backup',
  'lib',
  'coverage',
  'dist',
  'build',
  '__pycache__',
  '.venv',
  'venv',
  'docs/refactor/evidence',
])
const SKIP_ANY = new Set(['node_modules', 'node_modules_symlink_backup', '__pycache__', '.git', '.vite', '.vite-temp', '.cache'])
const TEXT_EXT = new Set(['.md', '.mdx', '.txt', '.json', '.yml', '.yaml', '.mjs', '.cjs', '.js', '.ts', '.py', '.sh', '.html', '.toml', '.cfg', '.ini'])

/** Repository text files, relative to the package root, skipping vendored and generated trees. */
export function repositoryTextFiles(root: string = PKG_ROOT, prefix = ''): string[] {
  const found: string[] = []
  const entries = fs.readdirSync(root, { withFileTypes: true })
  for (const entry of entries) {
    const relative = prefix ? prefix + '/' + entry.name : entry.name
    if (SKIP_DIRS.has(relative) || SKIP_ANY.has(entry.name)) continue
    const absolute = path.join(root, entry.name)
    if (entry.isSymbolicLink()) continue
    if (entry.isDirectory()) {
      // runtime/ext holds a 1.2 GB vendored interpreter; not documentation.
      if (relative === 'runtime/ext') continue
      found.push(...repositoryTextFiles(absolute, relative))
      continue
    }
    if (!entry.isFile()) continue
    if (!TEXT_EXT.has(path.extname(entry.name).toLowerCase())) continue
    if (fs.statSync(absolute).size > 1_048_576) continue
    found.push(relative)
  }
  return found
}
