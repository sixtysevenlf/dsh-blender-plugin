import { describe, it, expect } from 'vitest'
import fs from 'node:fs'
import path from 'node:path'
import { PKG_ROOT } from '../../../helpers/pkg'

const PYPROJECT = path.join(PKG_ROOT, 'pyproject.toml')
const PACKAGE = path.join(PKG_ROOT, 'package.json')
const WORKFLOW = path.join(PKG_ROOT, '.github', 'workflows', 'pr.yml')
const AGENTS = path.join(PKG_ROOT, 'AGENTS.md')
const SCRIPTS_README = path.join(PKG_ROOT, 'scripts', 'README.md')

function read(file: string): string {
  return fs.readFileSync(file, 'utf8')
}

function contracts(text: string): string[] {
  return text.split('[[tool.importlinter.contracts]]').slice(1)
}

describe('python import boundary governance', () => {
  it('declares runtime domains, addon assets, and addon panels contracts', () => {
    const text = read(PYPROJECT)
    expect(text).toContain('[tool.importlinter]')
    expect(text).toContain('root_packages = ["runtime.py", "dsh_blender_bridge"]')
    expect(text).toContain('include_external_packages = true')

    const blocks = contracts(text)
    expect(blocks).toHaveLength(3)

    const runtime = blocks.find((block) => block.includes('runtime domains are independent'))
    expect(runtime).toBeTruthy()
    expect(runtime).toContain('type = "layers"')
    expect(runtime).toContain('containers = ["runtime.py"]')
    expect(runtime).toContain(
      'layers = ["core | geometry | assembly | render | export | animation"]',
    )
    expect(runtime).toContain('exhaustive = true')
    expect(runtime).toContain('exhaustive_ignores = ["_facade"]')

    const assets = blocks.find((block) => block.includes('addon assets must not import commands'))
    expect(assets).toBeTruthy()
    expect(assets).toContain('type = "forbidden"')
    expect(assets).toContain('source_modules = ["dsh_blender_bridge.assets.io"]')
    expect(assets).toContain('forbidden_modules = ["dsh_blender_bridge.commands"]')

    const panels = blocks.find((block) =>
      block.includes('addon ui panels must not import integrations'),
    )
    expect(panels).toBeTruthy()
    expect(panels).toContain('type = "forbidden"')
    expect(panels).toContain('source_modules = ["dsh_blender_bridge.ui.panels"]')
    expect(panels).toContain('forbidden_modules = ["dsh_blender_bridge.integrations"]')
    expect(panels).toContain('allow_indirect_imports = true')
  })

  it('wires the import-linter gate into package scripts, CI, and docs', () => {
    const packageJson = JSON.parse(read(PACKAGE)) as { scripts?: Record<string, string> }
    expect(packageJson.scripts?.['check:python-imports']).toBe(
      'node scripts/check-python-imports.mjs',
    )

    const workflow = read(WORKFLOW)
    expect(workflow).toContain('Python import boundaries')
    expect(workflow).toContain('npm run check:python-imports')
    expect(workflow.indexOf('npm run check:python-imports')).toBeGreaterThan(
      workflow.indexOf('npm run lint'),
    )

    expect(read(AGENTS)).toContain('npm run check:python-imports')
    expect(read(SCRIPTS_README)).toContain('check-python-imports.mjs')
  })
})

const ESLINT_CONFIG = path.join(PKG_ROOT, 'eslint.config.mjs')

function srcDirectories(): string[] {
  return fs
    .readdirSync(path.join(PKG_ROOT, 'src'), { withFileTypes: true })
    .filter((entry) => entry.isDirectory())
    .map((entry) => entry.name)
}

describe('typescript boundary governance', () => {
  it('registers every src directory and uses default-deny boundaries', () => {
    const text = read(ESLINT_CONFIG)
    expect(text).toContain("import boundaries from 'eslint-plugin-boundaries'")
    expect(text).toContain("'boundaries/dependencies'")
    expect(text).toContain("'boundaries/no-unknown-files'")
    expect(text).toContain("default: 'disallow'")

    for (const directory of srcDirectories()) {
      expect(text).toContain(`pattern: 'src/${directory}'`)
    }
    for (const category of ['entry', 'constants', 'plugin']) {
      expect(text).toContain(`category: '${category}'`)
    }

    expect(text).toContain("files: ['src/client/**/*.js']")
    expect(text).toContain("files: ['src/host/runtime-bridge.ts']")
    expect(text).toContain("'no-restricted-imports': 'off'")
    expect(text).not.toContain('src/index.ts 是 entry-only')
    expect(text).not.toContain('src/ui 是叶子层')
    expect(text).not.toContain('基础层不得依赖')
  })

  it('registers every runtime directory and the runtime root category', () => {
    const text = read(ESLINT_CONFIG)
    for (const directory of [
      'catalog',
      'core',
      'display',
      'execution',
      'gateway',
      'http',
      'launch',
      'transport',
      'vision',
    ]) {
      expect(text).toContain(`type: 'runtime-${directory}'`)
      expect(text).toContain(`pattern: 'runtime/${directory}'`)
    }
    expect(text).toContain("files: ['runtime/**/*.ts']")
    expect(text).toContain("category: 'runtime-root'")
  })

  it('retires the old boundary checker', () => {
    expect(fs.existsSync(path.join(PKG_ROOT, 'scripts', 'check-import-boundaries.mjs'))).toBe(false)
    const packageJson = JSON.parse(read(PACKAGE)) as { scripts?: Record<string, string> }
    expect(packageJson.scripts?.['check:import-boundaries']).toBeUndefined()
    expect(packageJson.scripts?.['check:finalization']).not.toContain('check:import-boundaries')
  })
})
