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
export const FRAME_CACHE = new Map();
/** 纯决策：命中 ⇒ dup=true 且计数 +1；未命中 ⇒ 记下新 hash。force=true 一律当未命中处理并重置。 */
export function frameDedupe(key, hash, force = false) {
    const k = String(key || 'default');
    const h = String(hash || '');
    const cur = FRAME_CACHE.get(k);
    if (!force && cur && h && cur.hash === h) {
        cur.repeats += 1;
        return { dup: true, repeats: cur.repeats, firstAt: cur.firstAt };
    }
    const firstAt = Date.now();
    FRAME_CACHE.set(k, { hash: h, firstAt, repeats: 0 });
    if (FRAME_CACHE.size > 32) {
        // 键很少（几种通道），加个硬上限防意外增长
        const oldest = FRAME_CACHE.keys().next().value;
        if (oldest !== undefined && oldest !== k)
            FRAME_CACHE.delete(oldest);
    }
    return { dup: false, repeats: 0, firstAt };
}
/** 测试用：清空去重缓存 */
export function resetFrameCache() {
    FRAME_CACHE.clear();
}
/** 去重命中时统一的措辞（写清"相同、未附图、怎么要回来"，避免 agent 误以为看过图） */
export function dedupeNote(repeats) {
    return ' · **与上一张完全相同**（第 ' + String(repeats + 1) + ' 次）⇒ 未重复附图；要重发传 force:true';
}
/** PNG → durable attachment（v0.9.2 起失败**不再静默**：返回 {ref} 或 {why}，由调用方写进工具文本） */
//# sourceMappingURL=frame-cache.js.map