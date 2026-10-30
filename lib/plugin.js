/**
 * @dsh-external/dsh-blender-plugin —— host 侧生命周期与工具面装配。
 *
 * 这里只做装配：配置 schema / 看护 / 15 个工具注册 / 测试接缝。
 * 具体实现按层拆在 contracts/ host/ tools/ plan/ headless/ utils/ ui/。
 */
import { HOLDER, PLUGIN_VERSION } from './constants.js';
import { z, schemaResolve } from './contracts/tool.js';
import { losslessSanitize } from './contracts/lossless.js';
import { backendPost } from './host/http.js';
import { ensureBackend, isPaused, killBackendByCmd, killChildHandle, probe, resolveHttpPort, sleep, startBackend } from './host/backend.js';
import { registerSchemaDiagnostic } from './tools/schema-diagnostic.js';
import { registerTools } from './tools/index.js';
import { BUNDLED_SKILL_DIR, BUNDLED_SKILL_PROVIDER, BUNDLED_SKILL_RANK, createBundledSkillProvider, parseSkillFrontmatter, readBundledSkill, registerBundledSkill } from './host/skill.js';
import { registerTuiHost } from './tui-host.js';
import { frameDedupe, resetFrameCache } from './utils/frame-cache.js';
import { HEADLESS_WAIT_MS, headlessReceipt, promotedReceipt, receiptEnvelope } from './headless/receipts.js';
import { CARD_SPECS, cardPresenters } from './ui/cards.js';
export const name = '@dsh-external/dsh-blender-plugin';
export const inject = ['tools'];
// v1.0.1+S11：z 不可用时（降级模式）不给 schema —— 宿主不会因为这里再抛一次而整体失败。
export const Config = z
    ? z.object({
        port: z.natural().default(resolveHttpPort()),
        autoStart: z.boolean().default(true),
        tui: z.boolean().default(true)
    })
    : undefined;
export function apply(ctx, config) {
    // v2.2.0：随包 skill 自动注册（可选能力；失败不影响工具面）。
    // 放在 degraded 分支之前 —— 即使 schemastery 解析失败、工具降级，技能内容也仍然可用。
    registerBundledSkill(ctx);
    // v1.0.1+S11（issue #10）：解析失败 ⇒ 降级但不消失（见 registerSchemaDiagnostic）。
    if (!z) {
        registerSchemaDiagnostic(ctx);
        return;
    }
    // 防御性读配置：schema 正常时宿主会补默认值，但降级/宿主差异都不该让这里再抛。
    const cfgIn = config && typeof config === 'object' ? config : {};
    const port = Number.isFinite(Number(cfgIn.port)) && Number(cfgIn.port) > 0 ? Number(cfgIn.port) : resolveHttpPort();
    const autoStart = cfgIn.autoStart === undefined ? true : !!cfgIn.autoStart;
    // v2.6.1：dsh-TUI 宿主半边（契约 v2.3 增补 §S6）。各块按接缝是否存在启用；Web / headless 上什么都不做。
    if (cfgIn.tui !== false)
        registerTuiHost(ctx, { port: port, holder: HOLDER });
    void (async () => {
        if (autoStart && !isPaused())
            await ensureBackend(port);
    })();
    // 常驻看护：每 15s 探活一次，掉线自动拉起（宿主重启 / 进程被杀 / 端口被回收都能自愈）。
    // 显式 op=stop 会置 paused，看护尊重它，不会跟用户对着干。
    // ★ v1.0.1+S10（探活失败时**先清尸再重生**）：否则"僵死后端占着端口"时，重生进程必然撞
    //   EADDRINUSE 退出 → 每 15 s 空转一次、永不自愈（现场：后端事件循环被长时间同步操作占死，
    //   /health 不应答、连 SIGTERM 都排不上队）。probe 超时只有 1500ms，所以连续 2 次（≈30s）
    //   失败才认定卡死，避免把"只是忙"的后端杀掉。
    let probeMisses = 0;
    ctx.effect(() => {
        const wd = setInterval(() => {
            void (async () => {
                try {
                    if (isPaused())
                        return;
                    if (await probe(port)) {
                        probeMisses = 0;
                        // 租约心跳：只在"本来就持有"时续期，空闲时不抢占别的会话
                        try {
                            await backendPost(port, '/lease', { renewOnly: true }, 5000);
                        }
                        catch (e) {
                            /* 后端可能刚挂，下一轮再试 */
                        }
                        return;
                    }
                    probeMisses += 1;
                    if (probeMisses >= 2) {
                        probeMisses = 0;
                        // SIGTERM → 1.2s → SIGKILL：事件循环被同步操作占死的进程只认 SIGKILL
                        try {
                            killBackendByCmd(port, 'SIGTERM');
                        }
                        catch (e) {
                            /* 忽略 */
                        }
                        await sleep(1200);
                        try {
                            killBackendByCmd(port, 'SIGKILL');
                        }
                        catch (e) {
                            /* 忽略 */
                        }
                    }
                    await startBackend(port);
                }
                catch (e) {
                    /* 看护失败静默，下一轮再试 */
                }
            })();
        }, 15000);
        return () => clearInterval(wd);
    }, '@dsh-external/dsh-blender-plugin: watchdog');
    ctx.effect(() => () => {
        killChildHandle();
    }, '@dsh-external/dsh-blender-plugin: backend');
    // ---------- 工具面：15 个工具按 v2.6.2 原顺序注册（各工具的 ctx.effect 键不变） ----------
    registerTools(ctx, port);
}
/* ────────────────────────────────────────────────────────────────────────────
 * v0.9.4：**测试接缝**。tests/lossless_guard.mjs 直接 import 本模块，就能拿到
 *   ① 真实回执构造器（receiptEnvelope / headlessReceipt / promotedReceipt）
 *   ② 出口消毒器（losslessSanitize）
 * 然后用**宿主的真校验器**（dsh-util-values 的 snapshotJsonValue）断言：
 * "本插件任何一个回执都过得了 lossless-JSON 门"。
 * 这是把「返回通道」从"靠人记得别写 undefined"变成"机器每次回归都验"的唯一办法。
 * ──────────────────────────────────────────────────────────────────────────── */
export const __internals = {
    frameDedupe: frameDedupe,
    resetFrameCache: resetFrameCache,
    losslessSanitize: losslessSanitize,
    receiptEnvelope: receiptEnvelope,
    headlessReceipt: headlessReceipt,
    promotedReceipt: promotedReceipt,
    HEADLESS_WAIT_MS: HEADLESS_WAIT_MS,
    PLUGIN_VERSION: PLUGIN_VERSION,
    schemaResolve: schemaResolve,
    bundledSkill: {
        dir: BUNDLED_SKILL_DIR,
        provider: BUNDLED_SKILL_PROVIDER,
        rank: BUNDLED_SKILL_RANK,
        read: readBundledSkill,
        createProvider: createBundledSkillProvider,
        register: registerBundledSkill,
        parseFrontmatter: parseSkillFrontmatter
    },
    cards: { specs: CARD_SPECS, presenters: cardPresenters }
};
//# sourceMappingURL=plugin.js.map