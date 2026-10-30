import { describe, expect, it } from 'vitest'
import fs from 'node:fs'
import path from 'node:path'
import * as plugin from '../../../../lib/index.js'
import { pkgPath } from '../../../helpers/pkg'

// v2.2.0 bundled skill: the skill ships inside the plugin. Silent failure here
// would make users believe they have the skill while the directory is missing,
// so the asset tree, frontmatter, provider shape and registration path are all
// asserted without starting DSH.

const SKILL_DIR = pkgPath('skills', 'blender-modeling')
const SKILL_FILE = path.join(SKILL_DIR, 'SKILL.md')
const NAME_RE = /^[a-z0-9]+(?:-[a-z0-9]+)*$/
const stripFrontmatter = (raw: string) => {
  const match = /^---\r?\n([\s\S]*?)\r?\n---(?:\r?\n|$)/.exec(raw)
  return match ? raw.slice(match[0].length) : raw
}

describe('bundled skill asset tree', () => {
  it('ships SKILL.md with a valid frontmatter', () => {
    const raw = fs.readFileSync(SKILL_FILE, 'utf8')
    expect(raw.startsWith('---\n') || raw.startsWith('---\r\n')).toBe(true)
    expect(raw.length).toBeGreaterThan(50 * 1024)
    const match = /^---\r?\n([\s\S]*?)\r?\n---(?:\r?\n|$)/.exec(raw)
    expect(match).not.toBeNull()
    expect(raw).toContain('name: blender-modeling')
    expect(raw).toContain('description:')
  })

  it('ships references, LICENSE, NOTICE and README without temp files', () => {
    for (const file of ['modeling-overview.md', 'multiview-calibration.md', '参考图通用还原SOP.md', '验收清单.md']) {
      expect(fs.existsSync(path.join(SKILL_DIR, 'references', file)), file).toBe(true)
    }
    for (const file of ['LICENSE', 'NOTICE.md', 'README.md']) expect(fs.existsSync(path.join(SKILL_DIR, file)), file).toBe(true)
    const bad: string[] = []
    const walk = (dir: string) => {
      for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
        const full = path.join(dir, entry.name)
        if (entry.isDirectory()) walk(full)
        else if (/\.(bak|tmp|orig)$|\.bak-|~$/.test(entry.name)) bad.push(path.relative(pkgPath(), full))
      }
    }
    walk(SKILL_DIR)
    expect(bad).toEqual([])
  })

  it('packs skills and keeps the package metadata', () => {
    const pkg = JSON.parse(fs.readFileSync(pkgPath('package.json'), 'utf8'))
    expect(pkg.files).toContain('skills')
    expect(pkg.name).toBe('@dsh-external/dsh-blender-plugin')
    expect(pkg.license).toBe('BSD-3-Clause')
  })
})

describe('bundled skill provider', () => {
  const bs = (plugin.__internals as any).bundledSkill

  it('resolves the bundled provider contract', () => {
    expect(bs).toBeTruthy()
    expect(path.resolve(bs.dir)).toBe(path.resolve(SKILL_DIR))
    expect(NAME_RE.test(bs.provider)).toBe(true)
    expect(bs.rank).toBe(600)
    expect(bs.createProvider()).not.toBeNull()
  })

  it('lists and gets the single candidate, with signal support', async () => {
    const provider = bs.createProvider()
    expect(provider.name).toBe(bs.provider)
    const candidates = await provider.list({ cwd: pkgPath() })
    expect(candidates).toHaveLength(1)
    const withSignal = provider.list({ cwd: pkgPath(), signal: new AbortController().signal })
    expect(typeof withSignal.then).toBe('function')
    expect(await withSignal).toHaveLength(1)
    const candidate = candidates[0]
    expect(candidate.name).toBe('blender-modeling')
    expect(candidate.invocation).toMatchObject({ modelInvocable: true, userInvocable: true })
    expect(candidate.source).toBe('bundled')
    expect(candidate.provider).toBe(provider.name)
    expect(candidate.rank).toBe(600)
    expect(candidate.resourceBase.kind).toBe('directory')
    expect(fs.existsSync(candidate.resourceBase.path)).toBe(true)
    expect(path.resolve(candidate.path)).toBe(path.resolve(SKILL_FILE))
    const def = await provider.get(candidate, {})
    expect(def.name).toBe(candidate.name)
    expect(def.content.startsWith('---\n')).toBe(false)
    expect(def.content.length).toBeGreaterThan(50 * 1024)
    expect(def.content).toBe(stripFrontmatter(fs.readFileSync(SKILL_FILE, 'utf8')))
    const withSignalGet = provider.get(candidate, { signal: new AbortController().signal })
    expect(typeof withSignalGet.then).toBe('function')
    expect((await withSignalGet).content).toContain('# Blender 建模流程')
  })

  it('parses frontmatter edge cases', () => {
    const pf = bs.parseFrontmatter
    expect(pf('---\nname: demo-skill\ndescription: "a quoted line"\n---\nbody\n').meta.name).toBe('demo-skill')
    expect(pf('---\nname: demo-skill\ndescription: "a quoted line"\n---\nbody\n').meta.description).toBe('a quoted line')
    expect(pf('---\nname: demo-skill\ndescription: "a quoted line"\n---\nbody\n').body).toBe('body\n')
    expect(pf("---\nname: x\ndescription: 'single'\n---\n").meta.description).toBe('single')
    expect(pf('---\nname: x\ndescription: line one\n  line two\n---\nx').meta.description).toBe('line one line two')
    expect(pf('no frontmatter here')).toMatchObject({ body: 'no frontmatter here' })
  })

  it('registers through ctx.inject, direct ctx.skills, or not at all', async () => {
    let injected: string[] | null = null
    let registered: any = null
    const fakeCtx = {
      logger: { warn() {} },
      inject(deps: string[], cb: (scope: any) => void) {
        injected = deps
        cb({ skills: { registerProvider(factory: (ctx: any) => any) { registered = factory({}) } } })
      },
    }
    expect(() => bs.register(fakeCtx)).not.toThrow()
    expect(injected).toEqual(['skills'])
    expect(registered.name).toBe(bs.provider)
    expect(await registered.list({ cwd: pkgPath() })).toHaveLength(1)

    let direct: any = null
    expect(() => bs.register({ skills: { registerProvider(factory: (ctx: any) => any) { direct = factory({}) } }, logger: { warn() {} } })).not.toThrow()
    expect(direct.name).toBe(bs.provider)
    expect(() => bs.register({ logger: { warn() {} } })).not.toThrow()
  })
})
