export declare function renderOne(_args: unknown, value: any): any[];
/** 安全 JSON.stringify（循环引用/大对象不炸；超长返回 null 由调用方给路径） */
export declare function safeStringify(v: any): string | null;
/** 截断（保留头尾，中间省略标记）—— 与后端 clipMiddle 同一口径 */
export declare function clipText(s: any, head: number, tail: number): string;
/** 带结构化信封的工具回执：value = {text, envelope?, image?, images?} */
export declare function renderStructured(_args: unknown, value: any): any[];
export interface CardSpec {
    kind: string;
    title: (a: any) => string;
    /** 这次调用执行的代码（有就进卡片正文，结果卡片里附在摘要后面） */
    code?: (a: any) => {
        label: string;
        code: string;
    } | null;
}
export declare const CARD_SPECS: Record<string, CardSpec>;
/** vTool 用：按工具名给出 presentCall / presentResult（未登记的工具 / spec 自带的不覆盖） */
export declare function cardPresenters(spec: any): any;
/**
 * v0.9.3（D1/F3）：客户端单次等待上限。超过它就**不干等**，转作业层并回 jobId（对齐 bash 的 promoted 语义）。
 * 默认 100 s：外层 run_code 的默认 deadline 是 120 s，留 20 s 余量给回执与状态查询（promoted 路径只是一次很快的 /job 查询）。
 * 调大它 = 允许单次同步等更久（代价：外层 deadline 到点时更容易"结果在服务端、客户端拿不到"）。
 */
