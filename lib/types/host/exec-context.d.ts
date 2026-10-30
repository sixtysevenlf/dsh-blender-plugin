import { AsyncLocalStorage } from 'node:async_hooks';
export declare const EXEC_CTX: AsyncLocalStorage<any>;
/** 当前工具调用对应的宿主 exec.signal（没有则 undefined） */
export declare function execSignal(): AbortSignal | undefined;
