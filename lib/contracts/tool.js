import { createRequire } from 'node:module';
import { defineTool } from '@deepseek-ai/dsh-tools';
import { PLUGIN_VERSION } from '../constants.js';
import { EXEC_CTX } from '../host/exec-context.js';
import { losslessSanitize } from './lossless.js';
import { cardPresenters } from '../ui/cards.js';
export const vTool = (spec) => defineTool({
    ...spec,
    // v2.6.1：工具卡片（只管宿主 UI 怎么显示，不改模型侧回执；见 cardPresenters）
    ...cardPresenters(spec),
    description: '[v' + PLUGIN_VERSION + '] ' + String((spec && spec.description) || ''),
    // v0.9.4（P0 返回通道）：所有工具**唯一出口**都过一遍 losslessSanitize ——
    // 一个字段写错（NaN / undefined / -0）不该让整条工具通道变成不可用。
    execute: async (args, exec) => {
        const raw = await EXEC_CTX.run(exec || {}, () => spec.execute(args, exec));
        const { value, fixes } = losslessSanitize(raw);
        if (fixes.length) {
            const note = '⚠️ 回执已消毒 ' +
                String(fixes.length) +
                ' 处非 lossless-JSON 值（宿主门会拒收 undefined/NaN/Infinity/-0）：' +
                fixes.slice(0, 6).join(' · ') +
                (fixes.length > 6 ? ' …等' : '');
            try {
                if (value && typeof value === 'object' && typeof value.text === 'string')
                    value.text += String.fromCharCode(10) + note;
                else if (value && typeof value === 'object')
                    value.__losslessNote = note;
                console.warn('[dsh-blender] ' + note);
            }
            catch (e) {
                /* 提示失败不影响返回 */
            }
        }
        return value;
    }
});
/**
 * schemastery 的包名在不同 DSH 版本间是**有 scope / 无 scope 两种形态**（`schemastery` ↔
 * `@deepseek-ai/schemastery`；cordis 同理），静态 import 无法条件化，只能运行期解析。
 *
 * v1.0.1+S11（issue #10 修复）—— 解析顺序与失败语义都改了：
 *   ① **顺序**：ESM `await import()` 优先，失败再退回 `createRequire`。
 *      旧版只走 createRequire：在"宿主只提供带 scope 的名字"的 profile 里，该 require 在
 *      多插件共存时的 DSH module-resolution 拦截下解析失败（现场 3/3 复现，见 issue #10 的
 *      bundle 二分表），而 ESM loader 正是宿主自己走的那条路 —— 所以先试它。
 *   ② **失败语义**：顶层不再 `throw`。旧版在模块载入期抛错 ⇒ 宿主只回一句 `failed to import`
 *      （真因被吞）、entry 的 fiber 始终 undefined ⇒ **15 个工具整体消失、后端也不起**，
 *      且现象与真因不在同一层，极难定位。现在改为**降级加载**：注册一个只回报原因的占位工具
 *      （见下面 DEGRADED 分支），其余工具不注册，但报错可读、插件不会消失。
 *   ③ **诊断**：走了哪条通道、试过哪些名字，进 __internals.schemaResolve，并在 op=status /
 *      op=doctor 里可见（这类"静默整体消失"必须留下可读的现场）。
 */
const SM_NAMES = ['@deepseek-ai/schemastery', 'schemastery'];
export const schemaResolve = { tried: [] };
/** 只取错误的首行 + 截断：诊断要能一行说完，不要把整个 stack 塞进状态输出 */
const smErrLine = (e) => String((e && e.code) || (e && e.message) || e)
    .split('\n')[0]
    .slice(0, 120);
/** 形态判定：能拿到带 `.object()` 的那一层才算可用（拿不到就是不认，不赌） */
const smPick = (m) => {
    for (const c of [m && m.default, m])
        if (c && typeof c.object === 'function')
            return c;
    return null;
};
for (const id of SM_NAMES) {
    try {
        const m = await import(/* webpackIgnore: true */ id);
        const hit = smPick(m);
        if (hit) {
            schemaResolve.z = hit;
            schemaResolve.via = 'esm:' + id;
            break;
        }
        schemaResolve.tried.push(id + '(esm:no-object)');
    }
    catch (e) {
        schemaResolve.tried.push(id + '(esm:' + smErrLine(e) + ')');
    }
}
if (!schemaResolve.z) {
    try {
        const req = createRequire(import.meta.url);
        for (const id of SM_NAMES) {
            try {
                const m = req(id);
                const hit = smPick(m);
                if (hit) {
                    schemaResolve.z = hit;
                    schemaResolve.via = 'cjs:' + id;
                    break;
                }
                schemaResolve.tried.push(id + '(cjs:no-object)');
            }
            catch (e) {
                schemaResolve.tried.push(id + '(cjs:' + smErrLine(e) + ')');
            }
        }
    }
    catch (e) {
        schemaResolve.tried.push('createRequire(' + smErrLine(e) + ')');
    }
}
if (!schemaResolve.z) {
    schemaResolve.error =
        'schemastery 解析失败，已尝试：' +
            schemaResolve.tried.join(' / ') +
            '。宿主需提供 schemastery —— 有 scope 的 @deepseek-ai/schemastery 或无 scope 的 schemastery 任一即可。';
    console.error('[dsh-blender] ' + schemaResolve.error + '（降级加载：只注册一个诊断占位工具，不会再出现"15 个工具整体消失且真因被吞"）');
}
export const z = schemaResolve.z;
//# sourceMappingURL=tool.js.map