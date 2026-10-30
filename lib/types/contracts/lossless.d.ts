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
export declare function losslessSanitize(root: any): {
    value: any;
    fixes: string[];
};
/** 普通对象/数组判定（用于决定是否走 toJSON 分支） */
export declare function isPlainObjectLike(v: any): boolean;
