// @ts-nocheck
/**
 * @dsh-external/dsh-blender-plugin —— host 侧（10 个工具）。
 *
 * 唯一职责：**给模型低延迟的「看 / 做 / 连续看 / 重活」原语**
 * （后端 runtime/server.mjs 127.0.0.1:9877 → 直连 addon socket 127.0.0.1:9876）：
 *       blender_rt_see      取一帧视口 / 自定义视角（不动物体、不动用户视口）
 *       blender_rt_do       跑一段 Python + 立刻回一帧（整步 ~105ms）
 *       blender_rt_watch    指定时间窗内连续采样若干帧（看动画/交互行为）
 *       blender_rt_loop     内环：一次调用在 Blender 主线程跑几千次迭代
 *       blender_rt_cmd/commands  透传 addon 任意命令（30 条）
 *       blender_rt_perf/opt 渲染性能预设 / 对象精简
 *       blender_rt_headless 无头进程（blender -b）：重活不占 GUI 通道
 *       blender_viewport   后端运维：status / doctor / who / lease / release / start / stop / restart
 * 配合持久内核变量 K（addon 每次调用是新命名空间，K 存在 sys.modules 里 → REPL 语义）。
 *
 * 并发：写操作自动带租约 holder；别的会话持有时写路由 409（只读 op 豁免，force 可抢）。
 * 说明：人肉面板（网页 UI + Windows 输入注入）已按需求移除，不在此插件内。
 */
import { AsyncLocalStorage } from 'node:async_hooks';
import { spawn } from 'node:child_process';
import { createHash } from 'node:crypto';
import { readdirSync, readFileSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { defineTool } from '@deepseek-ai/dsh-tools';
/**
 * v0.8.10（A0 版本自证）：工具描述统一带版本前缀。
 * description 由**已加载的那份 lib** 生成 → 会话里一眼能看出自己跑的是哪一代，
 * 不会再出现「读的是新源码、跑的是旧 lib」那种排查事故（外部反馈 j20-build 的元教训）。
 */
const PLUGIN_VERSION = '0.9.4';
// 注意：必须**先加前缀、再交给 defineTool** —— defineTool 负责校验与规范化，
// 自己 spread 一个成品对象会绕过它（实测：spread 版本会让插件 fiber 直接 failed）。
//
// v0.9.2（DSH 更新适配 R3）：把宿主的 exec（exec.signal = 用户打断 / per-tool 截止）
// 放进 AsyncLocalStorage，传输层 withTimeout() 自动合并它 —— **15 个工具体一行不改**
// 就能响应宿主取消；每个工具另补静态 timeoutMs（见 T），把"上游设默认截止"从
// "Blender 还在跑、工具已被判超时"的静默错位，变成"先由本插件给出可读错误"。
const EXEC_CTX = new AsyncLocalStorage();
/** 当前工具调用对应的宿主 exec.signal（没有则 undefined） */
function execSignal() {
    const s = EXEC_CTX.getStore();
    return s && s.signal instanceof AbortSignal ? s.signal : undefined;
}
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
function losslessSanitize(root) {
    const fixes = [];
    const seen = new Set();
    const walk = (v, at) => {
        if (v === null)
            return null;
        const t = typeof v;
        if (t === 'string' || t === 'boolean')
            return v;
        if (t === 'number') {
            if (!Number.isFinite(v)) {
                fixes.push(at + '=' + String(v) + '→null');
                return null;
            }
            if (Object.is(v, -0)) {
                fixes.push(at + '=-0→0');
                return 0;
            }
            return v;
        }
        if (t === 'undefined') {
            fixes.push(at + '=undefined→丢弃');
            return undefined;
        }
        if (t === 'bigint') {
            const n = Number(v);
            fixes.push(at + '=bigint→' + (Number.isSafeInteger(n) ? 'number' : 'string'));
            return Number.isSafeInteger(n) ? n : String(v);
        }
        if (t === 'function' || t === 'symbol') {
            fixes.push(at + '=' + t + '→丢弃');
            return undefined;
        }
        if (seen.has(v)) {
            fixes.push(at + '=循环引用→[Circular]');
            return '[Circular]';
        }
        seen.add(v);
        let out;
        if (Array.isArray(v)) {
            out = [];
            for (let i = 0; i < v.length; i++) {
                const w = walk(v[i], at + '[' + String(i) + ']');
                out.push(w === undefined ? null : w); // 数组元素不能用 undefined
            }
        }
        else if (v instanceof Date) {
            const ms = v.getTime();
            fixes.push(at + '=Date→ISO');
            out = Number.isFinite(ms) ? v.toISOString() : null;
        }
        else if (v instanceof Map) {
            fixes.push(at + '=Map→数组');
            out = Array.from(v.entries()).map((e, i) => walk(e, at + '<map' + String(i) + '>'));
        }
        else if (v instanceof Set) {
            fixes.push(at + '=Set→数组');
            out = Array.from(v.values()).map((e, i) => walk(e, at + '<set' + String(i) + '>'));
        }
        else if (ArrayBuffer.isView(v) || v instanceof ArrayBuffer) {
            const n = v.byteLength;
            fixes.push(at + '=二进制(' + String(n) + 'B)→{bytes}（要回传给模型请走 attachments/saveImage）');
            out = { bytes: n, why: 'binary-not-json' };
        }
        else if (typeof v.toJSON === 'function' && !isPlainObjectLike(v)) {
            // 宿主没定义 toJSON 语义，但它至少比类实例更可能给出 JSON 形状；失败就退回自有键
            try {
                return walk(v.toJSON(), at + '<toJSON>');
            }
            catch (e) { /* 退回自有可枚举键 */ }
            // eslint-disable-next-line no-fallthrough
            out = {};
            for (const k of Object.keys(v)) {
                const w = walk(v[k], at + '.' + k);
                if (w !== undefined)
                    out[k] = w;
            }
        }
        else {
            out = {};
            for (const k of Object.keys(v)) {
                const w = walk(v[k], at + '.' + k);
                if (w !== undefined)
                    out[k] = w; // 丢键（宿主拒收 undefined 值）
            }
        }
        seen.delete(v); // DAG 共享（非循环）可以重复出现
        return out;
    };
    const value = walk(root, 'value');
    return { value: value === undefined ? null : value, fixes: fixes };
}
/** 普通对象/数组判定（用于决定是否走 toJSON 分支） */
function isPlainObjectLike(v) {
    if (Array.isArray(v))
        return true;
    const proto = Object.getPrototypeOf(v);
    return proto === Object.prototype || proto === null;
}
const vTool = (spec) => defineTool({
    ...spec,
    description: '[v' + PLUGIN_VERSION + '] ' + String((spec && spec.description) || ''),
    // v0.9.4（P0 返回通道）：所有工具**唯一出口**都过一遍 losslessSanitize ——
    // 一个字段写错（NaN / undefined / -0）不该让整条工具通道变成不可用。
    execute: async (args, exec) => {
        const raw = await EXEC_CTX.run(exec || {}, () => spec.execute(args, exec));
        const { value, fixes } = losslessSanitize(raw);
        if (fixes.length) {
            const note = '⚠️ 回执已消毒 ' + String(fixes.length) + ' 处非 lossless-JSON 值（宿主门会拒收 undefined/NaN/Infinity/-0）：'
                + fixes.slice(0, 6).join(' · ') + (fixes.length > 6 ? ' …等' : '');
            try {
                if (value && typeof value === 'object' && typeof value.text === 'string')
                    value.text += String.fromCharCode(10) + note;
                else if (value && typeof value === 'object')
                    value.__losslessNote = note;
                console.warn('[dsh-blender] ' + note);
            }
            catch (e) { /* 提示失败不影响返回 */ }
        }
        return value;
    },
});
import z from 'schemastery';
export const name = '@dsh-external/dsh-blender-plugin';
export const inject = ['tools'];
// 后端与 Blender 侧模块都放在插件包的 runtime/ 下 → 整个包可搬到任意目录（WSL 或 Windows）
const HERE = path.dirname(fileURLToPath(import.meta.url));
const SERVER_PATH = process.env.DSH_BLENDER_SERVER || path.join(HERE, '..', 'runtime', 'server.mjs');
const HOSTNAME = '127.0.0.1';
/**
 * 端口解析（与 runtime/config.mjs 同一套来源，避免两处配置漂移）：
 *   插件配置 port → DSH_BLENDER_HTTP_PORT → $DSH_BLENDER_CONFIG → <包根>/dsh-blender.config.json
 *   → ~/.dsh/dsh-blender.config.json → 9877
 */
function resolveHttpPort() {
    const env = Number(process.env.DSH_BLENDER_HTTP_PORT);
    if (Number.isFinite(env) && env > 0)
        return env;
    const cands = [
        process.env.DSH_BLENDER_CONFIG,
        path.join(HERE, '..', 'dsh-blender.config.json'),
        path.join(os.homedir(), '.dsh', 'dsh-blender.config.json'),
    ].filter(Boolean);
    for (const p of cands) {
        try {
            const j = JSON.parse(readFileSync(p, 'utf8'));
            const n = Number(j && j.httpPort);
            if (Number.isFinite(n) && n > 0)
                return n;
        }
        catch (e) { /* 没这个文件或不是 JSON：跳过 */ }
    }
    return 9877;
}
/** 后端未就绪时的统一提示（不假设任何本机路径） */
const BACKEND_HINT = '先确认 Blender 在运行且 addon（MCP for Blender）已 Connect；然后 blender_viewport op=start 拉起后端。'
    + '端口/工作目录/blender.exe 可用 DSH_BLENDER_* 环境变量或包根 dsh-blender.config.json 配置（见 docs/配置参考.md）。';
/**
 * v0.9.2（R3）：给宿主的**静态**截止时间 —— defineTool 只收常量，不能按参数动态给。
 * 取值原则：一律 = 该工具内部最长超时 + 余量 → 内部超时永远先触发，模型拿到的是本插件
 * 的可读错误；宿主的 TOOL_TIMEOUT 只兜"连内部超时都没兜住"的最后一道。
 * 背景：宿主 0.1.6 起装配 @deepseek-ai/dsh-tool-call-timeout-policy，但**只在工具自己
 * 声明 timeoutMs 时才设截止**；不声明 = 上游一旦给默认值，Blender 还在跑而工具已被判超时。
 */
const T = {
    see: 300000, // 内部 max(/view 180s, /frame.png 60s) + 余量
    act: 300000, // /act 180s + 回帧 60s + 余量
    watch: 600000, // /act 180s + 最多 32 帧（每帧最长 60s）
    cmd: 360000, // /cmd 内部 300s
    commands: 60000, // 只读清单
    loop: 360000, // /loop 内部 300s（内环预算在 spec.budget_ms 里另算）
    perf: 660000, // /perf 内部 600s
    opt: 660000, // /opt 内部 600s
    // v0.9.3（D1）：headless 不再"陪跑到超时" —— 客户端等待窗口（DSH_HEADLESS_WAIT_MS，默认 100 s）到点就回
    // promoted 回执（jobId），服务端子进程继续跑。静态截止留大余量即可（自定义大窗口的用户也不会被判超时）。
    headless: 1200000,
    plan: 360000, // /plan 内部 300s
    worker: 1920000, // exec timeout_ms 由调用方给；静态上限取 32min
    txn: 360000, // /txn 内部 300s
    preset: 360000, // /preset 内部 300s
    // v0.9.3（D6.2）：job 新增 op=wait（服务端单次上限 600 s）→ 静态截止要盖住它
    job: 660000,
    viewport: 60000, // status/doctor/lease + start/restart 的重试窗口
};
/**
 * v0.9.2（R2 自证）：插件 import 到的 @deepseek-ai/dsh-tools 是哪一份、哪一版。
 * 本机解析链：插件 node_modules → ~/dsh-harness（fake checkout）→ /usr/lib/node_modules
 * 的**全局安装**。DSH 一升级，这里就是升级后那份；而全局安装位置一变，插件会在载入期
 * ERR_MODULE_NOT_FOUND（15 个工具整体消失）。doctor/status 带上它，用来一眼区分
 * "插件/宿主 API 坏了"与"Blender 没起"。
 */
function hostApiInfo() {
    try {
        const resolveFn = import.meta.resolve;
        if (typeof resolveFn !== 'function')
            return 'dsh-tools: import.meta.resolve 不可用（node < 20.6?）';
        const url = String(resolveFn.call(import.meta, '@deepseek-ai/dsh-tools') || '');
        let p = url;
        if (p.startsWith('file://'))
            p = fileURLToPath(p);
        let ver = '?';
        try {
            ver = JSON.parse(readFileSync(path.join(path.dirname(path.dirname(p)), 'package.json'), 'utf8')).version;
        }
        catch (e) { /* 版本读不到不算错 */ }
        return 'dsh-tools@' + String(ver) + ' @ ' + p;
    }
    catch (e) {
        return 'dsh-tools 解析失败：' + String((e && e.message) || e) + '（插件会在载入期就失败：检查全局安装位置是否变了）';
    }
}
const HOST_API = hostApiInfo();
export const Config = z.object({
    port: z.natural().default(resolveHttpPort()),
    autoStart: z.boolean().default(true),
});
let child = null;
/** 用户用 op=stop 显式停过 → 看护不再自动拉起（op=start 解除） */
let paused = false;
function base(port) {
    return 'http://' + HOSTNAME + ':' + String(port);
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function probe(port, timeoutMs = 1500) {
    const ac = new AbortController();
    const timer = setTimeout(() => ac.abort(), timeoutMs);
    try {
        const r = await fetch(base(port) + '/health', { signal: ac.signal });
        return r.ok;
    }
    catch (e) {
        return false;
    }
    finally {
        clearTimeout(timer);
    }
}
async function ensureBackend(port) {
    if (await probe(port))
        return true;
    if (paused)
        return false;
    try {
        startBackend(port);
    }
    catch (e) { /* ignore */ }
    for (let i = 0; i < 25; i++) {
        if (await probe(port))
            return true;
        await sleep(300);
    }
    return false;
}
/** 子进程是否真的还活着：被信号杀掉时 exitCode 仍为 null、只有 signalCode 有值 */
function childAlive() {
    return !!(child && child.exitCode === null && child.signalCode === null && child.pid);
}
function startBackend(port) {
    if (childAlive())
        return 'already-spawned';
    child = null;
    child = spawn(process.execPath, [SERVER_PATH, '--port', String(port)], { stdio: 'ignore', detached: true });
    child.unref();
    return 'spawned pid=' + String(child.pid);
}
/**
 * 按 /proc 扫出"本插件历史代次拉起的后端"，SIGTERM 掉。
 * 热重载会丢掉 child 句柄（上一代 fiber 持有），只靠句柄停不掉残留进程 —— 这是兜底。
 */
function killBackendByCmd(port) {
    const killed = [];
    try {
        for (const d of readdirSync('/proc')) {
            if (!/^[0-9]+$/.test(d))
                continue;
            if (Number(d) === process.pid)
                continue;
            try {
                const cmd = readFileSync('/proc/' + d + '/cmdline', 'utf8').replace(/\u0000/g, ' ');
                // 路径已随插件包改名（dsh-blender-plugin/runtime/server.mjs）→ 用更通用的匹配，否则停不掉残留
                if (cmd.includes('server.mjs') && cmd.includes('--port ' + String(port))) {
                    process.kill(Number(d), 'SIGTERM');
                    killed.push(d);
                }
            }
            catch (e) { /* 进程可能已退出 */ }
        }
    }
    catch (e) { /* /proc 不可读则不兜底 */ }
    return killed;
}
/** 宿主取消（exec.signal abort）时的统一措辞：只说"等待被取消"，不谎称任务失败 */
const ABORT_HINT = '宿主取消了本次等待（exec.signal 已 abort：用户打断或 per-tool 截止）。'
    + '服务端任务可能仍在跑 —— 长任务请用 blender_rt_job 并按 runId 回收；'
    + '本插件的内部超时通常先于宿主截止触发，正常情况看到的是上面这类可读错误。';
/**
 * v0.9.2：合并「本插件内部超时」与「宿主 exec.signal」。
 * 两条都只影响**等待**：服务端已开始的 Blender 侧任务照旧跑完（headless/job 既有语义
 * = 客户端断连 ≠ 任务失败，按 runId 回收）。
 */
async function withTimeout(path, init, timeoutMs) {
    const ac = new AbortController();
    const ext = execSignal();
    let fired = false;
    const timer = setTimeout(() => { fired = true; ac.abort(); }, timeoutMs);
    const signal = ext ? AbortSignal.any([ext, ac.signal]) : ac.signal;
    try {
        return await fetch(path, { ...(init || {}), signal });
    }
    catch (e) {
        if (!fired && ext && ext.aborted)
            throw new Error(ABORT_HINT);
        throw e;
    }
    finally {
        clearTimeout(timer);
    }
}
async function backendJson(port, path, timeoutMs = 20000, init) {
    const r = await withTimeout(base(port) + path, init, timeoutMs);
    const text = await r.text();
    try {
        return JSON.parse(text);
    }
    catch (e) {
        return { ok: false, raw: text.slice(0, 500) };
    }
}
/** 本会话在写通道上的身份（租约 holder）；跨会话共用同一个后端时用它区分 */
const HOLDER = process.env.DSH_BLENDER_HOLDER || ('plugin-pid-' + String(process.pid));
/**
 * 解析后端响应（v0.8.9 修）：后端有两种形态 ——
 *   ① 普通路由：整段就是一个 JSON；
 *   ② 流式路由（/headless /worker /txn /preset）：NDJSON，**每 15 s 先写一行** {"heartbeat":true,...}，最后一行才是结果。
 * 旧实现整段 JSON.parse，于是任何 **超过 15 s** 的调用都会抛 Extra data → 调用方只看到 exit=undefined（子进程其实跑完了）。
 * 现在：解析失败就按行往前找最后一条非心跳 JSON；同时把心跳条数记进 __heartbeats 供工具层提示。
 */
function parseBackendBody(text) {
    const t = String(text == null ? '' : text);
    try {
        const o = JSON.parse(t);
        if (o && typeof o === 'object')
            return o;
    }
    catch (e) { /* 流式响应，往下走 */ }
    const lines = t.split('\n').map((s) => s.trim()).filter(Boolean);
    let last = null;
    let beats = 0;
    for (let i = lines.length - 1; i >= 0; i--) {
        let o = null;
        try {
            o = JSON.parse(lines[i]);
        }
        catch (e) {
            continue;
        }
        if (!o || typeof o !== 'object')
            continue;
        if (o.heartbeat === true)
            continue;
        last = o;
        break;
    }
    for (let i = 0; i < lines.length; i++) {
        try {
            const o = JSON.parse(lines[i]);
            if (o && o.heartbeat === true)
                beats++;
        }
        catch (e) { /* 非 JSON 行 */ }
    }
    if (last) {
        last.__heartbeats = beats;
        last.__streamLines = lines.length;
        return last;
    }
    return { ok: false, raw: t.slice(-500), streamLines: lines.length, parseError: 'no JSON line found' };
}
/**
 * 写操作统一走它：自动带 holder（租约门禁用），并保留 HTTP 状态码便于识别 409。
 *
 * v0.9.3（D1，关键）：**计时器必须活到 body 读完**。
 * 旧版是 withTimeout(fetch) → fetch 在**收到响应头**时就 resolve（流式路由 /headless 立刻发头 + 每 15 s 心跳），
 * 于是超时预算只覆盖「等响应头」，随后的 r.text() 无限期等下去 —— 客户端等待窗口形同虚设，
 * 外层 run_code 的 deadline 一到就整段丢结果（正是 D1 报的现象）。
 * 现在把 text() 也放进同一个 AbortController：到点 abort → fetch / body 读取一起抛 AbortError。
 */
async function backendPost(port, path, body, timeoutMs = 60000) {
    const ac = new AbortController();
    const ext = execSignal();
    let fired = false;
    const timer = setTimeout(() => { fired = true; ac.abort(); }, timeoutMs);
    const signal = ext ? AbortSignal.any([ext, ac.signal]) : ac.signal;
    try {
        const r = await fetch(base(port) + path, {
            method: 'POST',
            headers: { 'content-type': 'application/json' },
            body: JSON.stringify({ ...(body || {}), holder: HOLDER }),
            signal,
        });
        const text = await r.text();
        const j = parseBackendBody(text);
        j.__status = r.status;
        return j;
    }
    catch (e) {
        if (!fired && ext && ext.aborted)
            throw new Error(ABORT_HINT);
        throw e;
    }
    finally {
        clearTimeout(timer);
    }
}
/** v0.9.1：工具入参容错 —— harness 的 `type:"json"` 有时把对象给成**字符串**（实测同一会话里两种形态都出现过），
 *  旧写法 `typeof x === 'object'` 会静默丢参（表现为"参数没生效却 ok:true"，非常难查）。 */
function jsonPayload(v) {
    if (v === undefined || v === null || v === '')
        return {};
    if (typeof v === 'string') {
        try {
            const p = JSON.parse(v);
            return (p && typeof p === 'object' && !Array.isArray(p)) ? p : {};
        }
        catch (e) {
            return {};
        }
    }
    if (typeof v === 'object' && !Array.isArray(v))
        return { ...v };
    return {};
}
/** 把 "‑‑flag \"a b\" c" 这样的字符串切成 argv（引号成对时不当分隔符；v0.8.9 修 headless 的 args 引号问题）
 *  v0.9.1：数组直接透传（旧版 `String([...])` 会变成 "[delta]" 这种带方括号的单个 token） */
function splitArgs(s) {
    if (Array.isArray(s))
        return s.map((x) => String(x));
    const str = String(s == null ? '' : s);
    const out = [];
    let cur = '';
    let q = null;
    for (let i = 0; i < str.length; i++) {
        const c = str[i];
        if (q) {
            if (c === q)
                q = null;
            else
                cur += c;
            continue;
        }
        if (c === '"' || c === "'") {
            q = c;
            continue;
        }
        if (/\s/.test(c)) {
            if (cur) {
                out.push(cur);
                cur = '';
            }
            continue;
        }
        cur += c;
    }
    if (cur)
        out.push(cur);
    return out;
}
/** 409 leased → 给模型一句能照着做的话（而不是丢一段 JSON） */
function leasedText(r) {
    if (!r || r.error !== 'leased')
        return undefined;
    return '写通道被别的会话占用（holder=' + String(r.holder) + '，剩余 ' + String(Math.round(Number(r.expiresInMs || 0) / 1000)) + 's）。'
        + String(r.hint || '') + '；要强抢：blender_viewport op=lease force=true';
}
/** 拉一帧 PNG 字节（含耗时）；full=true 走 screenshot_area（整窗口/区域） */
async function fetchFrame(port, size, full = false, area = 0) {
    const t0 = Date.now();
    const url = base(port) + '/frame.png?' + (full ? ('full=1&area=' + String(area)) : ('size=' + String(size)));
    const r = await withTimeout(url, {}, 60000);
    if (!r.ok)
        throw new Error('frame http ' + String(r.status));
    const buf = new Uint8Array(await r.arrayBuffer());
    return { png: buf, ms: Date.now() - t0, hash: createHash('md5').update(buf).digest('hex').slice(0, 8) };
}
/** 自定义视角出图：POST /view（view.py）——不动物体、不动用户视口 */
async function fetchView(port, spec, timeoutMs = 180000) {
    const t0 = Date.now();
    const r = await withTimeout(base(port) + '/view', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(spec || {}),
    }, timeoutMs);
    if (!r.ok) {
        const text = await r.text();
        let j = null;
        try {
            j = JSON.parse(text);
        }
        catch (e) {
            j = null;
        }
        throw new Error('view http ' + String(r.status) + ' · ' + String((j && j.error) || text.slice(0, 300)));
    }
    let meta = null;
    try {
        const rawHdr = String(r.headers.get('x-dsh-view') || 'null');
        // v0.9.1：头值 URL 编码（非 ASCII 不能进头）—— 老后端是裸 JSON，两种都兼容
        const txt = rawHdr.indexOf('%7B') === 0 ? decodeURIComponent(rawHdr) : rawHdr;
        meta = JSON.parse(txt);
    }
    catch (e) {
        meta = null;
    }
    const buf = new Uint8Array(await r.arrayBuffer());
    return { png: buf, ms: Date.now() - t0, meta: meta, hash: createHash('md5').update(buf).digest('hex').slice(0, 8) };
}
function numTriple(v) {
    if (v === undefined || v === null || v === '')
        return undefined;
    if (Array.isArray(v))
        return v.map((x) => Number(x));
    return String(v).split(/[,\s]+/).filter(Boolean).map((x) => Number(x));
}
/** PNG → durable attachment（v0.9.2 起失败**不再静默**：返回 {ref} 或 {why}，由调用方写进工具文本） */
async function toAttachment(ctx, data, name) {
    try {
        const attachments = ctx.get('attachments');
        if (!attachments)
            return { why: '宿主没有 attachments 服务（ctx.get("attachments") 为空）' };
        const mediaTypes = attachments.imageLimits?.mediaTypes ?? [];
        if (!mediaTypes.includes('image/png'))
            return { why: '宿主 imageLimits.mediaTypes 不含 image/png（' + JSON.stringify(mediaTypes) + '）' };
        const ref = await attachments.saveImage({ data, mediaType: 'image/png', name });
        return {
            ref: {
                attachmentId: ref.attachmentId,
                mediaType: ref.mediaType,
                bytes: ref.bytes,
                width: ref.width,
                height: ref.height,
                ...(ref.name === undefined ? {} : { name: ref.name }),
            },
        };
    }
    catch (e) {
        return { why: 'saveImage 失败：' + String((e && e.message) || e) };
    }
}
function renderOne(_args, value) {
    const blocks = [{ type: 'text', text: String((value && value.text) || '') }];
    if (value && value.image)
        blocks.push({ type: 'image', attachment: value.image });
    if (value && Array.isArray(value.images))
        for (const im of value.images)
            blocks.push({ type: 'image', attachment: im });
    return blocks;
}
/* ────────────────────────────────────────────────────────────────────────────
 * v0.9.3（D2）：结构化回执 —— **两个 text block**
 *   block 0 = 单行 JSON 信封（可直接 JSON.parse，字段稳定；不必再手写正则从
 *             "中文前言 + stdout 尾 + result: {...}" 里抠）
 *   block 1 = 人读摘要（原样保留旧版的叙述性文本）
 * 为什么用两个 block：宿主对 tool 结果只透传 ContentBlock[]（text/image/file），
 * 没有 json block；而**每个 text block 在 provider 侧是一个独立的 text part**，
 * 所以"第一块就是纯 JSON"等价于"调用方拿得到一个可解析的结构化回执"。
 * ──────────────────────────────────────────────────────────────────────────── */
/** 安全 JSON.stringify（循环引用/大对象不炸；超长返回 null 由调用方给路径） */
function safeStringify(v) {
    try {
        return JSON.stringify(v);
    }
    catch (e) {
        return null;
    }
}
/** 截断（保留头尾，中间省略标记）—— 与后端 clipMiddle 同一口径 */
function clipText(s, head, tail) {
    const t = String(s == null ? '' : s);
    if (t.length <= head + tail)
        return t;
    return t.slice(0, head) + String.fromCharCode(10) + '…[中间省略 ' + String(t.length - head - tail) + ' 字符]…' + String.fromCharCode(10) + t.slice(-tail);
}
/** 带结构化信封的工具回执：value = {text, envelope?, image?, images?} */
function renderStructured(_args, value) {
    const blocks = [];
    if (value && value.envelope) {
        const s = safeStringify(value.envelope);
        if (s)
            blocks.push({ type: 'text', text: s });
    }
    blocks.push({ type: 'text', text: String((value && value.text) || '') });
    if (value && value.image)
        blocks.push({ type: 'image', attachment: value.image });
    if (value && Array.isArray(value.images))
        for (const im of value.images)
            blocks.push({ type: 'image', attachment: im });
    return blocks;
}
/**
 * v0.9.3（D1/F3）：客户端单次等待上限。超过它就**不干等**，转作业层并回 jobId（对齐 bash 的 promoted 语义）。
 * 默认 100 s：外层 run_code 的默认 deadline 是 120 s，留 20 s 余量给回执与状态查询（promoted 路径只是一次很快的 /job 查询）。
 * 调大它 = 允许单次同步等更久（代价：外层 deadline 到点时更容易"结果在服务端、客户端拿不到"）。
 */
const HEADLESS_WAIT_MS = (() => {
    const n = Number(process.env.DSH_HEADLESS_WAIT_MS);
    // 下限 1000ms：验收测试会把它调到秒级来复现 promoted 路径（别把合法的小窗口当成脏值）
    return Number.isFinite(n) && n >= 1000 ? n : 100000;
})();
/** run id 由**客户端**生成：窗口到点时立刻就能报出一个可用句柄（服务端会用它登记台账） */
function newRunId() {
    return 'run-' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
}
/** 后台化 + 可选等待（F3）：POST /headless asJob:true 拿 jobId，再 op=wait 等一小会儿 */
async function startJobThenMaybeWait(port, body, waitS) {
    const jr = await backendPost(port, '/headless', Object.assign({}, body, { asJob: true }), 60000);
    const lt = leasedText(jr);
    if (lt)
        return { text: lt };
    if (!jr || jr.error)
        return { text: 'HEADLESS 转作业层失败 · ' + String((jr && (jr.error || jr.raw)) || 'unknown') };
    // 后端 /headless 的回包是 {ok, ms, result:<引擎回执>} —— jobId 在 result 里
    const inner = (jr.result && typeof jr.result === 'object') ? jr.result : jr;
    const jobId = String(inner.jobId || (inner.job && inner.job.id) || '');
    const budget = waitS && waitS > 0 ? Math.min(Math.round(waitS * 1000), HEADLESS_WAIT_MS + 2000) : 0;
    if (!budget)
        return { jobId: jobId, job: inner.job || null, waited: false };
    const wr = await backendPost(port, '/job', { op: 'wait', id: jobId, timeoutMs: budget }, budget + 30000);
    const lt2 = leasedText(wr);
    if (lt2)
        return { text: lt2 };
    const job = (wr && wr.job) || null;
    if (job && job.status && job.status !== 'running')
        return { jobId: jobId, job: job, waited: true, finished: true };
    return { jobId: jobId, job: job || inner.job || null, waited: true, finished: false };
}
/** shots 回执归一化：sync 路径给 {rows:[…]}，job 快照给 qc_render 原样的 {views:[…]} */
function normalizeShots(s) {
    if (!s || typeof s !== 'object')
        return null;
    let rows = Array.isArray(s.rows) ? s.rows : null;
    if (!rows && Array.isArray(s.views)) {
        rows = s.views.map((e) => {
            const cov = (e && e.frame && typeof e.frame.coverage === 'number') ? e.frame.coverage : null;
            const row = { name: (e && e.view) || null, path: (e && e.path) || null, ms: (e && e.ms) || null,
                bytes: (e && e.bytes) || null, md5: (e && e.hash) || null, res: (e && e.res) || null,
                device: (e && e.device) || null, coverage_estimate: cov };
            if (cov !== null && cov < 0.05)
                row.warning = { code: 'subject_too_small', coverage_estimate: cov };
            return row;
        });
    }
    const covs = (rows || []).map((r) => r.coverage_estimate).filter((x) => typeof x === 'number');
    return { ok: !!s.ok, count: (rows || []).length, rows: rows || [],
        coverage_min: (typeof s.coverage_min === 'number') ? s.coverage_min : (covs.length ? Math.min.apply(null, covs) : null),
        outdir: s.outdir || null, jsonl: s.jsonl || null, total_ms: s.total_ms || null,
        engine: s.engine || null, samples: s.samples || null, device_line: s.device_line || null,
        unknown_views: s.unknown_views || null, error: s.error || null };
}
/** 结构化信封（block 0）：稳定字段 + 有界大小（大结果只给 resultPath） */
function receiptEnvelope(res, ctx) {
    const r = res || {};
    const rj = (r.resultJson !== undefined) ? r.resultJson : r.result;
    const rjStr = (rj === null || rj === undefined) ? null : safeStringify(rj);
    const ENV_LIMIT = 8000;
    const big = !!(rjStr && rjStr.length > ENV_LIMIT);
    const artifacts = (Array.isArray(r.artifacts) ? r.artifacts : []).slice(0, 40)
        .map((a) => (typeof a === 'string' ? { name: a } : a));
    return {
        kind: ctx.mode === 'job' ? 'job' : (r.kind || 'headless'),
        mode: ctx.mode,
        status: r.status || null,
        // 作业层快照没有 ok 字段：按终态推断（done/finished=成功；failed/killed/timeout/stale=失败；running=未判定）
        ok: (typeof r.ok === 'boolean') ? r.ok
            : ((r.status === 'done' || r.status === 'finished') ? true
                : ((r.status === 'failed' || r.status === 'killed' || r.status === 'timeout' || r.status === 'stale') ? false : null)),
        runId: r.runId || ctx.runId || null,
        jobId: ctx.jobId || ((r.id && String(r.id).indexOf('job-') === 0) ? r.id : null),
        id: r.id || null,
        // v0.9.4（P0 返回通道）：这里原来是 `? true : undefined` —— 宿主 dsh-util-values 的
        // lossless-JSON 门**拒收值为 undefined 的自有可枚举键**（JSON.stringify 会把它丢掉，
        // 所以肉眼看不出问题），于是 blender_rt_headless / rt_job 的每一条回执都被判
        // `value is not lossless JSON`，无头批处理这条第一路径整条被打死。改成「有才给键」。
        ...(ctx.mode === 'promoted' ? { promoted: true } : {}),
        ms: (typeof r.ms === 'number') ? r.ms : null,
        timeoutMs: r.timeoutMs || null,
        exitCode: (r.exitCode === undefined) ? null : r.exitCode,
        timedOut: !!r.timedOut,
        resultJson: big ? null : (rj === undefined ? null : rj),
        resultTruncated: !!(r.resultTruncated || big),
        resultPath: r.resultPath || r.outJson || null,
        resultPathWsl: r.resultPathWsl || r.outJsonWsl || null,
        resultBytes: r.resultBytes || null,
        resultParseError: r.resultParseError || null,
        stdoutTail: clipText(r.stdoutTail !== undefined && r.stdoutTail !== null ? r.stdoutTail : r.stdout, 1200, 1200),
        stderrTail: clipText(r.stderrTail !== undefined && r.stderrTail !== null ? r.stderrTail : r.stderr, 800, 800),
        logs: r.logs || (r.stdoutLog ? { stdout: r.stdoutLog, stderr: r.stderrLog } : null),
        outdir: r.outdir || null,
        artifacts: artifacts,
        shots: normalizeShots(r.shots),
        inputFile: r.inputFile || null,
        pathWarnings: r.pathWarnings || [],
        stage: r.stage || r.stageName || null,
        stageAt: r.stageAt || null,
        idleMs: (typeof r.idleMs === 'number') ? r.idleMs : null,
        lines: (typeof r.lines === 'number') ? r.lines : null,
        logBytes: (typeof r.logBytes === 'number') ? r.logBytes : null,
        engine: r.engine || null,
        // v0.9.4：这里原来是裸的 r.gpu.fell_back_to_cpu —— 后端没给这个字段时值就是 undefined，
        // 又一个「宿主门拒收 undefined 值」的实例（回归测试 tests/lossless_guard.mjs 抓到的第二处）。
        gpu: r.gpu ? { ok: !!r.gpu.ok, fell_back_to_cpu: !!r.gpu.fell_back_to_cpu,
            device: (r.gpu.after && r.gpu.after.device_type) || r.gpu.configured || null } : null,
        scriptFile: r.scriptFile || null,
        expect: r.expect || null,
        notes: ctx.notes || [],
        hint: ctx.hint || null,
    };
}
/** 人读摘要（block 1）—— 沿用 v0.9.2 的叙述口径，读的是 v0.9.3 的结构化字段 */
function headlessHumanText(res, ctx) {
    const r = res || {};
    const NLx = String.fromCharCode(10);
    const parts = [];
    const okFlag = (typeof r.ok === 'boolean') ? r.ok
        : ((r.status === 'done' || r.status === 'finished') ? true
            : ((r.status === 'failed' || r.status === 'killed' || r.status === 'timeout' || r.status === 'stale') ? false : null));
    const st = r.status || (okFlag ? 'finished' : 'unknown');
    parts.push((okFlag === true ? 'HEADLESS ok' : (okFlag === false ? 'HEADLESS 失败' : 'HEADLESS 进行中')) + ' · status=' + String(st)
        + (r.exitCode === undefined || r.exitCode === null ? '' : (' · exit=' + String(r.exitCode)))
        + (r.timedOut ? ' · 超时被杀' : '')
        + (typeof r.ms === 'number' ? (' · ' + String(r.ms) + 'ms') : '')
        + (r.reason ? (' · ' + String(r.reason)) : ''));
    if (ctx && ctx.mode === 'job')
        parts.push('模式：作业层（jobId=' + String(ctx.jobId || '') + '，等待窗口内已完成）');
    if (r.failure_hint)
        parts.push('提示：' + String(r.failure_hint));
    if (r.session)
        parts.push('会话：' + String(r.session));
    if (r.script)
        parts.push('脚本：' + String(r.script));
    if (r.scriptFile)
        parts.push('脚本文件：' + String(r.scriptFile.resolved || r.scriptFile.given) + '（' + String(r.scriptFile.bytes) + ' 字节）');
    if (r.inputFile && r.inputFile.md5)
        parts.push('输入文件：' + String(r.inputFile.path) + ' · ' + String(r.inputFile.size) + ' 字节 · mtime=' + String(r.inputFile.mtime || r.inputFile.mtimeMs) + ' · md5=' + String(r.inputFile.md5) + (r.inputFile.md5Partial ? '（部分哈希）' : ''));
    const engName = r.engine || (r.gpu && r.gpu.after && r.gpu.after.engine) || null;
    if (engName)
        parts.push('引擎：' + String(engName) + (r.engineMode ? ('（mode=' + String(r.engineMode) + '）') : ''));
    if (r.gpu) {
        const g = r.gpu;
        parts.push('GPU：' + (g.fell_back_to_cpu ? ('⚠️ 回落 CPU（' + String(g.error || (g.before && g.before.device_type) || '') + '）')
            : ('✅ ' + String((g.after && g.after.device_type) || g.configured || '') + ' · ' + String(((g.after && g.after.gpu_enabled) || []).join(','))))
            + (g.configured ? (' · 本次已自动配置 ' + String(g.configured)) : ''));
    }
    if (r.stage || r.stageName)
        parts.push('最后阶段（stage）：' + safeStringify(r.stage || r.stageName));
    if (r.lastException)
        parts.push('最后异常：' + String(r.lastException));
    if (r.logs)
        parts.push('日志：' + String(r.logs.stdout || '') + ' · ' + String(r.logs.stderr || ''));
    const rj = (r.resultJson !== undefined) ? r.resultJson : r.result;
    if (rj !== null && rj !== undefined) {
        const s = safeStringify(rj) || '';
        parts.push('result: ' + (s.length > 6000 ? (s.slice(0, 6000) + NLx + '…[result 共 ' + String(s.length) + ' 字符；全文见 resultPath]') : s));
    }
    if (r.resultPath)
        parts.push('result 已落盘：' + String(r.resultPath) + (r.resultPathWsl ? ('（WSL：' + String(r.resultPathWsl) + '）') : '') + ' · ' + String(r.resultBytes || 0) + ' 字节，全量 JSON');
    if (r.resultParseError)
        parts.push('⚠️ 末行 HEADLESS 不是合法 JSON（**原始输出已保留**，见下面的 stdout）· ' + String(r.resultParseError.message || '') + NLx + '  该行：' + String(r.resultParseError.line || '').slice(0, 300));
    if (Array.isArray(r.shots && r.shots.rows) ? r.shots.rows.length : false) {
        parts.push('shots（' + String(r.shots.rows.length) + ' 张，覆盖度=非底色像素占比）：' + NLx
            + r.shots.rows.map((x) => '  · ' + String(x.name) + ' · ' + String(x.ms) + 'ms · ' + String(x.bytes) + 'B · md5=' + String(x.md5)
                + (typeof x.coverage_estimate === 'number' ? (' · coverage=' + (x.coverage_estimate * 100).toFixed(2) + '%') : '')
                + (x.warning ? ('  ⚠️ ' + String((x.warning && x.warning.hint) || x.warning.code)) : '')).join(NLx));
        if (r.shots.jsonl)
            parts.push('shots jsonl：' + String(r.shots.jsonl));
    }
    else if (r.shots && r.shots.error)
        parts.push('⚠️ shots 失败：' + String(r.shots.error));
    if (Array.isArray(r.pathWarnings) && r.pathWarnings.length) {
        parts.push('⚠️ 路径体检（' + String(r.pathWarnings.length) + ' 条）：' + NLx
            + r.pathWarnings.map((w) => '  · ' + String(w.code) + ' · ' + String(w.evidence || w.value || '') + NLx + '    ' + String(w.hint || '')).join(NLx));
    }
    if (r.stdoutTruncated)
        parts.push('（stdout 已截断：全量见 logs 路径 / resultPath）');
    const so = String(r.stdoutTail !== undefined && r.stdoutTail !== null ? r.stdoutTail : (r.stdout || ''));
    if (so)
        parts.push((r.stdoutTruncated ? '--- stdout 尾（已截断）---' : '--- stdout ---') + NLx + (rj !== null && rj !== undefined ? clipText(so, 500, 800) : clipText(so, 1500, 2500)));
    const se = String(r.stderrTail !== undefined && r.stderrTail !== null ? r.stderrTail : (r.stderr || ''));
    if (se.trim())
        parts.push('--- stderr（头+尾）---' + NLx + clipText(se, 500, 1500));
    if (r.expect) {
        const ex = r.expect, obs = ex.observed || {};
        parts.push('expect 校验：' + (ex.ok ? '✅ 通过' : '❌ **未通过**')
            + (obs.delta ? (' · 新增对象 ' + String(obs.delta.objects) + ' / 网格 ' + String(obs.delta.meshes) + '（before ' + String((obs.before || {}).objects) + ' → after ' + String((obs.after || {}).objects) + '）') : '')
            + ((ex.warnings || []).length ? (NLx + '  ' + ex.warnings.join(NLx + '  ')) : ''));
    }
    const arts = Array.isArray(r.artifacts) ? r.artifacts : [];
    if (arts.length)
        parts.push('产物：' + arts.slice(0, 12).map((a) => (typeof a === 'string' ? a : (a.name + '(' + a.bytes + 'B' + (a.fresh ? ' 新' : '') + ')'))).join(' · '));
    if (r.noiseFiltered)
        parts.push('（已过滤 ' + String(r.noiseFiltered) + ' 个噪音产物）');
    if (r.hint)
        parts.push('提示：' + String(r.hint));
    if (r.traceback)
        parts.push('--- traceback ---' + NLx + String(r.traceback).slice(0, 1200));
    if (r.id && String(r.id).indexOf('job-') === 0)
        parts.push('作业号：' + String(r.id) + '（runId=' + String(r.runId || '-') + '）');
    if (r.runId)
        parts.push('运行号：' + String(r.runId) + '（blender_rt_job(op="status"/"collect"/"wait", id=…) 可查；后端重启后也能从磁盘台账查到）');
    if (Array.isArray(ctx && ctx.notes))
        for (const n of ctx.notes)
            parts.push('· ' + String(n));
    return parts.join(NLx);
}
/** 无头回执（sync / 作业层完成）：{text, envelope} —— 两个 text block 的来源 */
function headlessReceipt(res, ctx) {
    return { text: headlessHumanText(res, ctx), envelope: receiptEnvelope(res, ctx) };
}
/**
 * v0.9.3（D1）：**promoted 回执** —— 任务还在跑（或已结束但我们没等到），句柄已经有效。
 * 与 bash 通道的 promoted 语义对齐：kind="promoted" + jobId，note 里写清「怎么收结果」。
 */
function promotedReceipt(job, jobId, why) {
    const NLx = String.fromCharCode(10);
    const j = job || {};
    const st = j.status || 'running';
    const logs = j.logs || (j.stdoutLog ? { stdout: j.stdoutLog, stderr: j.stderrLog } : null);
    const env = {
        kind: 'promoted', mode: 'promoted', promoted: true, status: st, ok: null,
        jobId: jobId, runId: j.runId || jobId, id: j.id || jobId,
        ms: (typeof j.ms === 'number') ? j.ms : null,
        stage: j.stage || j.stageName || null,
        idleMs: (typeof j.idleMs === 'number') ? j.idleMs : null,
        lines: (typeof j.lines === 'number') ? j.lines : null,
        logBytes: (typeof j.logBytes === 'number') ? j.logBytes : null,
        resultJson: j.resultJson || null, resultTruncated: !!j.resultTruncated,
        resultPath: j.resultPath || null, resultPathWsl: j.resultPathWsl || null,
        stdoutTail: clipText(j.stdoutTail, 1200, 1200), stderrTail: clipText(j.stderrTail, 800, 800),
        logs: logs, outdir: j.outdir || null, artifacts: (j.artifacts || []).slice(0, 40),
        shots: normalizeShots(j.shots), inputFile: j.inputFile || null,
        pathWarnings: j.pathWarnings || [],
        hint: '用 blender_rt_job(op="wait", id="' + jobId + '") 阻塞等结果，或 op="collect" 看进展（stage/idleMs）；op="list" 看全部作业',
        notes: [why],
    };
    const parts = [];
    parts.push('⏳ 已后台化（**不是失败**）· ' + why);
    parts.push('句柄：' + jobId + ' · status=' + String(st)
        + (env.stage ? (' · stage=' + safeStringify(env.stage)) : '')
        + (typeof env.idleMs === 'number' ? (' · 最近输出 ' + String(Math.round(env.idleMs / 1000)) + 's 前') : ''));
    if (logs)
        parts.push('日志（运行期就有增量：PYTHONUNBUFFERED=1）：' + String(logs.stdout || '') + ' · ' + String(logs.stderr || ''));
    if (env.stdoutTail)
        parts.push('--- stdout 尾 ---' + NLx + String(env.stdoutTail).slice(-1500));
    parts.push('收结果：blender_rt_job(op="wait", id="' + jobId + '", timeout_ms=120000) —— 阻塞到完成或超时（不要连发 status 轮询，会撞重复调用检测）；'
        + '也可 op="collect"（随时看，未完成时也能看到 stage/日志尾），op="kill" 中止。');
    parts.push('提醒：下次这类长活直接 as_job=true（预期 >100 s 都建议），就不会占用调用窗口。');
    return { text: parts.join(NLx), envelope: env };
}
/**
 * v0.9.3（D1）：客户端等待窗口到点 / 宿主取消时的兜底 —— 先问一句服务端状态：
 *   已结束 → 直接 collect 把结果拿回来（窗口与完成是赛跑，可能刚好赶上）；
 *   还在跑 → 回 promoted 回执（runId 是客户端生成的，句柄一定有效）。
 */
async function promotedFromTimeout(port, runId, err) {
    const msg = String((err && err.message) || err);
    const job = await fetchJobStatus(port, runId);
    if (job && job.status && job.status !== 'running' && job.status !== 'unknown') {
        try {
            const cr = await backendJson(port, '/job', 15000, {
                method: 'POST', headers: { 'content-type': 'application/json' },
                body: JSON.stringify({ op: 'collect', id: runId, holder: HOLDER }),
            });
            const cj = (cr && cr.job) || null;
            if (cj && cj.status && cj.status !== 'running') {
                return headlessReceipt(cj, { mode: 'sync', jobId: null, runId: runId,
                    notes: ['客户端窗口到点时任务其实已经跑完 → 已直接取回结果（' + msg + '）'] });
            }
        }
        catch (e2) { /* 取不回来就走 promoted */ }
    }
    return promotedReceipt(job, runId, '客户端等待窗口到点或宿主取消：' + msg);
}
/** 取某个 id（run-… / job-…）的状态快照（拿不到就返回 null，绝不抛） */
async function fetchJobStatus(port, id) {
    try {
        const r = await backendJson(port, '/job', 8000, {
            method: 'POST', headers: { 'content-type': 'application/json' },
            body: JSON.stringify({ op: 'status', id: id, holder: HOLDER }),
        });
        return (r && r.job) || null;
    }
    catch (e) {
        return null;
    }
}
const ANY_SCHEMA = { type: 'json' };
const HINT_KERNEL = '持久内核：K.x = 1 这次写，下次调用还能读到；预置 bpy / math / mathutils / Vector。';
export function apply(ctx, config) {
    const port = config.port;
    void (async () => { if (config.autoStart && !paused)
        await ensureBackend(port); })();
    // 常驻看护：每 15s 探活一次，掉线自动拉起（宿主重启 / 进程被杀 / 端口被回收都能自愈）。
    // 显式 op=stop 会置 paused，看护尊重它，不会跟用户对着干。
    ctx.effect(() => {
        const wd = setInterval(() => {
            void (async () => {
                try {
                    if (paused)
                        return;
                    if (await probe(port)) {
                        // 租约心跳：只在"本来就持有"时续期，空闲时不抢占别的会话
                        try {
                            await backendPost(port, '/lease', { renewOnly: true }, 5000);
                        }
                        catch (e) { /* 后端可能刚挂，下一轮再试 */ }
                        return;
                    }
                    startBackend(port);
                }
                catch (e) { /* 看护失败静默，下一轮再试 */ }
            })();
        }, 15000);
        return () => clearInterval(wd);
    }, '@dsh-external/dsh-blender-plugin: watchdog');
    ctx.effect(() => () => {
        if (child) {
            try {
                child.kill();
            }
            catch (e) { }
            child = null;
        }
    }, '@dsh-external/dsh-blender-plugin: backend');
    // ---------- A. AI 实时交互原语 ----------
    ctx.effect(() => ctx.tools.register(vTool({
        name: 'blender_rt_see',
        description: '实时看 Blender 视口：直接向 addon socket 取一帧离屏渲染并内联返回（约 55ms，比 CLI/MCP 通道快 20 倍）。用于「改一步、看一眼」的闭环。'
            + '**v0.9.1（93-D2）自诊断**：返回体带 `coverage_estimate`（画面里非底色像素占比）与 `scene_bbox`（可见 mesh 的世界 bbox）；'
            + '自定义视角画面几乎全空时给 `warning{code:"frame_looks_empty", suggest:{from,look_at,lens}}` —— 照 suggest 再出一次即可，不必自己去按 Home。' + HINT_KERNEL,
        parameters: {
            max_size: { type: 'integer', description: '最长边像素，默认 560（420 更快，900 更清晰）' },
            full: { type: 'boolean', description: '整窗口/区域截图（screenshot_area 路径）：看 Blender UI 或其他编辑器时用；默认 false = 3D 视口离屏帧' },
            area: { type: 'integer', description: 'full=true 时的区域序号，默认 0（第 1 个区域）' },
            from: { type: 'string', description: '【自定义视角】相机位置 "x,y,z"（世界坐标）。给 from + look_at 就走自定义视角出图：套用自建矩阵离屏绘制，完全不动物体、不动用户视口 —— 想"从另一侧看看"或做多角度校验时用它（约 100ms）' },
            look_at: { type: 'string', description: '【自定义视角】看向的点 "x,y,z"' },
            lens: { type: 'number', description: '【自定义视角】焦距 mm，默认 50' },
            ortho: { type: 'boolean', description: '【自定义视角】正交投影（配合 ortho_scale）' },
            ortho_scale: { type: 'number', description: '【自定义视角】正交尺度（贴长边），默认 10' },
            view_size: { type: 'string', description: '【自定义视角】分辨率 "宽x高"，如 "1280x720"；默认按 max_size 出 16:9' },
            shading: { type: 'string', description: '【自定义视角】临时切换着色 WIREFRAME/SOLID/MATERIAL/RENDERED（出图后还原）' },
            overlays: { type: 'boolean', description: '【自定义视角】临时开关覆盖物（网格/坐标轴/gizmo），出图后还原' },
            view_mode: { type: 'string', description: '【自定义视角】viewport（默认，零场景改动，就是视口看到的样子）/ render（临时相机 + Workbench 快渲）' },
        },
        output: { schema: ANY_SCHEMA, render: renderOne },
        isConcurrencySafe: () => true,
        timeoutMs: T.see,
        async execute(args) {
            if (!(await ensureBackend(port)))
                return { text: '后端不可用（127.0.0.1:' + String(port) + '）。' + BACKEND_HINT };
            const size = Math.max(120, Math.min(1600, Number((args && args.max_size) || 560)));
            const full = !!(args && args.full);
            const area = Number((args && args.area) || 0);
            const frm = numTriple(args && args.from);
            const look = numTriple(args && args.look_at);
            if (frm || look) {
                const spec = {
                    from: frm || [7, -7, 5],
                    look_at: look || [0, 0, 1],
                    lens: args.lens === undefined ? undefined : Number(args.lens),
                    ortho: args.ortho === undefined ? undefined : !!args.ortho,
                    ortho_scale: args.ortho_scale === undefined ? undefined : Number(args.ortho_scale),
                    shading: args.shading || undefined,
                    overlays: args.overlays === undefined ? undefined : !!args.overlays,
                    mode: args.view_mode || undefined,
                };
                const vs = String((args && args.view_size) || '');
                const mm = vs.match(/^(\d+)\s*[x×]\s*(\d+)$/);
                if (mm) {
                    spec.width = Number(mm[1]);
                    spec.height = Number(mm[2]);
                }
                else {
                    spec.width = Math.round(size * 16 / 9);
                    spec.height = size;
                }
                const fv = await fetchView(port, spec);
                const img2 = await toAttachment(ctx, fv.png, 'view-' + String(Date.now()) + '.png');
                const mt = fv.meta || {};
                const out2 = {
                    text: '自定义视角 ' + String(mt.mode || spec.mode || 'viewport') + ' · ' + String(spec.width) + 'x' + String(spec.height)
                        + ' · ' + String(fv.ms) + 'ms · ' + String(fv.png.length) + 'B · hash ' + fv.hash
                        + ' · from ' + JSON.stringify(spec.from) + ' → look_at ' + JSON.stringify(spec.look_at)
                        + (mt.fallback_from ? (' · (viewport 不可用已降级 render：' + String(mt.fallback_from) + ')') : '')
                        + (mt.coverage_estimate === undefined || mt.coverage_estimate === null ? '' : (' · 覆盖 ' + (Number(mt.coverage_estimate) * 100).toFixed(2) + '%'))
                        + ' · 场景与用户视口均未改动',
                };
                // v0.9.1（93-D2）：自定义视角瞄空时直接告警 + 给出该怎么瞄（照 suggest 再出一次即可）
                if (mt.warning && mt.warning.code === 'frame_looks_empty') {
                    const w = mt.warning;
                    out2.text += String.fromCharCode(10) + '⚠ frame_looks_empty：' + String(w.why || '画面里几乎只剩底色')
                        + (w.scene_bbox ? (String.fromCharCode(10) + '  场景 bbox：center=' + JSON.stringify(w.scene_bbox.center) + ' span=' + String(w.scene_bbox.span) + ' objects=' + String(w.scene_bbox.objects)) : '')
                        + (w.suggest ? (String.fromCharCode(10) + '  建议照这组再出一次：from=' + JSON.stringify(w.suggest.from) + ' look_at=' + JSON.stringify(w.suggest.look_at)) : String.fromCharCode(10) + '  ' + String(w.hint || ''));
                }
                if (img2.ref)
                    out2.image = img2.ref;
                else
                    out2.text += String.fromCharCode(10) + '⚠ 图片未回传：' + String(img2.why || '未知原因');
                return out2;
            }
            const f = await fetchFrame(port, size, full, area);
            const img = await toAttachment(ctx, f.png, (full ? 'area-' : 'viewport-') + String(Date.now()) + '.png');
            const out = { text: (full ? '区域截图（screenshot_area）· ' : '视口帧 ' + String(size) + 'px · ') + String(f.ms) + 'ms · ' + String(f.png.length) + 'B · hash ' + f.hash };
            if (img.ref)
                out.image = img.ref;
            else
                out.text += String.fromCharCode(10) + '⚠ 图片未回传：' + String(img.why || '未知原因');
            return out;
        },
    })), '@dsh-external/dsh-blender-plugin: rt-see');
    ctx.effect(() => ctx.tools.register(vTool({
        name: 'blender_rt_do',
        description: '实时驱动 Blender：在 Blender 的 Python 里执行一段代码（或直接跑一个 .py 文件），可选立刻回一帧视口（整步约 105ms/次，可在同一轮里连续做几十步）。'
            + HINT_KERNEL
            + ' v0.7.0 起：① **异常也会回传 partial stdout / stderr / traceback**（不再只给一句 error，诊断信息不会丢）；'
            + ' ② file 参数可直接执行工作区脚本（等价 K.run(path)，支持 WSL / Windows / 相对 .blend 三种路径）；'
            + ' ③ 内置路径辅助 K.win_path / K.wsl_path / K.blend_path / K.out_dir（省掉手拼 UNC 与 chr(92)）；'
            + ' ④ 返回的 ms 即**主线程占用**，>1 s 会提示改走 blender_rt_headless / blender_rt_worker。',
        parameters: {
            code: { type: 'string', description: 'Python 代码（在 Blender 主线程执行；预置 bpy/math/mathutils/Vector 与持久内核 K）。与 file 二选一' },
            file: { type: 'string', description: '要执行的 .py 文件路径（WSL / Windows / 相对当前 .blend 均可）—— 长脚本用这个，不必塞进入参' },
            see: { type: 'boolean', description: '是否同时回一帧视口（默认 true）' },
            max_size: { type: 'integer', description: '回帧最长边像素，默认 560' },
        },
        output: { schema: ANY_SCHEMA, render: renderOne },
        isConcurrencySafe: () => false,
        timeoutMs: T.act,
        async execute(args) {
            if (!(await ensureBackend(port)))
                return { text: '后端不可用（127.0.0.1:' + String(port) + '）。' + BACKEND_HINT };
            const code = String((args && args.code) || '');
            const file = (args && args.file) ? String(args.file) : null;
            if (!code && !file)
                return { text: '需要 code 或 file 之一' };
            const body = { code: code };
            if (file)
                body.file = file;
            const r = await backendPost(port, '/act', body, 180000);
            const lt = leasedText(r);
            if (lt)
                return { text: lt };
            const parts = [];
            parts.push(r && r.ok ? 'ACT ok · ' + String(r.ms) + 'ms' : 'ACT 失败 · ' + String((r && (r.error || r.raw)) || 'unknown'));
            if (r && Number(r.mainThreadMs) > 1000)
                parts.push('⚠️ 本次占用 Blender 主线程约 ' + String(r.mainThreadMs) + 'ms —— 超过 ~1s 的重活建议改走 blender_rt_headless / blender_rt_worker（不卡 GUI）');
            if (r && r.stderr && String(r.stderr).trim())
                parts.push('stderr: ' + String(r.stderr).trim().slice(0, 1200));
            if (r && r.traceback)
                parts.push("--- traceback ---" + String.fromCharCode(10) + String(r.traceback).slice(0, 2000));
            if (r && r.stdout)
                parts.push('stdout: ' + String(r.stdout).trim().slice(0, 3000));
            let image = undefined;
            const see = !(args && args.see === false);
            if (see) {
                try {
                    const size = Math.max(120, Math.min(1600, Number((args && args.max_size) || 560)));
                    const f = await fetchFrame(port, size);
                    const att2 = await toAttachment(ctx, f.png, 'viewport-' + String(Date.now()) + '.png');
                    image = att2.ref;
                    if (!image)
                        parts.push('⚠ 图片未回传：' + String(att2.why || '未知原因'));
                    parts.push('SEE ' + String(size) + 'px · ' + String(f.ms) + 'ms · hash ' + f.hash);
                }
                catch (e) {
                    parts.push('SEE 失败: ' + String((e && e.message) || e));
                }
            }
            const out = { text: parts.join('\n') };
            if (image)
                out.image = image;
            return out;
        },
    })), '@dsh-external/dsh-blender-plugin: rt-do');
    ctx.effect(() => ctx.tools.register(vTool({
        name: 'blender_rt_watch',
        description: '实时观察一段时间窗：可选先跑一段 Python（例如起 bpy.app.timers 驱动 / 播放动画 / 让物体动起来），然后在 seconds 秒内按 fps 连续采样视口帧，内联返回均匀抽取的最多 6 帧 + 逐帧 hash（hash 相同=画面没变）。用于判断「它到底动没动、动得对不对」。',
        parameters: {
            code: { type: 'string', description: '可选：观察前先执行的 Python' },
            seconds: { type: 'number', description: '观察时长，默认 2 秒（0.2-20）' },
            fps: { type: 'number', description: '采样率，默认 4（0.5-8）；注意每帧占用 Blender 主线程 ~55ms' },
            max_size: { type: 'integer', description: '帧最长边像素，默认 420' },
        },
        output: { schema: ANY_SCHEMA, render: renderOne },
        isConcurrencySafe: () => false,
        timeoutMs: T.watch,
        async execute(args) {
            if (!(await ensureBackend(port)))
                return { text: '后端不可用（127.0.0.1:' + String(port) + '）。' + BACKEND_HINT };
            const seconds = Math.max(0.2, Math.min(20, Number((args && args.seconds) || 2)));
            const fps = Math.max(0.5, Math.min(8, Number((args && args.fps) || 4)));
            const size = Math.max(120, Math.min(1600, Number((args && args.max_size) || 420)));
            const parts = [];
            if (args && args.code) {
                const r = await backendPost(port, '/act', { code: String(args.code) }, 180000);
                const lt = leasedText(r);
                parts.push(lt ? lt : (r && r.ok ? 'ACT ok · ' + String(r.ms) + 'ms' + (r.stdout ? ' · stdout: ' + String(r.stdout).trim().slice(0, 600) : '') : 'ACT 失败 · ' + String((r && (r.error || r.raw)) || '')));
            }
            const total = Math.max(1, Math.min(32, Math.round(seconds * fps)));
            const interval = (seconds * 1000) / total;
            const shots = [];
            const t0 = Date.now();
            for (let i = 0; i < total; i++) {
                try {
                    const f = await fetchFrame(port, size);
                    shots.push({ t: Date.now() - t0, ms: f.ms, hash: f.hash, png: f.png });
                }
                catch (e) {
                    shots.push({ t: Date.now() - t0, ms: -1, hash: 'ERR', png: null });
                }
                const wait = (i + 1) * interval - (Date.now() - t0);
                if (wait > 1)
                    await sleep(wait);
            }
            const hashes = shots.map((s) => s.hash);
            const distinct = Array.from(new Set(hashes)).length;
            const avgMs = Math.round(shots.reduce((a, s) => a + Math.max(0, s.ms), 0) / Math.max(1, shots.length));
            parts.push('WATCH ' + String(seconds) + 's @' + String(fps) + 'fps → ' + String(shots.length) + ' 帧 · 平均 ' + String(avgMs) + 'ms/帧 · 不同画面 ' + String(distinct) + '/' + String(hashes.length));
            parts.push('timeline: ' + shots.map((s, i) => String(i) + '@' + String(s.t) + 'ms:' + s.hash).join(' '));
            const picks = [];
            const maxImages = 6;
            if (shots.length <= maxImages) {
                for (const s of shots)
                    if (s.png)
                        picks.push(s);
            }
            else {
                for (let k = 0; k < maxImages; k++) {
                    const idx = Math.round((k * (shots.length - 1)) / (maxImages - 1));
                    if (shots[idx] && shots[idx].png)
                        picks.push(shots[idx]);
                }
            }
            const images = [];
            let whyNot;
            for (let k = 0; k < picks.length; k++) {
                const att = await toAttachment(ctx, picks[k].png, 'watch-' + String(k) + '-' + picks[k].hash + '.png');
                if (att.ref)
                    images.push(att.ref);
                else if (!whyNot)
                    whyNot = att.why;
            }
            parts.push('附帧：' + String(images.length) + ' 张（均匀抽取）' + (whyNot ? (' · ⚠ 有帧未回传：' + whyNot) : ''));
            const out = { text: parts.join('\n') };
            if (images.length)
                out.images = images;
            return out;
        },
    })), '@dsh-external/dsh-blender-plugin: rt-watch');
    ctx.effect(() => ctx.tools.register(vTool({
        name: 'blender_rt_cmd',
        description: '直连调用 Blender addon 的任意命令（30 个名字：14 常驻 + 15 集成门控 + ping），含 MCP 层不暴露的 get_world_state_snapshot / drain_human_activity / get_telemetry_consent / set_telemetry_consent / get_addon_info，以及资产类命令（PolyHaven / Sketchfab / Poly Pizza / Hyper3D / Hunyuan3D）。参数必须匹配 addon 真实签名：get_scene_info 无参、get_object_info 用 name（不是 object_name）、不要传 MCP 才有的 user_prompt。先用 blender_rt_commands 看清单与可用性。',
        parameters: {
            name: { type: 'string', required: true, description: 'addon 命令名，如 get_world_state_snapshot、search_polyhaven_assets' },
            params: { type: 'json', description: '参数对象（可选），如 {"asset_type":"hdris"}' },
            timeout_ms: { type: 'integer', description: '超时毫秒，默认 120000（下载/生成类可调大）' },
        },
        output: { schema: ANY_SCHEMA, render: renderOne },
        isConcurrencySafe: () => false,
        timeoutMs: T.cmd,
        async execute(args) {
            if (!(await ensureBackend(port)))
                return { text: '后端不可用（127.0.0.1:' + String(port) + '）。' + BACKEND_HINT };
            const name = String((args && args.name) || '');
            if (!name)
                return { text: 'blender_rt_cmd 需要 name 参数' };
            const r = await backendPost(port, '/cmd', { name: name, params: (args && args.params) || {}, timeoutMs: Number((args && args.timeout_ms) || 120000) }, 300000);
            const lt = leasedText(r);
            if (lt)
                return { text: lt };
            if (!r || r.ok !== true)
                return { text: 'CMD ' + name + ' 失败 · ' + String((r && (r.error || r.raw)) || 'unknown') };
            const payload = JSON.stringify(r.result);
            const head = 'CMD ' + name + ' ok · ' + String(r.ms) + 'ms · ' + String(payload.length) + ' chars';
            return { text: head + '\n' + (payload.length > 3000 ? payload.slice(0, 3000) + '\n…(已截断)' : payload) };
        },
    })), '@dsh-external/dsh-blender-plugin: rt-cmd');
    ctx.effect(() => ctx.tools.register(vTool({
        name: 'blender_rt_commands',
        description: '列出直连通道当前可用的 addon 命令（14 常驻 + 15 集成门控 + ping）与 5 个集成的真实状态（开关是否打开、是否缺 API key）。调 blender_rt_cmd 之前先用它。',
        parameters: {},
        output: { schema: ANY_SCHEMA, render: renderOne },
        isConcurrencySafe: () => true,
        timeoutMs: T.commands,
        async execute() {
            if (!(await ensureBackend(port)))
                return { text: '后端不可用（127.0.0.1:' + String(port) + '）。' + BACKEND_HINT };
            const r = await backendJson(port, '/commands', 120000);
            if (!r || r.ok !== true)
                return { text: 'COMMANDS 失败 · ' + String((r && (r.error || r.raw)) || 'unknown') };
            const lines = [];
            lines.push('场景 ' + String(r.scene) + (r.file ? ' · 文件 ' + String(r.file) : ' · 未保存文件'));
            lines.push('可用命令 ' + String(r.total) + ' 条；被集成开关挡住 ' + String((r.disabled || []).length) + ' 条');
            lines.push('集成：' + Object.keys(r.integrations || {}).map((k) => k + '=' + (r.integrations[k].enabled ? 'on' : 'off')).join(' · '));
            for (const k of Object.keys(r.integrations || {})) {
                const it = r.integrations[k];
                if (!it.enabled && it.message)
                    lines.push('  [' + k + '] ' + it.message);
            }
            if ((r.disabled || []).length)
                lines.push('被挡住：' + r.disabled.join(', '));
            lines.push('可用：' + (r.available || []).join(', '));
            return { text: lines.join('\n') };
        },
    })), '@dsh-external/dsh-blender-plugin: rt-commands');
    ctx.effect(() => ctx.tools.register(vTool({
        name: 'blender_rt_loop',
        description: '【AI 建模内环】在 Blender 侧跑高频迭代（bpy.app.timers，主线程安全，迭代/时间双上限 + 急停）：模型只写目标与验收，机器跑几千次迭代。**什么时候该用它（v0.8.10，量化判定）**：要在参数空间里搜 ≥20 次、且每次都要重新出图或重新量测 —— 把"改一步看一眼"的往返交给 Blender 侧；只搜 ≤5 次、或判据不需要每次渲染 → 用 rt_do 自己循环更省事。spec={setup, step, measure, iterations, budget_ms, interval, measure_every, minimize, top_k, group_key, redraw_every}；setup 只跑一次，step/measure 共享命名空间 ns（预置 bpy/K/math/random/np/i/frac/penalize/anneal/record），measure 必须给 ns["score"]，参数写 ns["params"]，可选 ns["metrics"]/ns["violations"]。辅助：penalize(errors, violations, weights, lam) 做多目标+罚项；anneal(v0,v1,frac) 退火步长（step 里读 ns["i"]/ns["frac"]）；record(...) 手动登记候选；top_k 保留候选表，group_key（如 "obj"）按对象分组各留最优（跨对象批量）。op=help 出契约速查；op=board 取候选表；op=export 把 best 导出成可复用脚本（内嵌 setup 源码，可直接再跑）。⚠️ 内环只优化你写的目标函数：收敛后必须换**另一条**计算通路复核 + blender_rt_see 视觉确认（防 Goodhart）。',
        parameters: {
            op: { type: 'string', required: true, description: 'start | status | stop | board | export | help | bench' },
            spec: { type: 'json', description: 'op=start 的规格：{setup, step, measure, iterations, budget_ms, interval, measure_every, minimize, top_k, group_key, redraw_every}' },
            history: { type: 'integer', description: 'op=status 返回的指标尾迹长度，默认 8' },
            board: { type: 'integer', description: 'op=status 时同时返回前 N 个候选（默认 0=不返回）' },
            limit: { type: 'integer', description: 'op=board 的候选条数，默认 10' },
            groups: { type: 'boolean', description: 'op=board 是否包含分组候选（默认 true）' },
            path: { type: 'string', description: 'op=export 写出脚本的路径（宿主可见路径，如 /tmp/xxx.py 或 D:\\out\\xxx.py），省略=只回文本' },
            top: { type: 'integer', description: 'op=export 的变体数量（配 include_variants）' },
            include_variants: { type: 'boolean', description: 'op=export 是否把候选表一起导出' },
            note: { type: 'string', description: 'op=export 的备注（写进脚本头部）' },
            iterations: { type: 'integer', description: 'op=bench 的迭代次数' },
        },
        output: { schema: ANY_SCHEMA, render: renderOne },
        isConcurrencySafe: () => false,
        timeoutMs: T.loop,
        async execute(args) {
            if (!(await ensureBackend(port)))
                return { text: '后端不可用（127.0.0.1:' + String(port) + '）。' + BACKEND_HINT };
            const op = String((args && args.op) || 'status');
            const payload = {
                op: op,
                spec: (args && args.spec) || {},
                history: (args && args.history) || 8,
                board: (args && args.board) || 0,
                limit: (args && args.limit) || 10,
                groups: !(args && args.groups === false),
                path: args && args.path,
                top: (args && args.top) || 1,
                include_variants: !!(args && args.include_variants),
                note: (args && args.note) || '',
                iterations: (args && args.iterations) || 5000,
            };
            const r = await backendPost(port, '/loop', payload, 300000);
            const lt = leasedText(r);
            if (lt)
                return { text: lt };
            if (!r || r.ok !== true)
                return { text: 'LOOP ' + op + ' 失败 · ' + String((r && (r.error || r.raw)) || 'unknown') };
            return { text: 'LOOP ' + op + ' ' + JSON.stringify(r.result) };
        },
    })), '@dsh-external/dsh-blender-plugin: rt-loop');
    ctx.effect(() => ctx.tools.register(vTool({
        name: 'blender_rt_perf',
        description: '【渲染性能】Cycles CPU 瓶颈诊断与优化预设。op=analyze 差分实测「每轮同步 / 每采样 GPU 成本」（占主线程十几秒）；op=apply 应用预设（persistent_data / OptiX 降噪 / denoising_use_gpu / auto_tile off / 采样上限）；op=revert 还原 apply 之前；op=status 看当前设置与已存快照；op=help 契约。实测背景：2318 对象场景每轮 CPU 侧同步 ≈4.2 s、GPU 单采样 ≈0.2 s（1080p）、首次 kernel JIT ≈15 s；persistent_data 让重复渲染 14.6 s → 0.79 s。',
        parameters: {
            op: { type: 'string', required: true, description: 'status | analyze | apply | revert | help' },
            args: { type: 'json', description: 'analyze{pct=25,low=1,high=4}；apply{samples=1024,persistent=true,denoiser="OPTIX",denoise_gpu=true,auto_tile=false}' },
        },
        output: { schema: ANY_SCHEMA, render: renderOne },
        isConcurrencySafe: () => false,
        timeoutMs: T.perf,
        async execute(args) {
            if (!(await ensureBackend(port)))
                return { text: '后端不可用（127.0.0.1:' + String(port) + '）。' + BACKEND_HINT };
            const op = String((args && args.op) || 'status');
            // ⚠ 无参时必须给 undefined（给 {} 会让 python 侧多收一个位置参数：实测 "_dsh_perf_status_cycles() takes 0 positional arguments"）
            const pargs = jsonPayload(args && args.args);
            const r = await backendPost(port, '/perf', { op: op, args: Object.keys(pargs).length ? pargs : undefined }, 600000);
            const lt = leasedText(r);
            if (lt)
                return { text: lt };
            if (!r || r.ok !== true)
                return { text: 'PERF ' + op + ' 失败 · ' + String((r && (r.error || r.raw)) || 'unknown') };
            return { text: 'PERF ' + op + ' ' + JSON.stringify(r.result) };
        },
    })), '@dsh-external/dsh-blender-plugin: rt-perf');
    ctx.effect(() => ctx.tools.register(vTool({
        name: 'blender_rt_opt',
        description: '【对象精简】降 Cycles 每轮场景同步成本（实测 ≈1.4 ms/对象）。op=analyze 列出可安全合并的分组与预计节省；op=join 执行合并（**默认 dry_run=true 只报告**；dry_run=false 才真合并，强烈建议同时给 save_before=<.blend 绝对路径> 先存回退点）。合并规则：同集合 / 同材质 / 同父级 / 无修改器 / 无动画 / 无形态键 / 无自定义属性 / 无实例 / 非库链接。合并后**几何零损失**（面数与顶点数不变），合并对象名为 `<前缀>_MERGED_<材质>`。',
        parameters: {
            op: { type: 'string', required: true, description: 'analyze | join | help' },
            args: { type: 'json', description: 'join{dry_run=true, save_before="D:/.../xxx_before_join.blend"}' },
        },
        output: { schema: ANY_SCHEMA, render: renderOne },
        isConcurrencySafe: () => false,
        timeoutMs: T.opt,
        async execute(args) {
            if (!(await ensureBackend(port)))
                return { text: '后端不可用（127.0.0.1:' + String(port) + '）。' + BACKEND_HINT };
            const op = String((args && args.op) || 'analyze');
            const oargs = jsonPayload(args && args.args);
            const r = await backendPost(port, '/opt', { op: op === 'join' ? 'opt_join' : 'opt_analyze', args: Object.keys(oargs).length ? oargs : undefined }, 600000);
            const lt = leasedText(r);
            if (lt)
                return { text: lt };
            if (!r || r.ok !== true)
                return { text: 'OPT ' + op + ' 失败 · ' + String((r && (r.error || r.raw)) || 'unknown') };
            return { text: 'OPT ' + op + ' ' + JSON.stringify(r.result) };
        },
    })), '@dsh-external/dsh-blender-plugin: rt-opt');
    ctx.effect(() => ctx.tools.register(vTool({
        name: 'blender_rt_headless',
        description: '【无头 Blender · **第一路径**（v0.9.3）】独立进程跑脚本（blender.exe -b）：不占 GUI 通道、不动你正看着的场景。'
            + '批量几何 / 数据校验 / 渲染 / 任何不需要「人在回路看视口」的活都先走这里；只有「改一步看一眼」才回 GUI 工具（rt_do/rt_see）。'
            + '脚本里 print("HEADLESS {...}") 会被解析成 result（**必须是单行 JSON**：json.dumps(obj, separators=(",",":"))）。'
            + '默认 --factory-startup（干净、快、不会去抢 9876 端口）；要用用户的启动文件与偏好时设 factory_startup=false + use_user_config=true。'
            + '引擎语义（v0.8.0，默认 **EEVEE + 光追**）：默认注入引擎前导，并回传 engine/gpu 字段；可选 engine="cycles"（OptiX）/ engine="keep" / engine="none"（跳过前导）。'
            + '**v0.9.3 关键变化（外部反馈 2026-09-24 的 P0）**：'
            + '① **超时不再吞结果**：客户端等待窗口（默认 100 s，DSH_HEADLESS_WAIT_MS 可调）到点或宿主取消时，'
            + '立刻回 `{kind:"promoted", jobId:"run-…"}`（bash 的 promoted 语义）—— 服务端子进程照跑，用 `blender_rt_job(op="collect"|"wait", id=…)` 收结果；'
            + '② **预期 >100 s 的活直接 `as_job=true`**（可配 `wait_s`：后台化后最多再等几秒，没完就回 jobId）—— 别让外层 run_code 的 deadline 决定结果去向；'
            + '③ **回执结构化**：第 1 个 text block 是**单行 JSON 信封**（可直接 JSON.parse：status/runId/jobId/resultJson/resultPath/resultTruncated/stdoutTail/artifacts/inputFile/stage/shots/pathWarnings），第 2 个是人读摘要 —— 不必再手写正则解析 "result: {...}"；'
            + '④ **末行非 JSON 不再顶掉输出**：解析失败记在 resultParseError，stdoutTail/logs 保留原文；>4KB 的结果自动落盘并给 resultPath（也可显式 `out_json=`）；'
            + '⑤ **路径**：outdir/out_json/file 都收 WSL 路径（/home/… 内部映射成 \\\\wsl.localhost\\<distro>\\…），回执里给 Windows + WSL 两种真实路径；脚本把 POSIX 路径交给 Windows API 会被体检出来（pathWarnings）；'
            + '⑥ **可观测**：三处 spawn 已注入 PYTHONUNBUFFERED=1，日志运行期就有增量；脚本里 `dsh_stage("building")`（或 K.progress）会打印 DSH_STAGE 心跳，`blender_rt_job(op="status").stage/idleMs` 直接可读；'
            + '⑦ **多视角一体化（F1/F2）**：`shots=[{name, from, look_at, lens, res, samples}]` 一次出 N 张（走内置渲染 harness：自动三点光/渲染锁/逐张 md5/实测设备），回执 res.shots 每行带 coverage_estimate，主体占画面 <5% 出 warning。'
            + '返回：result / resultJson / status / gpu / logs（全量日志路径）/ lastException / traceback / artifacts / shots / inputFile。'
            + '**注意**：长任务别用「连发 status 轮询」—— 用 op=wait（阻塞到完成或超时）。',
        parameters: {
            script: { type: 'string', description: 'Python 源码（默认已注入持久内核 K；预置 bpy/math/mathutils/Vector）。不给脚本则只起 Blender（可用于 --version 类探测）' },
            file: { type: 'string', description: '要打开的 .blend（Windows 路径或 WSL 路径都可，自动转换）；回执会带 inputFile{size,mtime,md5} 便于确认「探的是哪一版」' },
            outdir: { type: 'string', description: '产物目录（Windows 路径 D:\\work\\out 或 **WSL 路径 /home/… 都行**）；也是默认工作目录，跑完列出其中新文件，并把该路径追加到脚本的 sys.argv' },
            args: { type: 'string', description: '额外命令行参数（空格分隔），追加在 -- 之后，脚本里从 sys.argv 读（也可用 K.args 读同一个数组）' },
            timeout_ms: { type: 'integer', description: '超时毫秒，默认 180000（3 min），上限 1800000（30 min）；超时 SIGKILL 掉整个进程。注意：它只决定「服务端子进程跑多久」，不再决定「客户端拿不拿得到结果」（见 as_job / promoted）' },
            as_job: { type: 'boolean', description: '【v0.9.3】true = 立刻后台化成作业（返回 jobId，不受调用窗口限制）；配合 wait_s 可先等一小会儿。预期 >100 s 的渲染/批量活请直接用它' },
            wait_s: { type: 'number', description: '【v0.9.3】as_job 之后最多再等几秒（上限受 DSH_HEADLESS_WAIT_MS 约束）；到点没完就回 {kind:"promoted", jobId}。不传 = 立刻返回 jobId' },
            shots: { type: 'json', description: '【v0.9.3 · F1】多视角渲染一体化：数组 [{name, from:[x,y,z], look_at:[x,y,z], lens, res:[w,h]或数字, samples, ortho, ortho_scale, margin}]，或对象 {views:[…], res, samples, tag, warmup, margin}。走 qc_render 的 harness（自动三点光 + 渲染锁 + 逐张 md5/设备回读），回执 res.shots=[{name,path,ms,bytes,md5,coverage_estimate,warning?}]；主体占画面 <5% 时给 warning{code:"subject_too_small"}。可与 script 同时给（先跑脚本再出图）' },
            factory_startup: { type: 'boolean', description: '默认 true = --factory-startup；false 用用户启动文件与插件（注意：其 startup 里的本插件会尝试占 9876 端口，通常无害但有报错噪音）' },
            bootstrap: { type: 'boolean', description: '默认 true = 注入持久内核 K（与 blender_rt_do 一致）；false 时脚本原样跑' },
            preload: { type: 'string', description: '预载 runtime 里的 python 模块（逗号分隔，如 "view,perf,contract,planner" 或 "qc,qc_render,audit"）：源码拼到脚本开头，之后可用 K.dsh_view_api / K.dsh_qc_api / K.dsh_audit_api 等。**audit_*/qc_*/deliver_*/montage/motion_* 都能这样在 headless 直调**（不需要 addon/Connect）' },
            engine: { type: 'string', description: '渲染引擎：eevee（默认 = EEVEE + 光追，纯 GPU、不依赖设备偏好）/ cycles（OptiX 设备前导）/ keep（保持现状）/ none（完全跳过引擎前导与 GPU 探测 —— 纯 numpy/图像类任务省 1.0-1.5 s）' },
            gpu: { type: 'string', description: '（仅 cycles 路径的设备语义）auto / true（必须有 GPU，否则 ok=false）/ false' },
            use_user_config: { type: 'boolean', description: '透传 BLENDER_USER_CONFIG / BLENDER_USER_SCRIPTS 给无头进程（默认 false）—— 想让无头进程继承你的偏好/插件时打开（通常配合 factory_startup=false）' },
            include_noise: { type: 'boolean', description: '产物清单是否包含噪音文件（__pycache__ / *.pyc / *.blend1|2 / tmp*）；默认 false = 过滤掉' },
            script_file: { type: 'string', description: '直接跑一个 .py 文件（Windows D:\\… 或 WSL /home/… 都行）—— 不必再「写盘→读回→当字符串传」。file= 的语义是 .blend；若把 .py 传给 file= 会自动识别为脚本并提示' },
            env: { type: 'json', description: '给子进程的额外环境变量 {KEY:"VALUE"}。插件已自动注入 DSH_RUN_ID / DSH_OUTDIR / DSH_ARGS / DSH_SESSION / DSH_PLUGIN_VERSION，脚本里可读 K.args / K.run_id / K.env' },
            out_json: { type: 'string', description: '把脚本 print 的 HEADLESS 结构化结果落盘到该路径（**WSL 路径也收**；结果 >4KB 时插件也会自动落到 results/，路径在 resultPath/resultPathWsl）。分析脚本不必再从 stdout 里 indexOf 切片' },
            workdir: { type: 'string', description: '脚本内 chdir + sys.path 首位（Windows/WSL 路径都收，脚本内过 K.win_path 转换）' },
        },
        output: { schema: ANY_SCHEMA, render: renderStructured },
        isConcurrencySafe: () => false,
        timeoutMs: T.headless,
        async execute(args) {
            if (!(await ensureBackend(port)))
                return { text: '后端不可用（127.0.0.1:' + String(port) + '）；无头通道由后端进程代管，先 blender_viewport op=start' };
            const runId = newRunId();
            const asJob = !!(args && args.as_job);
            const waitS = (args && args.wait_s !== undefined && args.wait_s !== null && args.wait_s !== '') ? Number(args.wait_s) : null;
            const body = {
                script: String((args && args.script) || ''),
                file: (args && args.file) || undefined,
                outdir: (args && args.outdir) || undefined,
                timeoutMs: (args && args.timeout_ms) ? Number(args.timeout_ms) : undefined,
                factoryStartup: !(args && args.factory_startup === false),
                bootstrap: !(args && args.bootstrap === false),
                preload: (args && args.preload) || undefined,
                gpu: (args && args.gpu) || undefined,
                engine: (args && args.engine) || undefined,
                useUserConfig: !!(args && args.use_user_config),
                includeNoise: !!(args && args.include_noise),
                expect: (args && args.expect && typeof args.expect === 'object') ? args.expect : undefined,
                workdir: (args && args.workdir) || undefined,
                scriptFile: (args && args.script_file) || undefined,
                env: (args && args.env && typeof args.env === 'object') ? args.env : undefined,
                outJson: (args && args.out_json) || undefined,
                shots: (args && args.shots) || undefined,
                runId: runId, // v0.9.3（D1）：客户端生成 → 窗口到点时立刻有可用句柄
            };
            if (args && args.args)
                body.args = splitArgs(args.args);
            const serverBudget = 60000 + Number(body.timeoutMs || 180000);
            const autoJobMs = Number(process.env.DSH_HEADLESS_AUTO_JOB_MS) || 0;
            // ---- v0.9.3（D1/F3）：路线决策
            //   as_job=true                → 直接后台化（可选 wait_s 再等一会儿）
            //   serverBudget >= autoJobMs  → 同上（DSH_HEADLESS_AUTO_JOB_MS 显式打开时）
            //   否则                        → 先同步等，窗口到点就回 promoted 回执（结果永不丢）
            const wantJobFirst = asJob || (autoJobMs > 0 && serverBudget >= autoJobMs);
            if (wantJobFirst) {
                const r2 = await startJobThenMaybeWait(port, body, waitS);
                if (r2.text)
                    return { text: r2.text };
                const why = asJob ? 'as_job=true' : ('预计预算 ' + String(Math.round(serverBudget / 1000)) + 's ≥ DSH_HEADLESS_AUTO_JOB_MS=' + String(autoJobMs) + 'ms');
                if (r2.finished && r2.job) {
                    return headlessReceipt(r2.job, { mode: 'job', jobId: r2.jobId,
                        notes: ['已后台化并在等待窗口内完成（' + why + '）—— 这就是完整结果，不必再 collect'] });
                }
                return promotedReceipt(r2.job, r2.jobId, why + '：已后台化，等待窗口内没跑完（**不是失败**）');
            }
            let res = null;
            try {
                // 预算 = 等待窗口 + 2 s 余量：到点就转 promoted 回执（+8s 会让每次长活都多等 8 秒，实测浪费）
                res = await backendPost(port, '/headless', body, Math.min(serverBudget, HEADLESS_WAIT_MS + 2000));
            }
            catch (e) {
                // v0.9.3（D1）：客户端窗口到点 / 宿主 exec.signal 取消 —— 服务端子进程仍在跑
                return await promotedFromTimeout(port, runId, e);
            }
            const lt = leasedText(res);
            if (lt)
                return { text: lt };
            if (!res || res.error)
                return { text: 'HEADLESS 失败 · ' + String((res && (res.error || res.raw)) || 'unknown') + (res && res.hint ? ('\n' + String(res.hint)) : '') };
            // 后端 /headless 的信封是 {ok, ms, result:<引擎回执>} —— 结构化字段在 result 里（v0.9.3 起与 job 快照同形）
            const bodyRes = (res && res.result && typeof res.result === 'object') ? res.result : null;
            // 注意：**不能**拿 "resultJson 为空" 当缺字段 —— 脚本末行不是 JSON 时 resultJson 本来就该是 null（D3）。
            // 这里只判"后端根本没给引擎回执"。
            if (!bodyRes || (!bodyRes.status && bodyRes.result === undefined && bodyRes.resultJson === undefined && !bodyRes.shots)) {
                const keys = Object.keys(res || {}).filter((k) => k.indexOf('__') !== 0);
                const raw = String((res && res.raw) || '').slice(0, 2000);
                const lines = ['HEADLESS 结果缺字段（不是"失败"）· HTTP ' + String(res.__status) + ' · 后端回的键：' + (keys.length ? keys.join(', ') : '(空)')];
                if (Number(res.__heartbeats) > 0)
                    lines.push('（流式回执：' + String(res.__heartbeats) + ' 条心跳，但没取到结果行）');
                if (raw)
                    lines.push('--- 原始响应（前 2KB）---' + String.fromCharCode(10) + raw);
                if (body.script)
                    lines.push('脚本内容长度=' + String(String(body.script).length) + ' 字符（内容见后端临时脚本目录）');
                lines.push('预期日志：' + String(body.outdir || '(默认工作目录)') + '\\dsh_headless_*.stdout.log');
                lines.push('跟进：blender_viewport(op="status") 看 runs 台账（v0.8.10 起每次无头运行都有 id/pid/日志/产物）。');
                return { text: lines.join(String.fromCharCode(10)) };
            }
            const out = headlessReceipt(bodyRes, { mode: 'sync', jobId: null, notes: [] });
            // v0.8.9 边界提示：本次走了流式回执（>15 s，心跳 N 条）
            const beats = Number((res && res.__heartbeats) || 0);
            if (beats > 0)
                out.text += String.fromCharCode(10) + '（本次耗时 > 15 s，后端走了流式回执：' + String(beats) + ' 条心跳后收到结果）';
            return out;
        },
    })), '@dsh-external/dsh-blender-plugin: rt-headless');
    ctx.effect(() => ctx.tools.register(vTool({
        name: 'blender_rt_plan',
        description: '【契约层 + 规划器】把「假设 / 区间 / 校验 / 证据 / 门控」与「对象图编译」变成可调用 API。'
            + '契约 op：status · help · reset · register_component · register_connection · register_envelope · '
            + 'check_envelope · check_interference · check_interface · destructive_guard · evidence · ledger · report · '
            + 'verify · flip · advance；规划器 op（plan_ 前缀）：plan_load · plan_validate · plan_order · plan_build · '
            + 'plan_graph · plan_status · plan_help。判据与流程见 docs/假设驱动建模-cookbook.md（外部证据不足必须报 unresolved；'
            + '未判别的连接上做 boolean/weld/merge 会被 destructive_guard 拦下）。'
            + '**QC 也在这里**（v0.7.0，前缀 qc_）：qc_compare（参考图 vs 渲染：IoU/Dice/缺面积/多面积/边界距离/剖面差 + 叠加图与三联对照图）、'
            + 'qc_compare_basic（对齐逻辑照搬 plush-build 脚本，用于与历史数字对照）、qc_self_check（合成自检）、qc_robustness_check（平移/缩放鲁棒性）、qc_help。'
            + '**多视角渲染 harness 也在这里**（v0.8.8，P2-1）：op="qc_render_views"，args={file, views[], res, samples, budget_s|thr, outdir, ref_path} —— '
            + '按所有可见 mesh 的 AABB 自动取景（逐角解算 + margin）、临时建固定三点光（key/fill/rim，出图后删除并还原现场）、逐张 PNG + 计时 + md5、'
            + '写 <outdir>/render_views.jsonl（每行 {view, ms, bytes, hash}）、累计超预算立即停并标 within_budget=false；'
            + '给 ref_path 时逐张调 qc_compare（默认固定对齐）把 IoU/剖面差写进 jsonl。args 里加 asJob=true 自动转作业层（长活，无头进程天然隔离场景）。'
            + '**v0.9.0 渲染 harness 三件**：args.mode="parts_color"（逐部件配色出图 + parts_color_meta.txt 图例 + 出图后完整还原）、'
            + 'jsonl 每行带实测 device（防 Cycles 静默回落 CPU ≈7×；device=null 时会点名）、op="qc_render_catalog"（22 视角目录/别名/未知名回传）。'
            + '**网格体检与装配门接通（v0.9.0）**：op="audit_scene"/"audit_mesh"/"audit_duplicates"（单件网格健康）、'
            + '"audit_connectivity"（连通分量：**每个分量问「你有没有跟别的分量相接」**，bbox 粗筛 + BVH 网格级复核；'
            + '判据：可见浮块 = 最长 bbox 边 ≥ 全模型 1%；相接口径 micro_gap_mm 默认 0.3mm（单一实体/3D 打印口径；带设计间隙的装配件按工艺给 1–2）、'
            + '"audit_gate"（出厂门：连通 + 包络 + 未确认即降级）、'
            + '"audit_drift"（对称 Chamfer 距离 = 形状漂移，BVHTree 点到曲面，≈0.05 ≈ 最长边 2.5%；平移/整体缩放不进这个数，另有 bbox_delta 报位置尺寸差）、'
            + '"audit_measure"（测量包：世界 bbox + 逐轴间隙/重叠 mm/% + 邻居）、"audit_snap_floaters"（浮块贴到最近邻，默认只报告）。'
            + '**拼图**：op="montage"。**机构（v0.9.0）**：motion_*（关节轴/锚点实测、扫掠验证、URDF+USDA 导出）。'
            + '**交付（v0.9.0）**：deliver_*（单位盒归一化 + 多组 OBJ/MTL + manifest md5）。'
            + '**生成器（v0.9.0，代码化建模轻量版）**：generator_save/run/list/get/diff —— 程序即形状 + 编译门（全新无头进程复现）+ 模块级缓存 + 源码一变回执过期。'
            + '**v0.9.1 新增（rifle-build《93 反馈》）**：'
            + '文件级算子 `audit_interference`（两端可以是当前会话对象，也可以是**另一个 .blend 文件** → 交集体积估计 mm³ + 95% 置信区间 + 三态结论）与 `audit_overlap`（BVH 三角面对 + 交叠 bbox）；'
            + '`audit_connectivity`/`audit_gate`/`audit_measure` 也都接受 `file=`（跨 parts/*.blend 批处理，不再要求先 register_component）；'
            + '渲染队列 `render_lock`（跨进程文件锁：acquire/release/status，TTL + 持有者 + 等待毫秒；qc_render 出图会自动 acquire 并把 wait_ms 写进 jsonl）；'
            + 'GUI 原语 `gui_frame` / `gui_shading` / `gui_open`（rt_do 里 bpy.context.screen 为 None，做不到"同步 GUI"—— 这三条由插件侧在真 UI 上下文执行）。'
            + '另：plan 通道的结构化结果上限从 4KB 提到 12KB（量测表/探针输出不再被砍）。'
            + '每次顶层调用另写轨迹 JSONL（<工作目录>/trajectory/blender_rt-<日期>.jsonl；DSH_TRAJ=0 关、DSH_TRAJ_FULL=1 记更多参数）。',
        parameters: {
            op: { type: 'string', required: true, description: '契约 op 或 plan_<op>（见工具描述；op=help / plan_help 出速查）' },
            args: { type: 'json', description: 'op 的参数对象，例如 {"cid":"joint","err":184,"tolerance":220,"identifiable":["dy"]}' },
            path: { type: 'string', description: 'op=report 时的输出路径（Markdown）；省略则只回文本' },
        },
        output: { schema: ANY_SCHEMA, render: renderOne },
        isConcurrencySafe: () => false,
        timeoutMs: T.plan,
        async execute(argsIn) {
            if (!(await ensureBackend(port)))
                return { text: '后端不可用（127.0.0.1:' + String(port) + '）。' + BACKEND_HINT };
            const op = String((argsIn && argsIn.op) || 'status');
            const payload = jsonPayload(argsIn && argsIn.args);
            if (!Object.keys(payload).length && argsIn && typeof argsIn.args === 'string' && argsIn.args.trim()
                && argsIn.args.trim() !== '{}') {
                return { text: 'PLAN ' + op + ' 失败 · args 不是合法 JSON 对象：' + String(argsIn.args).slice(0, 200) };
            }
            if (op === 'report' && argsIn && argsIn.path)
                payload.path = String(argsIn.path);
            const r = await backendPost(port, '/plan', { op: op, args: payload }, 300000);
            const lt = leasedText(r);
            if (lt)
                return { text: lt };
            if (!r || r.ok !== true)
                return { text: 'PLAN ' + op + ' 失败 · ' + String((r && (r.error || r.raw)) || 'unknown') };
            const res = r.result;
            const txt = typeof res === 'string' ? res : JSON.stringify(res, null, 1);
            // v0.9.1（93-A3）：plan 通道的结构化结果过去被 4000 字符砍掉（量测表/探针输出常超）→ 提到 12KB，
            // 并明确告诉调用方"结果被截了、原量在引擎侧"（引擎对 >4KB 的结果会自动落盘到 results/）。
            const CAP = 12000;
            return { text: 'PLAN ' + op + ' ok · ' + String(txt.length) + ' chars\n' + (txt.length > CAP ? txt.slice(0, CAP) + String.fromCharCode(10) + '…[已截断，共 ' + String(txt.length) + ' 字符；结构化全量见后端 results/ 目录或用 headless(outJson=…) 落盘]' : txt) };
        },
    })), '@dsh-external/dsh-blender-plugin: rt-plan');
    ctx.effect(() => ctx.tools.register(vTool({
        name: 'blender_rt_worker',
        description: '【热无头会话】常驻的 blender -b 进程：start 拉起 / exec 跑代码片段（复用**持久内核 K 与同一个 Blender 会话**）/ status / stop / restart。'
            + '**什么时候该用它（v0.8.10，量化判定）**：同一个脚本要跑 ≥3 次，或单次 >10 s 且要反复迭代 —— 每次 headless 冷启动 1.1–1.5 s + EEVEE 着色器编译最多 ~16 s，'
            + 'worker 只付一次。反之：一次性脚本、要 GUI 上下文的 bpy.ops、要多进程并行 → 继续用 headless。'
            + '适合"反复迭代"的工作流 —— 省掉每次冷启动（0.9–1.2 s）与重复导入/重建的成本；无头侧也能用 K.dsh_view_api（exec 里 exec(open(<包路径>/runtime/view.py, encoding="utf-8").read()) 加载即可）。'
            + '语义与限制：① **串行** —— 一次只处理一个请求，长代码会占住 worker（可 op=status 看状态）；'
            + '② 无窗口 —— 依赖 GUI 上下文的 bpy.ops 可能失败（用 temp_override 或改用 GUI 通道）；'
            + '③ print("HEADLESS {json}")（单行）会作为 result 回传；异常带 traceback 返回；'
            + '④ 与 GUI 通道**互不干扰**（独立进程、不占 9876/9877）。',
        parameters: {
            op: { type: 'string', required: true, description: 'start | exec | status | stop | restart' },
            code: { type: 'string', description: 'op=exec 时的 Python 源码（预置 K/bpy/math/mathutils/Vector）' },
            timeout_ms: { type: 'integer', description: 'op=exec 的响应超时，默认 120000；长代码请调大' },
            gpu: { type: 'string', description: 'op=start 时的 GPU 语义（仅 cycles 路径）：auto（默认）/ true / false' },
            engine: { type: 'string', description: 'op=start 时的渲染引擎：eevee（默认 = EEVEE + 光追）/ cycles / keep；热会话让 EEVEE 着色器编译只付一次' },
            purge_prefix: { type: 'string', description: 'op=exec 时先清掉这些模块前缀（逗号分隔，如 "pe_geom,pe_look"）—— 热会话里改了用户模块必须清，否则 import 命中旧代码' },
        },
        output: { schema: ANY_SCHEMA, render: renderOne },
        isConcurrencySafe: () => false,
        timeoutMs: T.worker,
        async execute(args) {
            if (!(await ensureBackend(port)))
                return { text: '后端不可用（127.0.0.1:' + String(port) + '）。' + BACKEND_HINT };
            const op = String((args && args.op) || 'status');
            const body = { op: op };
            if (args && args.code !== undefined)
                body.code = String(args.code);
            if (args && args.timeout_ms)
                body.timeoutMs = Number(args.timeout_ms);
            if (args && args.gpu)
                body.gpu = String(args.gpu);
            if (args && args.engine)
                body.engine = String(args.engine);
            if (args && args.purge_prefix)
                body.purgePrefix = String(args.purge_prefix);
            const budget = 60000 + Number(body.timeoutMs || 120000);
            const r = await backendPost(port, '/worker', body, budget);
            const lt = leasedText(r);
            if (lt)
                return { text: lt };
            if (!r || r.ok !== true) {
                const rr = (r && r.result) || {};
                const bits = [];
                bits.push('WORKER ' + op + ' 失败 · ' + String((r && (r.error || rr.error)) || 'unknown'));
                if (rr.traceback)
                    bits.push('--- traceback ---' + String.fromCharCode(10) + String(rr.traceback).slice(0, 2000));
                if (rr.stderr && String(rr.stderr).trim())
                    bits.push('stderr: ' + String(rr.stderr).trim().slice(0, 1200));
                if (rr.stdout && String(rr.stdout).trim())
                    bits.push('stdout: ' + String(rr.stdout).trim().slice(0, 800));
                if (r && r.hint)
                    bits.push(String(r.hint));
                return { text: bits.join(String.fromCharCode(10)) };
            }
            const res = (r && r.result) || {};
            if (op === 'exec') {
                const parts = [];
                parts.push((res.ok ? 'WORKER exec ok' : 'WORKER exec 失败') + ' · ' + String(res.ms) + 'ms');
                if (res.result)
                    parts.push('result: ' + JSON.stringify(res.result).slice(0, 2000));
                if (res.error)
                    parts.push('error: ' + String(res.error));
                if (res.stdout && String(res.stdout).trim())
                    parts.push('--- stdout ---\n' + String(res.stdout).slice(-2500));
                if (res.stderr && String(res.stderr).trim())
                    parts.push('--- stderr ---\n' + String(res.stderr).slice(-1200));
                if (res.traceback)
                    parts.push('--- traceback ---' + String.fromCharCode(10) + String(res.traceback).slice(0, 1500));
                return { text: parts.join('\n') };
            }
            const parts2 = [];
            parts2.push('WORKER ' + op + ' ok · ' + JSON.stringify({ alive: res.alive, ready: res.ready, pid: res.pid, port: res.port, uptimeMs: res.uptimeMs }));
            if (res.gpu)
                parts2.push('GPU：' + (res.gpu.fell_back_to_cpu ? '⚠️ 回落 CPU' : ('✅ ' + String((res.gpu.after || {}).device_type || ''))) + ' ' + String(((res.gpu.after || {}).gpu_enabled || []).join(',')));
            if (res.status)
                parts2.push('status: ' + JSON.stringify(res.status));
            if (res.lastError)
                parts2.push('lastError: ' + String(res.lastError));
            if (res.stdoutTail)
                parts2.push('worker stdout 尾：' + String(res.stdoutTail).slice(-800));
            return { text: parts2.join('\n') };
        },
    })), '@dsh-external/dsh-blender-plugin: rt-worker');
    ctx.effect(() => ctx.tools.register(vTool({
        name: 'blender_rt_txn',
        description: '【事务 / 回滚】两级快照：**文件级** snapshot/restore（整场景回退；写 .blend 副本，用 copy=True 所以不改当前 filepath；大场景有 MB 级成本，本机 300 对象工程实测约 97MB / 0.8s）+ **对象级** mark/revert（只记 transform / 材质槽 / 可见性 / 修改器开关，毫秒级、就地回滚）。'
            + '用途："整个场景推倒重来之前先留个点"（snapshot）、"只改一处再对比"（mark 再 revert）。'
            + '⚠️ 边界：对象级**不含拓扑/UV/顶点级改动** —— Boolean、合并、删面之后回不去（revert 会跳过并报告），那种回滚请用文件级 snapshot/restore（restore 会丢掉当前未保存状态）。',
        parameters: {
            op: { type: 'string', required: true, description: 'snapshot | restore | list | prune | mark | revert | marks | drop | help' },
            label: { type: 'string', description: '快照 / mark 的名字（snapshot 省略则用时间戳）' },
            objects: { type: 'string', description: 'mark 时限定对象（逗号分隔；省略 = 全场景）' },
            keep: { type: 'integer', description: 'prune 只保留最近 N 个快照，默认 5' },
            note: { type: 'string', description: 'snapshot 备注' },
        },
        output: { schema: ANY_SCHEMA, render: renderOne },
        isConcurrencySafe: () => false,
        timeoutMs: T.txn,
        async execute(args) {
            if (!(await ensureBackend(port)))
                return { text: '后端不可用（127.0.0.1:' + String(port) + '）' };
            const op = String((args && args.op) || 'list');
            const a = {};
            if (args && args.label)
                a.label = String(args.label);
            if (args && args.note)
                a.note = String(args.note);
            if (args && args.keep)
                a.keep = Number(args.keep);
            if (args && args.objects)
                a.objects = String(args.objects).split(',').map((s) => s.trim()).filter(Boolean);
            const r = await backendPost(port, '/txn', { op: op, args: a }, 300000);
            const lt = leasedText(r);
            if (lt)
                return { text: lt };
            if (!r || r.ok !== true)
                return { text: 'TXN ' + op + ' 失败 · ' + String((r && (r.error || r.raw)) || 'unknown') };
            const res = (r && r.result) || {};
            const parts = [];
            parts.push('TXN ' + op + ' ok');
            if (op === 'snapshot' && res.snapshot)
                parts.push('快照 ' + String(res.snapshot.label) + ' · ' + String(res.snapshot.bytes) + 'B · ' + String(res.snapshot.ms) + 'ms · ' + String(res.snapshot.objects) + ' 对象' + String.fromCharCode(10) + '路径：' + String(res.snapshot.path));
            else if (op === 'restore')
                parts.push('已恢复：' + JSON.stringify(res.restored || {}).slice(0, 200) + String.fromCharCode(10) + String(res.warning || ''));
            else if (op === 'list') {
                parts.push('快照 ' + String((res.snapshots || []).length) + ' 个：' + (res.snapshots || []).map((s) => s.label + '(' + s.bytes + 'B)').join(' · '));
                parts.push('mark：' + JSON.stringify(res.marks || []));
                parts.push('目录：' + String(res.snapshot_dir || ''));
            }
            else if (op === 'mark')
                parts.push('已记 mark ' + String(res.mark) + ' · ' + String(res.objects) + ' 个对象');
            else if (op === 'revert')
                parts.push('回滚 ' + String(res.reverted) + '：改了 ' + String(res.changed_count) + ' 个' + (res.blocked && res.blocked.length ? (' · 跳过 ' + JSON.stringify(res.blocked).slice(0, 200)) : ''));
            else
                parts.push(JSON.stringify(res).slice(0, 800));
            return { text: parts.join(String.fromCharCode(10)) };
        },
    })), '@dsh-external/dsh-blender-plugin: rt-txn');
    ctx.effect(() => ctx.tools.register(vTool({
        name: 'blender_rt_preset',
        description: '【配方库】把「参数组合」变成可保存、可套用、可分发的资产：save / list / get / apply / delete / export / import / help。'
            + 'data 用点路径表达：材质节点 {"inputs.Base Color": [1,0,0,1]}、对象属性 {"location": [0,0,1]}、场景设置 {"render.resolution_x": 640}。'
            + 'apply 的 targets 用 MAT:材质名 / OBJ:对象名 / SCENE（逗号分隔）；不给 targets 只预览（dry_run），给了就实际写入并回报 applied/skipped/errors。'
            + 'export/import 走单个 JSON bundle，便于把配方分享到别的机器或会话。',
        parameters: {
            op: { type: 'string', required: true, description: 'save | list | get | apply | delete | export | import | help' },
            name: { type: 'string', description: '配方名（save / get / apply / delete）' },
            kind: { type: 'string', description: '分类（list 可按 kind 过滤），如 material / object / scene' },
            tags: { type: 'string', description: '标签（逗号分隔；list 可按 tag 过滤）' },
            note: { type: 'string', description: '备注（save）' },
            data: { type: 'string', description: '点路径 JSON 字符串（save），如 {"inputs.Roughness": 0.4}' },
            targets: { type: 'string', description: 'apply 目标（逗号分隔）：MAT:材质名 / OBJ:对象名 / SCENE' },
            path: { type: 'string', description: 'export 输出路径 / import 输入路径' },
            names: { type: 'string', description: 'export 选定的配方名（逗号分隔；省略=全部）' },
            overwrite: { type: 'boolean', description: 'save/import 是否覆盖同名（默认 true）' },
        },
        output: { schema: ANY_SCHEMA, render: renderOne },
        isConcurrencySafe: () => false,
        timeoutMs: T.preset,
        async execute(args) {
            if (!(await ensureBackend(port)))
                return { text: '后端不可用（127.0.0.1:' + String(port) + '）' };
            const op = String((args && args.op) || 'list');
            const a = {};
            if (args && args.name)
                a.name = String(args.name);
            if (args && args.kind)
                a.kind = String(args.kind);
            if (args && args.note)
                a.note = String(args.note);
            if (args && args.path)
                a.path = String(args.path);
            if (args && args.tags)
                a.tags = String(args.tags).split(',').map((x) => x.trim()).filter(Boolean);
            if (args && args.names)
                a.names = String(args.names).split(',').map((x) => x.trim()).filter(Boolean);
            if (args && args.targets)
                a.targets = String(args.targets).split(',').map((x) => x.trim()).filter(Boolean);
            if (args && args.data) {
                try {
                    a.data = JSON.parse(String(args.data));
                }
                catch (e) {
                    return { text: 'data 不是合法 JSON: ' + String(e.message).slice(0, 120) };
                }
            }
            if (args && args.overwrite === false)
                a.overwrite = false;
            const r = await backendPost(port, '/preset', { op: op, args: a }, 300000);
            const lt = leasedText(r);
            if (lt)
                return { text: lt };
            if (!r || r.ok !== true)
                return { text: 'PRESET ' + op + ' 失败 · ' + String((r && (r.error || r.raw)) || 'unknown') };
            const res = (r && r.result) || {};
            const parts = [];
            parts.push('PRESET ' + op + ' ok');
            if (op === 'list') {
                parts.push('共 ' + String(res.count) + ' 个：' + (res.presets || []).map((p) => p.name + '(' + p.kind + ',' + p.keys + '键)').join(' · '));
                parts.push('目录：' + String(res.dir || ''));
            }
            else if (op === 'apply') {
                const rr = res.report || {};
                parts.push('配方 ' + String(res.preset) + ' · 目标 ' + JSON.stringify(res.targets) + ' · 写入 ' + String((rr.applied || []).length) + ' 项');
                if ((rr.skipped || []).length)
                    parts.push('跳过：' + JSON.stringify(rr.skipped).slice(0, 300));
                if ((rr.errors || []).length)
                    parts.push('错误：' + JSON.stringify(rr.errors).slice(0, 300));
            }
            else
                parts.push(JSON.stringify(res).slice(0, 900));
            return { text: parts.join(String.fromCharCode(10)) };
        },
    })), '@dsh-external/dsh-blender-plugin: rt-preset');
    ctx.effect(() => ctx.tools.register(vTool({
        name: 'blender_rt_job',
        description: '【作业层】长任务后台化：start 立刻返回 job id（不占客户端连接、不会被工具超时掐断）；status/collect/**wait**/kill/list。'
            + '适合渲染一整晚、批量出图、大批量几何；子进程与 headless 同源（自动注入引擎前导），日志与产物落在 outdir/jobs/<id>/。'
            + '与 headless 的分工：预期 <100 s 用 headless 直接拿结果；更长 / 已知要跑很久 → as_job=true（或本工具 op=start）后台化。'
            + '**run 与 job 同一 id 空间**：headless 的 runId（run-…）也能用 status/collect/wait/kill 收（v0.9.3 / D1）。'
            + '**v0.9.3 变化（外部反馈 2026-09-24）**：'
            + '① op=start 与 headless 同形参 —— `script_file` / `args` / `env` / `factory_startup` / `bootstrap` / `workdir` / `include_noise` / `out_json` 全收（旧版只认 script，靠 DSH_ARGS 取参的脚本无法复用）；'
            + '② **op=wait**：阻塞到完成或超时（默认 120 s/次，上限 600 s），一次拿到结构化结果 —— 别再连发 op=status（会撞 harness 的重复调用检测）；'
            + '③ 运行期可观测：三处 spawn 注入 **PYTHONUNBUFFERED=1**，stdout.log 边跑边写；`op=status` 回 **stage/stageAt/stageAgeMs/lastOutputAt/idleMs/lines/logBytes/pidAlive**，脚本里 `dsh_stage("building")` 打心跳即可；'
            + '④ 状态与实际进程对账：后端重启后的 stale running 会收敛成 `status="stale"`（带 staleReason），不再谎报 running；`op=kill` 对未知 id **不抛错**（回「已结束/不存在」）；'
            + '⑤ 回执结构化：第 1 个 text block 是单行 JSON 信封（status/resultJson/resultPath/stdoutTail/stage/…），第 2 个是人读摘要；>4KB 的结果自动落盘给 resultPath。'
            + 'v0.8.9 起 op=start 也支持 preload（与 headless 同一套，如 preload:"qc,qc_render,audit"）：长活里直接用 K.dsh_qc_render_api / K.dsh_audit_api；脚本里 print("HEADLESS {json}") 仍是结果契约。',
        parameters: {
            op: { type: 'string', required: true, description: 'start | status | collect | wait | kill | list' },
            id: { type: 'string', description: 'status/collect/wait/kill 的 id（job-… 或 headless 的 run-…）' },
            script: { type: 'string', description: 'op=start：Python 源码（print HEADLESS 加单行 JSON 作为结果回传）' },
            script_file: { type: 'string', description: '【v0.9.3】op=start：直接跑一个 .py 文件（Windows/WSL 路径都收）—— 与 headless 同一语义；file= 仍是 .blend（传 .py 会自动识别为脚本）' },
            args: { type: 'string', description: '【v0.9.3】op=start：额外命令行参数（空格分隔；数组也可），脚本里从 sys.argv 读，同时写进 DSH_ARGS 供 K.args 读' },
            env: { type: 'json', description: '【v0.9.3】op=start：额外环境变量 {KEY:"VALUE"}（与 headless 同）' },
            file: { type: 'string', description: 'op=start：可选 .blend 工程（回执带 inputFile{size,mtime,md5}）' },
            outdir: { type: 'string', description: 'op=start：产物目录（Windows 路径或 **WSL 路径**）；日志落在其 jobs/<id>/ 下' },
            timeout_ms: { type: 'integer', description: 'op=start：作业上限毫秒（默认 3600000，上限 24 小时，到点 SIGKILL）；op=wait：本次等待上限（默认 120000，上限 600000）' },
            engine: { type: 'string', description: 'op=start：eevee（默认，含光追前导）/ cycles / keep' },
            preload: { type: 'string', description: 'op=start：预载 runtime 里的 python 模块（逗号分隔，如 "qc,qc_render" 或 "view,perf,audit"）' },
            factory_startup: { type: 'boolean', description: '【v0.9.3】op=start：默认 true = --factory-startup' },
            bootstrap: { type: 'boolean', description: '【v0.9.3】op=start：默认 true = 注入持久内核 K' },
            workdir: { type: 'string', description: '【v0.9.3】op=start：脚本内 chdir + sys.path 首位' },
            include_noise: { type: 'boolean', description: '【v0.9.3】op=start/collect：产物清单是否包含噪音文件（默认 false 过滤）' },
            out_json: { type: 'string', description: '【v0.9.3】op=start：结构化结果落盘路径（>4KB 也会自动落 results/，路径在 resultPath）' },
            tail: { type: 'integer', description: 'collect/wait：stdout/stderr 尾部取多少字符（默认 4000）' },
        },
        output: { schema: ANY_SCHEMA, render: renderStructured },
        isConcurrencySafe: () => true,
        timeoutMs: T.job,
        async execute(args) {
            if (!(await ensureBackend(port)))
                return { text: '后端不可用（127.0.0.1:' + String(port) + '）' };
            const op = String((args && args.op) || 'list');
            const body = { op: op };
            if (args && args.id)
                body.id = String(args.id);
            if (args && args.script !== undefined)
                body.script = String(args.script);
            if (args && args.script_file)
                body.scriptFile = String(args.script_file);
            if (args && args.args)
                body.args = splitArgs(args.args);
            if (args && args.env && typeof args.env === 'object')
                body.env = args.env;
            if (args && args.file)
                body.file = String(args.file);
            if (args && args.outdir)
                body.outdir = String(args.outdir);
            if (args && args.engine)
                body.engine = String(args.engine);
            if (args && args.preload)
                body.preload = String(args.preload);
            if (args && args.workdir)
                body.workdir = String(args.workdir);
            if (args && args.out_json)
                body.outJson = String(args.out_json);
            if (args && args.include_noise !== undefined)
                body.includeNoise = !!args.include_noise;
            if (args && args.factory_startup !== undefined)
                body.factoryStartup = !!args.factory_startup;
            if (args && args.bootstrap !== undefined)
                body.bootstrap = !!args.bootstrap;
            if (args && args.tail)
                body.tail = Number(args.tail);
            if (args && args.timeout_ms)
                body.timeoutMs = Number(args.timeout_ms);
            // v0.9.3（D6.2）：op=wait 会阻塞 → 给足 HTTP 预算（服务端默认 120 s/次，上限 600 s）
            const budget = op === 'wait' ? Math.min(600000, Number(args && args.timeout_ms) || 120000) + 30000 : 60000;
            const r = await backendPost(port, '/job', body, budget);
            const lt = leasedText(r);
            if (lt)
                return { text: lt };
            if (!r || r.ok !== true)
                return { text: 'JOB ' + op + ' 失败 · ' + String((r && (r.error || r.raw)) || 'unknown') };
            if (op === 'list') {
                const js = (r && r.jobs) || [];
                if (!js.length)
                    return { text: '没有作业' };
                const lines = js.map((j) => '  ' + String(j.id) + ' · ' + String(j.status)
                    + (j.stageName ? (' · stage=' + String(j.stageName)) : '')
                    + (typeof j.ms === 'number' ? (' · ' + String(Math.round(j.ms / 1000)) + 's') : '')
                    + (j.fromLedger ? ' · (磁盘台账)' : '')
                    + ' · 产物 ' + String((j.artifacts || []).length) + ' 项');
                return { text: '作业（' + String(js.length) + '）：' + String.fromCharCode(10) + lines.join(String.fromCharCode(10)) };
            }
            const j = (r && r.job) || {};
            // ---- 统一回执：run/job 同一套字段（D2）
            const ctx2 = { mode: 'job', jobId: (String(j.id || '').indexOf('job-') === 0) ? j.id : null, runId: j.runId || null, notes: [], hint: null };
            if (op === 'start') {
                ctx2.notes.push('已后台化（pid ' + String(j.pid) + '，日志目录 ' + String(j.logDir || '') + '）');
                ctx2.hint = '用 op=wait id=' + String(j.id) + ' 阻塞等结果，或 op=collect 看进展（stage/idleMs）；op=kill 中止';
                const out0 = promotedReceipt(j, String(j.id), 'op=start 已接受');
                out0.envelope.mode = 'job-start';
                out0.envelope.notes = ctx2.notes;
                out0.envelope.hint = ctx2.hint;
                return out0;
            }
            if (op === 'kill') {
                const t = (j.alreadyFinished || j.status === 'unknown')
                    ? ('JOB kill · ' + String(j.id) + ' · ' + String(j.status) + '（' + String(j.note || '已结束/不存在，无需 kill') + '）')
                    : ('JOB kill · ' + String(j.id) + ' · status=' + String(j.status) + '（已发 SIGKILL）');
                return { text: t, envelope: { kind: 'job', mode: 'job-kill', id: j.id || null, status: j.status || null, ok: true,
                        alreadyFinished: !!j.alreadyFinished, note: j.note || null, fromLedger: !!j.fromLedger } };
            }
            if (j.status === 'stale' || j.fromLedger) {
                return { text: 'JOB ' + op + ' · ' + String(j.id || '') + ' · status=' + String(j.status)
                        + String.fromCharCode(10) + '（磁盘台账记录）' + String(j.staleReason || '') + String.fromCharCode(10) + String(j.hint || ''),
                    envelope: { kind: 'ledger', mode: 'job-' + op, id: j.id || null, status: j.status || null, ok: true,
                        fromLedger: true, staleReason: j.staleReason || null, pid: j.pid || null, pidAlive: !!j.pidAlive,
                        resultJson: j.resultJson || null, resultPath: j.resultPath || null, stdoutTail: clipText(j.stdoutTail, 1000, 1000) } };
            }
            if (op === 'collect' && j.status === 'running') {
                ctx2.notes.push('任务仍在跑（不是失败）：stage=' + safeStringify(j.stage || j.stageName) + ' · 最近输出 ' + String(Math.round(Number(j.idleMs || 0) / 1000)) + 's 前');
                const out1 = promotedReceipt(j, String(j.id), 'op=collect：任务还在跑');
                out1.envelope.mode = 'collect-running';
                out1.envelope.notes = ctx2.notes;
                return out1;
            }
            if (op === 'wait' && j.waitTimedOut) {
                const out2 = promotedReceipt(j, String(j.id), 'op=wait：等待窗口（' + String(Math.round(Number(j.waitedMs || 0) / 1000)) + 's）到点，任务仍在跑');
                out2.envelope.mode = 'wait-timeout';
                return out2;
            }
            const notes = [];
            if (op === 'wait')
                notes.push('op=wait 等到终态（等了 ' + String(Math.round(Number(j.waitedMs || 0) / 1000)) + 's）');
            if (j.kind === 'headless' || String(j.id || '').indexOf('run-') === 0)
                notes.push('这是 blender_rt_headless 的运行台账（run 与 job 同一 id 空间）');
            return headlessReceipt(j, { mode: 'job', jobId: ctx2.jobId, runId: j.runId || null, notes: notes });
        },
    })), '@dsh-external/dsh-blender-plugin: rt-job');
    // ---------- 运维 ----------
    ctx.effect(() => ctx.tools.register(vTool({
        name: 'blender_viewport',
        description: '实时通道后端（127.0.0.1:' + String(port) + '）运维入口：status 看健康/视口区域/计数，doctor 做连通性体检（区分 Blender 未连/主线程忙/addon 线程卡死并给修法），start/stop/restart 管后端，who 看写通道租约（多会话共存时先看它）、lease 拿/抢写权限、release 释放。'
            + '**op="launch"（v0.9.4）：一键拉起 GUI Blender 并自动 Connect addon** —— agent 没有人手去点「N 面板 → Connect」，'
            + '所以这条把"启动 Blender + 让 addon 起 socket server"固化成一个调用：写 boot 脚本（GUI 里 enable addon → 起 server）→ detached spawn blender.exe → '
            + '**轮询 addon 端口**（唯一可信判据，不靠进程活着）→ 端口开了顺手跑一次 doctor 回诊断；幂等（已在监听就直接返回 already=true，不会堆出第二个 Blender）。'
            + '参数：wait_ms 等待上限（默认 90000）、file 要打开的 .blend、exe 指定 blender.exe、addon_module/addon_file 指定 addon、dry_run 只看命令不启动。',
        parameters: {
            op: { type: 'string', required: true, description: 'status | doctor | who | lease | release | start | stop | restart | launch' },
            holder: { type: 'string', description: 'lease/release 时的持有者名（默认本插件进程 pid 标识）' },
            ttl_ms: { type: 'integer', description: 'lease 有效期毫秒，默认 600000（10 min）；写操作会自动续期' },
            force: { type: 'boolean', description: 'lease 时抢占别人持有的租约（默认 false，被别人占用时返回 409 提示）' },
            wait_ms: { type: 'integer', description: '【launch】等 addon 端口打开的上限，默认 90000ms（Blender 冷启动 + addon 加载要几十秒）' },
            file: { type: 'string', description: '【launch】启动时要打开的 .blend（WSL/Windows 路径都收）' },
            exe: { type: 'string', description: '【launch】显式指定 blender.exe（覆盖配置探测；排查"探测到的 exe 不对"时用）' },
            addon_module: { type: 'string', description: '【launch】要 enable 的 addon 模块名（逗号分隔）；默认自动扫名字里带 mcp 的模块' },
            addon_file: { type: 'string', description: '【launch】直接按文件 import 的 addon .py 绝对路径（Blender 5.x extension 布局下 addon_utils 扫不到老 scripts/addons 时的兜底）' },
            dry_run: { type: 'boolean', description: '【launch】true = 只回报将要执行的命令，不真的启动' },
        },
        output: {
            schema: { type: 'string' },
            render: (_args, value) => [{ type: 'text', text: String(value) }],
        },
        timeoutMs: T.viewport,
        async execute(args) {
            const op = String((args && args.op) || 'status');
            if (op === 'start') {
                paused = false;
                if (await probe(port))
                    return '后端已在运行：' + base(port) + '/（看护已启用）';
                const how = startBackend(port);
                for (let i = 0; i < 20; i++) {
                    if (await probe(port))
                        return '后端已启动（' + how + '）：' + base(port) + '/（看护已启用）';
                    await sleep(300);
                }
                return '启动失败：' + how + '（检查 ' + SERVER_PATH + ' 与 node 是否可用）';
            }
            if (op === 'restart') {
                paused = false;
                if (child) {
                    try {
                        child.kill();
                    }
                    catch (e) { }
                    child = null;
                }
                killBackendByCmd(port);
                await sleep(500);
                const how2 = startBackend(port);
                for (let i = 0; i < 20; i++) {
                    if (await probe(port))
                        return '后端已重启（' + how2 + '）：' + base(port) + '/';
                    await sleep(300);
                }
                return '重启失败：' + how2;
            }
            if (op === 'stop') {
                paused = true;
                const notes = [];
                if (child) {
                    try {
                        child.kill();
                    }
                    catch (e) { }
                    child = null;
                    notes.push('child-句柄已杀');
                }
                const killed = killBackendByCmd(port);
                if (killed.length)
                    notes.push('残留后端已杀 pid=' + killed.join(','));
                if (!notes.length)
                    return '没有在跑的后端（127.0.0.1:' + String(port) + '）；看护已暂停';
                await sleep(400);
                return '已停止后端：' + notes.join(' · ') + '（看护已暂停，op=start 恢复）';
            }
            if (op === 'who' || op === 'lease' || op === 'release') {
                try {
                    if (op === 'who') {
                        const w = await backendJson(port, '/who', 15000);
                        const lz = (w && w.lease) || {};
                        const m = (w && w.metrics) || {};
                        return [
                            '租约：' + (lz.active ? ('持有者 ' + String(lz.holder) + '（剩余 ' + String(Math.round(Number(lz.expiresInMs || 0) / 1000)) + 's，续期 ' + String(lz.renewals || 0) + ' 次）') : '空闲（谁都能写）'),
                            '本会话 holder：' + HOLDER + (lz.active && lz.holder === HOLDER ? '（就是本会话）' : ''),
                            '通道：calls=' + String(m.calls || 0) + ' errors=' + String(m.errors || 0) + ' timeouts=' + String(m.timeouts || 0) + ' inflight=' + String(m.inflight || 0) + ' lastCmd=' + String(m.lastCmd || '-'),
                            '统计：' + JSON.stringify((w && w.stats) || {}),
                        ].join('\n');
                    }
                    const body = { holder: String((args && args.holder) || HOLDER) };
                    if (args && args.ttl_ms)
                        body.ttlMs = Number(args.ttl_ms);
                    if (op === 'lease' && args && args.force)
                        body.force = true;
                    const r = await backendPost(port, op === 'lease' ? '/lease' : '/release', body, 20000);
                    if (op === 'lease' && r && r.error === 'leased') {
                        return '租约被占用：holder=' + String(r.holder) + '，剩余 ' + String(Math.round(Number(r.expiresInMs || 0) / 1000)) + 's\n'
                            + String(r.hint || '') + '\n（确认对方在跑就知道为什么被挡；要强抢再加 force=true）';
                    }
                    return (op === 'lease' ? 'LEASE ' : 'RELEASE ') + JSON.stringify(r);
                }
                catch (e) {
                    return op + ' 失败：' + String((e && e.message) || e) + '（后端没起？blender_viewport op=start）';
                }
            }
            // v0.9.4：一键拉起 GUI Blender + 自动 Connect addon（agent 没有人手点 N 面板）
            if (op === 'launch') {
                try {
                    const body = {};
                    if (args && args.wait_ms)
                        body.waitMs = Number(args.wait_ms);
                    if (args && args.file)
                        body.file = String(args.file);
                    if (args && args.exe)
                        body.exe = String(args.exe);
                    if (args && args.addon_module)
                        body.addonModule = String(args.addon_module);
                    if (args && args.addon_file)
                        body.addonFile = String(args.addon_file);
                    if (args && args.dry_run)
                        body.dryRun = true;
                    const r = await backendPost(port, '/launch', body, Math.max(30000, Number((args && args.wait_ms) || 90000) + 30000));
                    const lines = [];
                    lines.push((r && r.ok ? 'LAUNCH ok' : 'LAUNCH 未成功')
                        + (r && r.already ? ' · 已经在监听（幂等，未重复启动）' : (r && r.dryRun ? ' · dryRun（没启动）' : ''))
                        + (r && r.launched ? ' · 已 spawn pid=' + String(r.pid) : '')
                        + ' · addon 端口 ' + String((r && r.addonPort) || '-')
                        + ' · 等待 ' + String((r && r.waitedMs) || 0) + 'ms');
                    if (r && r.exe)
                        lines.push('blender.exe：' + String(r.exe) + (r.configFrom ? ('（来源 ' + String(r.configFrom) + '）') : ''));
                    if (r && r.error)
                        lines.push('错误：' + String(r.error));
                    if (r && r.hint)
                        lines.push('提示：' + String(r.hint));
                    if (r && Array.isArray(r.steps))
                        for (const s of r.steps)
                            lines.push('· ' + String(s));
                    const b = r && r.boot;
                    if (b) {
                        lines.push('boot 结论（GUI 侧自己写的 launch-status.json）：loaded_by=' + String(b.loaded_by || '-')
                            + ' · server_running=' + String(b.server_running) + ' · enabled=' + JSON.stringify(b.enabled || []));
                        if (Array.isArray(b.errors) && b.errors.length)
                            lines.push('boot 错误：' + b.errors.slice(0, 4).join(' | '));
                        if (b.env)
                            lines.push('boot 环境：' + JSON.stringify(b.env));
                        if (Array.isArray(b.addon_modules_seen) && b.addon_modules_seen.length)
                            lines.push('GUI 里可见的 addon 模块：' + b.addon_modules_seen.slice(0, 20).join(', '));
                    }
                    else if (r && (r.launched || r.dryRun)) {
                        lines.push('boot 状态文件：' + String(r.statusFile || '?') + '（没读到：可能 Blender 还没走到脚本，或 exe 路径不对）');
                    }
                    const doc = r && r.doctor;
                    if (doc && doc.addon)
                        lines.push('启动后体检：' + String((doc.addon && doc.addon.summary) || JSON.stringify(doc.addon)).slice(0, 400));
                    if (r && r.bootScript)
                        lines.push('boot 脚本：' + String(r.bootScript) + '（想手工排查就直接跑它）');
                    return lines.join(String.fromCharCode(10));
                }
                catch (e) {
                    return 'launch 失败：' + String((e && e.message) || e) + '（后端没起？先 blender_viewport op=start —— 那是管**后端**进程的，本 op 才是管 Blender 的）';
                }
            }
            if (op === 'doctor') {
                try {
                    const d = await backendJson(port, '/doctor');
                    const m = (d && d.metrics) || {};
                    const diag = (d && d.diagnosis) || m.lastDiagnosis || null;
                    const lines = [];
                    lines.push('诊断：' + String((d && d.kind) || (diag && diag.kind) || (d && d.ok ? 'ok' : 'unknown')));
                    lines.push('宿主 API：' + HOST_API); // v0.9.2（R2）：DSH 升级后一眼看出插件挂的是哪份 dsh-tools
                    lines.push('插件：v' + PLUGIN_VERSION + ' @ ' + HERE);
                    if (diag && diag.summary)
                        lines.push('结论：' + diag.summary);
                    if (diag && diag.fix)
                        lines.push('修法：' + diag.fix);
                    if (d && d.addon)
                        lines.push('addon：' + JSON.stringify(d.addon));
                    const led = (d && d.ledger) || null;
                    if (led)
                        lines.push('工作目录：' + String((led.workDir && led.workDir.wsl) || '?')
                            + (led.workDir && led.workDir.writable ? '（可写）' : '（⚠️ **不可写** —— ' + String(led.lastError || '?') + '）'));
                    if (led && !led.writable && led.hint)
                        lines.push('修法：' + String(led.hint));
                    lines.push('计数：' + JSON.stringify(d && d.stats ? d.stats : {}));
                    lines.push('指标：calls=' + String(m.calls || 0) + ' errors=' + String(m.errors || 0) + ' timeouts=' + String(m.timeouts || 0)
                        + ' inflight=' + String(m.inflight || 0) + ' lastCmd=' + String(m.lastCmd || '-')
                        + (m.lastCmdAgeMs === null || m.lastCmdAgeMs === undefined ? '' : (' (' + String(Math.round(Number(m.lastCmdAgeMs) / 1000)) + 's 前)'))
                        + ' views=' + String(m.views || 0) + ' headlessRuns=' + String(m.headlessRuns || 0));
                    return lines.join('\n');
                }
                catch (e) {
                    return '体检失败：' + String((e && e.message) || e) + '（后端可能没起：blender_viewport op=start）';
                }
            }
            try {
                const s = await backendJson(port, '/status');
                const views = s && s.region && s.region.views ? s.region.views : [];
                const v = views[0] || null;
                return JSON.stringify({
                    running: true,
                    paused: paused,
                    child: child ? (childAlive() ? ('alive pid=' + String(child.pid)) : ('dead pid=' + String(child.pid))) : 'none',
                    watchdog: '15s',
                    endpoint: base(port),
                    viewportRegion: v ? v.region : null,
                    shading: v ? v.shading : null,
                    window: s && s.region ? s.region.window : null,
                    stats: s.stats,
                    diagnosis: s.diagnosis || null,
                    addonError: s.addonError || null, // v0.9.3：addon 没连上也要能看到本地信息（台账/工作目录）
                    plugin: s.plugin || null, // v0.8.10（A0）：进程内 lib 的版本 + runtime 指纹
                    pluginVersion: s.plugin ? s.plugin.version : null,
                    hostApi: HOST_API, // v0.9.2（R2）：插件侧解析到的宿主 dsh-tools
                    toolVersion: PLUGIN_VERSION,
                    recentRuns: (s.runs || []).map((x) => ({ id: x.id, status: x.status, pid: x.pid, ms: x.ms,
                        outdir: x.outdir, artifacts: x.artifactCount,
                        expectOk: x.expect ? x.expect.ok : null })),
                    ledger: s.ledger || null, // v0.8.10（D4）：磁盘台账（后端重启后仍可查）
                    // v0.9.3：workDir 可写性自证（只读挂载上写台账/日志会静默失败 —— 这里必须说出来）
                    workDir: (s.ledger && s.ledger.workDir) || null,
                    workDirWritable: !!(s.ledger && s.ledger.writable),
                    workDirWarning: (s.ledger && !s.ledger.writable) ? String(s.ledger.hint || s.ledger.lastError || '') : null,
                });
            }
            catch (e) {
                return JSON.stringify({ running: false, url: base(port) + '/', error: String((e && e.message) || e), hint: '用 blender_viewport op=start 拉起' });
            }
        },
    })), '@dsh-external/dsh-blender-plugin: tool');
}
/* ────────────────────────────────────────────────────────────────────────────
 * v0.9.4：**测试接缝**。tests/lossless_guard.mjs 直接 import 本模块，就能拿到
 *   ① 真实回执构造器（receiptEnvelope / headlessReceipt / promotedReceipt）
 *   ② 出口消毒器（losslessSanitize）
 * 然后用**宿主的真校验器**（dsh-util-values 的 snapshotJsonValue）断言：
 * "本插件任何一个回执都过得了 lossless-JSON 门"。
 * 这是把「返回通道」从"靠人记得别写 undefined"变成"机器每次回归都验"的唯一办法。
 * ──────────────────────────────────────────────────────────────────────────── */
export const __internals = {
    losslessSanitize: losslessSanitize,
    receiptEnvelope: receiptEnvelope,
    headlessReceipt: headlessReceipt,
    promotedReceipt: promotedReceipt,
    HEADLESS_WAIT_MS: HEADLESS_WAIT_MS,
    PLUGIN_VERSION: PLUGIN_VERSION,
};
//# sourceMappingURL=index.js.map