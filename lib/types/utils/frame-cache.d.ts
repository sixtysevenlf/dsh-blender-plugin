/**
 * v0.9.4（P0-2）帧去重：**同一画面不重复附图**。
 *
 * 为什么：hash 已经是 PNG 字节的 md5 前 8 位（fetchFrame / fetchView 都算好了），
 * 而重复附图只烧**模型侧视觉 token** —— 实测同一画面连发 5 次 hash 全等
 * （`038c7439`）、单帧 116,870 B。hash 相等 ⇒ 像素逐字节相同 ⇒ 跳过附件**不丢任何信息**。
 * 要强行重发（例如想再看一眼）传 `force:true`。
 *
 * 缓存按"通道"分键：默认视口帧 / 自定义视角（按视角签名）/ rt_do 的 see，互不干扰。
 */
export declare const FRAME_CACHE: Map<string, {
    hash: string;
    firstAt: number;
    repeats: number;
}>;
/** 纯决策：命中 ⇒ dup=true 且计数 +1；未命中 ⇒ 记下新 hash。force=true 一律当未命中处理并重置。 */
export declare function frameDedupe(key: string, hash: string, force?: boolean): {
    dup: boolean;
    repeats: number;
    firstAt: number;
};
/** 测试用：清空去重缓存 */
export declare function resetFrameCache(): void;
/** 去重命中时统一的措辞（写清"相同、未附图、怎么要回来"，避免 agent 误以为看过图） */
export declare function dedupeNote(repeats: number): string;
/** PNG → durable attachment（v0.9.2 起失败**不再静默**：返回 {ref} 或 {why}，由调用方写进工具文本） */
