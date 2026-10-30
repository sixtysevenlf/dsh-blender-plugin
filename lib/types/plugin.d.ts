import { losslessSanitize } from './contracts/lossless.js';
import { createBundledSkillProvider, parseSkillFrontmatter, readBundledSkill, registerBundledSkill } from './host/skill.js';
import { frameDedupe, resetFrameCache } from './utils/frame-cache.js';
import { headlessReceipt, promotedReceipt, receiptEnvelope } from './headless/receipts.js';
import { cardPresenters } from './ui/cards.js';
export declare const name = "@dsh-external/dsh-blender-plugin";
export declare const inject: string[];
export interface Config {
    port: number;
    autoStart: boolean;
    /** v2.6.1：dsh-TUI 宿主半边（选中 chip / 页脚字段 / @ 提及 / /blender 命令）；只在有对应接缝的宿主生效 */
    tui: boolean;
}
export declare const Config: any;
export declare function apply(ctx: any, config: Config): void;
export declare const __internals: {
    frameDedupe: typeof frameDedupe;
    resetFrameCache: typeof resetFrameCache;
    losslessSanitize: typeof losslessSanitize;
    receiptEnvelope: typeof receiptEnvelope;
    headlessReceipt: typeof headlessReceipt;
    promotedReceipt: typeof promotedReceipt;
    HEADLESS_WAIT_MS: number;
    PLUGIN_VERSION: string;
    schemaResolve: {
        z?: typeof import("@deepseek-ai/schemastery").default;
        via?: string;
        error?: string;
        tried: string[];
    };
    bundledSkill: {
        dir: string;
        provider: string;
        rank: number;
        read: typeof readBundledSkill;
        createProvider: typeof createBundledSkillProvider;
        register: typeof registerBundledSkill;
        parseFrontmatter: typeof parseSkillFrontmatter;
    };
    cards: {
        specs: Record<string, import("./ui/cards.js").CardSpec>;
        presenters: typeof cardPresenters;
    };
};
