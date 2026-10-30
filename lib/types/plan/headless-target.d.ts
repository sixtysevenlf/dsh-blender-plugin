/** P1：plan op → 无头 worker 的模块 API（不给 GUI 也能跑；op 前缀路由 + 别名） */
export declare const PLAN_HEADLESS_MAP: any[];
export declare const PLAN_HEADLESS_ALIAS: any;
export declare function planHeadlessTarget(op: string): any;
/** 用持久 worker 执行一个 plan op（不依赖 GUI addon；模块由 preload 注入 K） */
export declare function planViaWorker(ctx: any, port: number, op: string, payload: any, session?: string): Promise<any>;
/** plan 回执统一收尾：信封 + 附图（GUI/无头共用） */
export declare function finishPlanResponse(ctx: any, op: string, res: any, argsIn: any): Promise<any>;
/** 弱模型契约 v2：verdict-first 信封（单行、≤2k）——plan 与 headless 共用口径 */
export declare function planEnvelope(op: string, res: any): any;
