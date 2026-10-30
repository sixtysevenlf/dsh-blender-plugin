export declare function backendJson(port: number, path: string, timeoutMs?: number, init?: any): Promise<any>;
/**
 * 解析后端响应（v0.8.9 修）：后端有两种形态 ——
 *   ① 普通路由：整段就是一个 JSON；
 *   ② 流式路由（/headless /worker /txn /preset）：NDJSON，**每 15 s 先写一行** {"heartbeat":true,...}，最后一行才是结果。
 * 旧实现整段 JSON.parse，于是任何 **超过 15 s** 的调用都会抛 Extra data → 调用方只看到 exit=undefined（子进程其实跑完了）。
 * 现在：解析失败就按行往前找最后一条非心跳 JSON；同时把心跳条数记进 __heartbeats 供工具层提示。
 */
export declare function parseBackendBody(text: string): any;
/**
 * 写操作统一走它：自动带 holder（租约门禁用），并保留 HTTP 状态码便于识别 409。
 *
 * v0.9.3（D1，关键）：**计时器必须活到 body 读完**。
 * 旧版是 withTimeout(fetch) → fetch 在**收到响应头**时就 resolve（流式路由 /headless 立刻发头 + 每 15 s 心跳），
 * 于是超时预算只覆盖「等响应头」，随后的 r.text() 无限期等下去 —— 客户端等待窗口形同虚设，
 * 外层 run_code 的 deadline 一到就整段丢结果（正是 D1 报的现象）。
 * 现在把 text() 也放进同一个 AbortController：到点 abort → fetch / body 读取一起抛 AbortError。
 */
export declare function backendPost(port: number, path: string, body: any, timeoutMs?: number): Promise<any>;
/** v0.9.1：工具入参容错 —— harness 的 `type:"json"` 有时把对象给成**字符串**（实测同一会话里两种形态都出现过），
 *  旧写法 `typeof x === 'object'` 会静默丢参（表现为"参数没生效却 ok:true"，非常难查）。 */
export declare function jsonPayload(v: any): any;
/** 把 "‑‑flag \"a b\" c" 这样的字符串切成 argv（引号成对时不当分隔符；v0.8.9 修 headless 的 args 引号问题）
 *  v0.9.1：数组直接透传（旧版 `String([...])` 会变成 "[delta]" 这种带方括号的单个 token） */
export declare function splitArgs(s: any): string[];
/** 409 leased → 给模型一句能照着做的话（而不是丢一段 JSON） */
export declare function leasedText(r: any): string | undefined;
/** 拉一帧 PNG 字节（含耗时）；full=true 走 screenshot_area（整窗口/区域） */
export declare function fetchFrame(port: number, size: number, full?: boolean, area?: number): Promise<any>;
/** 自定义视角出图：POST /view（view.py）——不动物体、不动用户视口 */
export declare function fetchView(port: number, spec: any, timeoutMs?: number): Promise<any>;
export declare function numTriple(v: any): number[] | undefined;
