export declare const ANY_SCHEMA: any;
export declare const HINT_KERNEL = "\u6301\u4E45\u5185\u6838\uFF1AK.x = 1 \u8FD9\u6B21\u5199\uFF0C\u4E0B\u6B21\u8C03\u7528\u8FD8\u80FD\u8BFB\u5230\uFF1B\u9884\u7F6E bpy / math / mathutils / Vector\u3002";
/**
 * v1.0.1+S11（issue #10）：schemastery 解析失败时的**降级出口**。
 * 目的不是"还能干活"，而是**让失败可读、可定位**：占位工具照样出现在工具列表里，
 * 一调它就直接给出试过的名字、每条通道的真实错误与修法 —— 而不是旧版那样
 * "模块载入期 throw → 15 个工具整体消失 + 宿主只回一句 failed to import"。
 */
export declare function registerSchemaDiagnostic(ctx: any): void;
