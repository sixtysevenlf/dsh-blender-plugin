import { readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
export const BUNDLED_SKILL_PROVIDER = 'dsh-blender-plugin';
export const BUNDLED_SKILL_RANK = 600;
export const BUNDLED_SKILL_DIR = fileURLToPath(new URL('../../skills/blender-modeling/', import.meta.url));
let bundledSkillCache;
let bundledSkillWarned = false;
/** 解析 SKILL.md 的 YAML frontmatter（只取本插件需要的 name/description；不引 yaml 依赖）。 */
export function parseSkillFrontmatter(raw) {
    const m = /^---\r?\n([\s\S]*?)\r?\n---(?:\r?\n|$)/.exec(raw);
    if (!m)
        return { meta: {}, body: raw };
    const meta = {};
    let key = '';
    for (const line of m[1].split(/\r?\n/)) {
        const kv = /^([A-Za-z0-9_-]+):\s*(.*)$/.exec(line);
        if (kv) {
            key = kv[1];
            let v = kv[2].trim();
            if ((v.startsWith('"') && v.endsWith('"')) || (v.startsWith("'") && v.endsWith("'")))
                v = v.slice(1, -1);
            meta[key] = v;
            continue;
        }
        // YAML 折叠/多行 continuation：缩进行并进上一个 key
        if (key && /^\s+\S/.test(line))
            meta[key] = (meta[key] ? meta[key] + ' ' : '') + line.trim();
    }
    return { meta, body: raw.slice(m[0].length) };
}
export function readBundledSkill() {
    if (bundledSkillCache !== undefined)
        return bundledSkillCache;
    try {
        const raw = readFileSync(path.join(BUNDLED_SKILL_DIR, 'SKILL.md'), 'utf8');
        const { meta, body } = parseSkillFrontmatter(raw);
        const name = String(meta.name || '').trim();
        const description = String(meta.description || '').trim();
        if (!/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(name) || !description) {
            bundledSkillCache = null;
            return null;
        }
        bundledSkillCache = { name, description, content: body };
        return bundledSkillCache;
    }
    catch (e) {
        bundledSkillCache = null;
        return null;
    }
}
export function createBundledSkillProvider() {
    const skill = readBundledSkill();
    if (!skill)
        return null;
    const skillFile = path.join(BUNDLED_SKILL_DIR, 'SKILL.md');
    const resourceBase = { kind: 'directory', path: BUNDLED_SKILL_DIR.split(path.sep).join('/') };
    const candidate = {
        name: skill.name,
        description: skill.description,
        invocation: { modelInvocable: true, userInvocable: true },
        source: 'bundled',
        provider: BUNDLED_SKILL_PROVIDER,
        rank: BUNDLED_SKILL_RANK,
        resourceBase,
        path: skillFile,
        locator: skillFile
    };
    return {
        name: BUNDLED_SKILL_PROVIDER,
        // ⚠ 必须是 Promise（async）：宿主 registry 的 waitWithAbort 在调用方带 signal 时会对返回值
        // 直接 `promise.then(...)`（dsh-skill/lib 每一条模型侧 lookup 都带 signal）——
        // 同步返回会抛 `promise.then is not a function`，provider 被静默 skip，skill 实际加载不了。
        list: async () => [candidate],
        get: async () => ({
            name: skill.name,
            description: skill.description,
            invocation: { modelInvocable: true, userInvocable: true },
            source: 'bundled',
            provider: BUNDLED_SKILL_PROVIDER,
            resourceBase,
            path: skillFile,
            content: skill.content
        })
    };
}
/**
 * 把随包 skill 注册进宿主的 skills 注册表（可选能力，失败只告警）。
 * 用 ctx.inject(['skills'], cb) 延迟到服务可用时再注册：宿主若没有 skill 服务，
 * 这个子 fiber 一直待命即可，**不会**让插件本身起不来。
 */
export function registerBundledSkill(ctx) {
    const provider = createBundledSkillProvider();
    if (provider === null) {
        if (!bundledSkillWarned) {
            bundledSkillWarned = true;
            try {
                ctx?.logger?.warn?.('bundled skill 未注册：skills/blender-modeling/SKILL.md 缺失或 frontmatter 不合法（不影响工具面）');
            }
            catch (e) { }
        }
        return;
    }
    const mount = (c) => {
        try {
            c.skills.registerProvider(() => provider);
        }
        catch (e) {
            try {
                ctx?.logger?.warn?.('bundled skill 注册失败：' + String((e && e.message) || e) + '（不影响工具面）');
            }
            catch (e2) { }
        }
    };
    try {
        if (typeof ctx?.inject === 'function')
            ctx.inject(['skills'], (c) => mount(c));
        else if (ctx?.skills && typeof ctx.skills.registerProvider === 'function')
            mount(ctx);
    }
    catch (e) {
        try {
            ctx?.logger?.warn?.('bundled skill 挂载失败：' + String((e && e.message) || e) + '（不影响工具面）');
        }
        catch (e2) { }
    }
}
//# sourceMappingURL=skill.js.map