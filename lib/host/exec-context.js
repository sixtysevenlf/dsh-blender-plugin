import { AsyncLocalStorage } from 'node:async_hooks';
// v0.9.2（DSH 更新适配 R3）：把宿主的 exec（exec.signal = 用户打断 / per-tool 截止）
// 放进 AsyncLocalStorage，传输层 withTimeout() 自动合并它 —— **15 个工具体一行不改**
// 就能响应宿主取消；每个工具另补静态 timeoutMs（见 T），把"上游设默认截止"从
// "Blender 还在跑、工具已被判超时"的静默错位，变成"先由本插件给出可读错误"。
export const EXEC_CTX = new AsyncLocalStorage();
/** 当前工具调用对应的宿主 exec.signal（没有则 undefined） */
export function execSignal() {
    const s = EXEC_CTX.getStore();
    return s && s.signal instanceof AbortSignal ? s.signal : undefined;
}
//# sourceMappingURL=exec-context.js.map