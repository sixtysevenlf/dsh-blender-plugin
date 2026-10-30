export function renderOne(_args, value) {
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
export function safeStringify(v) {
    try {
        return JSON.stringify(v);
    }
    catch (e) {
        return null;
    }
}
/** 截断（保留头尾，中间省略标记）—— 与后端 clipMiddle 同一口径 */
export function clipText(s, head, tail) {
    const t = String(s == null ? '' : s);
    if (t.length <= head + tail)
        return t;
    return (t.slice(0, head) +
        String.fromCharCode(10) +
        '…[中间省略 ' +
        String(t.length - head - tail) +
        ' 字符]…' +
        String.fromCharCode(10) +
        t.slice(-tail));
}
/** 带结构化信封的工具回执：value = {text, envelope?, image?, images?} */
export function renderStructured(_args, value) {
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
/* ────────────────────────────────────────────────────────────────────────────
 * v2.6.1（工具卡片）：presentCall / presentResult —— **只管给人看的卡片**，模型侧回执
 * （render 的输出）一个字节都不动。宿主（dsh-TUI / Web）没有这两个方法时显示的是
 * `Blender_rt_do({"code":"import bpy\n…"})` 这种整段 JSON 参数 + 原始回执（含单行 JSON 信封）。
 *   - 调用卡片：标题写成「Blender <动作> · <要点>」；带代码的调用把代码放进卡片正文。
 *   - 结果卡片：去掉 renderStructured 的单行 JSON 信封（那是给模型 JSON.parse 的），
 *     人读摘要放最前（TUI 折叠态只显示前 3 行），代码附在后面，图片块原样透传。
 * 纯函数、只依赖 args / result（宿主回放会话日志时也会调）；任何异常都回 undefined ⇒ 宿主退回通用卡片。
 * ──────────────────────────────────────────────────────────────────────────── */
const CARD_DETAIL_MAX = 48;
/** 卡片正文里代码的上限（头 + 尾，中间省略）：几千行脚本不该把卡片撑爆 */
const CARD_CODE_HEAD = 3000;
const CARD_CODE_TAIL = 1000;
/** 压成一行并截断（卡片标题只放一行） */
function cardClip(v, max = CARD_DETAIL_MAX) {
    const one = String(v == null ? '' : v)
        .replace(/\s+/g, ' ')
        .trim();
    return one.length > max ? one.slice(0, max - 1) + '…' : one;
}
/** 代码 → 「首个有意义的行（+N 行）」；全是注释/空行时回空串 */
function cardCodeHeadline(code) {
    const lines = String(code == null ? '' : code)
        .split(String.fromCharCode(10))
        .map((l) => l.trim())
        .filter(Boolean);
    const first = lines.find((l) => !l.startsWith('#'));
    if (!first)
        return '';
    return cardClip(first) + (lines.length > 1 ? '（+' + String(lines.length - 1) + ' 行）' : '');
}
/** 路径 → 文件名（Windows / POSIX 分隔符都认） */
function cardBaseName(p) {
    const parts = String(p == null ? '' : p).split(/[\\/]/);
    return parts[parts.length - 1] || '';
}
/** rt_cmd 的 params：对象列前 3 个键，字符串截一行 */
function cardParams(p) {
    if (p && typeof p === 'object' && !Array.isArray(p)) {
        const keys = Object.keys(p);
        return keys.length ? '{' + keys.slice(0, 3).join(', ') + (keys.length > 3 ? ', …' : '') + '}' : '';
    }
    return typeof p === 'string' ? cardClip(p, 32) : '';
}
/** 「头 · 要点1 · 要点2」，空要点跳过 */
function cardTitle(head, parts) {
    const tail = parts.map((x) => cardClip(x)).filter(Boolean);
    return tail.length ? head + ' · ' + tail.join(' · ') : head;
}
const cardSession = (a) => (a.session ? '无头 ' + cardClip(a.session, 24) : '');
const cardPython = (code) => (typeof code === 'string' && code.trim() ? { label: 'Python', code: code } : null);
export const CARD_SPECS = {
    blender_rt_see: {
        kind: 'read',
        title: (a) => a.full
            ? cardTitle('Blender 截窗口', ['区域 ' + String(a.area || 0)])
            : cardTitle('Blender 看视口', [
                a.from || a.look_at ? '自定义视角 ' + cardClip(a.from || '?', 20) + ' → ' + cardClip(a.look_at || '?', 20) : '',
                a.shading,
                String(a.max_size || 560) + 'px',
                cardSession(a)
            ])
    },
    blender_rt_do: {
        kind: 'execute',
        title: (a) => cardTitle('Blender 执行 Python', [a.code ? cardCodeHeadline(a.code) : cardBaseName(a.file), cardSession(a)]),
        code: (a) => cardPython(a.code)
    },
    blender_rt_watch: {
        kind: 'execute',
        title: (a) => cardTitle('Blender 连续观察', [
            a.seconds ? String(a.seconds) + 's' : '',
            a.fps ? String(a.fps) + 'fps' : '',
            cardCodeHeadline(a.code),
            cardSession(a)
        ]),
        code: (a) => cardPython(a.code)
    },
    blender_rt_cmd: { kind: 'execute', title: (a) => cardTitle('Blender 命令', [a.name, cardParams(a.params)]) },
    blender_rt_commands: { kind: 'search', title: () => 'Blender 命令清单' },
    blender_rt_loop: {
        kind: 'execute',
        title: (a) => cardTitle('Blender 内环', [a.op, a.target, a.iterations ? String(a.iterations) + ' 次' : ''])
    },
    blender_rt_perf: { kind: 'other', title: (a) => cardTitle('Blender 性能', [a.op]) },
    blender_rt_opt: { kind: 'edit', title: (a) => cardTitle('Blender 精简', [a.op]) },
    blender_rt_headless: {
        kind: 'execute',
        title: (a) => cardTitle('Blender 无头', [
            a.script_file ? cardBaseName(a.script_file) : cardCodeHeadline(a.script),
            cardBaseName(a.file),
            a.as_job ? '作业' : ''
        ]),
        code: (a) => cardPython(a.script)
    },
    blender_rt_plan: { kind: 'other', title: (a) => cardTitle('Blender 规划', [a.op, cardSession(a)]) },
    blender_rt_worker: {
        kind: 'execute',
        title: (a) => cardTitle('Blender 热会话', [a.op, a.name, cardCodeHeadline(a.code)]),
        code: (a) => cardPython(a.code)
    },
    blender_rt_txn: { kind: 'other', title: (a) => cardTitle('Blender 事务', [a.op, a.label]) },
    blender_rt_preset: { kind: 'other', title: (a) => cardTitle('Blender 配方', [a.op, a.name]) },
    blender_rt_job: {
        kind: 'execute',
        title: (a) => cardTitle('Blender 作业', [a.op, a.id, a.script_file ? cardBaseName(a.script_file) : cardCodeHeadline(a.script)]),
        code: (a) => cardPython(a.script)
    },
    blender_viewport: { kind: 'other', title: (a) => cardTitle('Blender 通道', [a.op || 'status']) }
};
/** 卡片正文里的代码段：一行标签 + 原样代码（过长时保留头尾） */
function cardCodeSection(code) {
    const body = code.code.replace(/\s+$/, '');
    const n = body.split(String.fromCharCode(10)).length;
    return '▸ ' + code.label + '（' + String(n) + ' 行）' + String.fromCharCode(10) + clipText(body, CARD_CODE_HEAD, CARD_CODE_TAIL);
}
/** renderStructured 的第 0 块：单行、能 parse 成普通对象的 JSON */
function isEnvelopeBlock(b) {
    if (!b || b.type !== 'text' || typeof b.text !== 'string')
        return false;
    const s = b.text.trim();
    if (!s.startsWith('{') || !s.endsWith('}') || s.indexOf(String.fromCharCode(10)) >= 0)
        return false;
    try {
        const v = JSON.parse(s);
        return !!v && typeof v === 'object' && !Array.isArray(v);
    }
    catch (e) {
        return false;
    }
}
/** vTool 用：按工具名给出 presentCall / presentResult（未登记的工具 / spec 自带的不覆盖） */
export function cardPresenters(spec) {
    const card = spec && CARD_SPECS[spec.name];
    if (!card)
        return {};
    const out = {};
    if (!spec.presentCall) {
        out.presentCall = (args) => {
            try {
                const a = args && typeof args === 'object' ? args : {};
                const view = { card: 'generic', title: card.title(a), kind: card.kind };
                const code = card.code ? card.code(a) : null;
                if (code)
                    view.content = [{ type: 'text', text: cardCodeSection(code) }];
                return view;
            }
            catch (e) {
                return undefined;
            }
        };
    }
    if (!spec.presentResult) {
        out.presentResult = (args, result) => {
            try {
                if (!result || result.isError || !Array.isArray(result.content))
                    return undefined;
                const texts = result.content.filter((b) => b && b.type === 'text');
                const human = texts.length > 1 && isEnvelopeBlock(texts[0]) ? texts.slice(1) : texts;
                const a = args && typeof args === 'object' ? args : {};
                const code = card.code ? card.code(a) : null;
                // 没有信封、也没有代码 ⇒ 原始回执本来就是人读文本，交给宿主照常显示
                if (human.length === texts.length && !code)
                    return undefined;
                // 一个 text block：dsh-TUI 拼接多个 text block 时不加换行
                const parts = human.map((b) => String(b.text || '').replace(/\s+$/, '')).filter(Boolean);
                if (code)
                    parts.push(cardCodeSection(code));
                const content = parts.length ? [{ type: 'text', text: parts.join(String.fromCharCode(10, 10)) }] : [];
                for (const b of result.content)
                    if (b && b.type !== 'text')
                        content.push(b);
                return { card: 'generic', content: content };
            }
            catch (e) {
                return undefined;
            }
        };
    }
    return out;
}
/**
 * v0.9.3（D1/F3）：客户端单次等待上限。超过它就**不干等**，转作业层并回 jobId（对齐 bash 的 promoted 语义）。
 * 默认 100 s：外层 run_code 的默认 deadline 是 120 s，留 20 s 余量给回执与状态查询（promoted 路径只是一次很快的 /job 查询）。
 * 调大它 = 允许单次同步等更久（代价：外层 deadline 到点时更容易"结果在服务端、客户端拿不到"）。
 */
//# sourceMappingURL=cards.js.map