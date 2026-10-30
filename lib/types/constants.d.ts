export declare const IS_WIN: boolean;
/**
 * v0.8.10（A0 版本自证）：工具描述统一带版本前缀。
 * description 由**已加载的那份 lib** 生成 → 会话里一眼能看出自己跑的是哪一代，
 * 不会再出现「读的是新源码、跑的是旧 lib」那种排查事故（外部反馈 j20-build 的元教训）。
 */
export declare const PLUGIN_VERSION = "2.6.2";
export declare const HERE: string;
export declare const SERVER_PATH: string;
export declare const HOSTNAME = "127.0.0.1";
/** 后端未就绪时的统一提示（不假设任何本机路径） */
export declare const BACKEND_HINT: string;
/**
 * v0.9.2（R3）：给宿主的**静态**截止时间 —— defineTool 只收常量，不能按参数动态给。
 * 取值原则：一律 = 该工具内部最长超时 + 余量 → 内部超时永远先触发，模型拿到的是本插件
 * 的可读错误；宿主的 TOOL_TIMEOUT 只兜"连内部超时都没兜住"的最后一道。
 * 背景：宿主 0.1.6 起装配 @deepseek-ai/dsh-tool-call-timeout-policy，但**只在工具自己
 * 声明 timeoutMs 时才设截止**；不声明 = 上游一旦给默认值，Blender 还在跑而工具已被判超时。
 */
export declare const T: {
    see: number;
    act: number;
    watch: number;
    cmd: number;
    commands: number;
    loop: number;
    perf: number;
    opt: number;
    headless: number;
    plan: number;
    worker: number;
    txn: number;
    preset: number;
    job: number;
    viewport: number;
};
export declare const HOST_API: string;
/** 宿主取消（exec.signal abort）时的统一措辞：只说"等待被取消"，不谎称任务失败 */
export declare const ABORT_HINT: string;
/** 本会话在写通道上的身份（租约 holder）；跨会话共用同一个后端时用它区分 */
export declare const HOLDER: string;
