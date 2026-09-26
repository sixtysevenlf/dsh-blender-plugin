/**
 * v0.9.4（P0 返回通道）：**工具返回值统一消毒**，保证过得了宿主的 lossless-JSON 门。
 *
 * 背景（外部反馈 2026-09-25《M1A1 分件建模》）：宿主用 dsh-util-values 的 walkJsonValue 校验
 * 每个工具的返回值，规则**比 JSON.stringify 严格得多**，以下一律判
 * `tool "…" returned invalid output: value is not lossless JSON` 并把整条工具通道打死：
 *   - 自有可枚举键的值为 undefined（stringify 会丢键 → 肉眼看不出来）
 *   - NaN / ±Infinity（stringify 变 null）· -0（stringify 变 0，符号丢失）
 *   - Date / Map / Set / 类实例 / 函数 / Symbol / 循环引用 / 带符号或非枚举键的对象
 * 实测后果：v0.9.3 的 envelope 里一个 `promoted: undefined` 让
 * blender_rt_headless **每次都失败**（任务其实跑完了、结果也在磁盘上，只是回执被拒），
 * rt_job 的 status/collect/wait 同病 —— 即"任务能交出去但收不回来"。
 *
 * 消毒口径（**不静默**：每一处改动都记进 fixes，回执文本里点名）：
 *   undefined → 丢键（数组元素 → null，长度不塌）· 非有限数 → null · -0 → 0
 *   bigint → number（超安全整数则 string）· Date → ISO 字符串 · Map/Set → 数组
 *   类实例 → 取自有可枚举属性 · 二进制视图 → {bytes:N} · 循环 → '[Circular]'
 */
declare function losslessSanitize(root: any): {
    value: any;
    fixes: string[];
};
export declare const name = "@dsh-external/dsh-blender-plugin";
export declare const inject: string[];
export interface Config {
    port: number;
    autoStart: boolean;
}
export declare const Config: any;
/** 纯决策：命中 ⇒ dup=true 且计数 +1；未命中 ⇒ 记下新 hash。force=true 一律当未命中处理并重置。 */
export declare function frameDedupe(key: string, hash: string, force?: boolean): {
    dup: boolean;
    repeats: number;
    firstAt: number;
};
/** 测试用：清空去重缓存 */
export declare function resetFrameCache(): void;
/** 结构化信封（block 0）：稳定字段 + 有界大小（大结果只给 resultPath） */
declare function receiptEnvelope(res: any, ctx: any): any;
/** 无头回执（sync / 作业层完成）：{text, envelope} —— 两个 text block 的来源 */
declare function headlessReceipt(res: any, ctx: any): any;
/**
 * v0.9.3（D1）：**promoted 回执** —— 任务还在跑（或已结束但我们没等到），句柄已经有效。
 * 与 bash 通道的 promoted 语义对齐：kind="promoted" + jobId，note 里写清「怎么收结果」。
 */
declare function promotedReceipt(job: any, jobId: string, why: string): any;
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
};
export {};
