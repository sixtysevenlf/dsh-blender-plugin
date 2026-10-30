import { vTool } from '../contracts/tool.js';
import { ANY_SCHEMA } from './schema-diagnostic.js';
import { T } from '../constants.js';
import { ensureBackend } from '../host/backend.js';
import { backendPost, leasedText } from '../host/http.js';
import { renderOne } from '../ui/cards.js';
export function registerPreset(ctx, port) {
    ctx.effect(() => ctx.tools.register(vTool({
        name: 'blender_rt_preset',
        description: '【配方库】把「参数组合」变成可保存、可套用、可分发的资产：save / list / get / apply / delete / export / import / help。' +
            'data 用点路径表达：材质节点 {"inputs.Base Color": [1,0,0,1]}、对象属性 {"location": [0,0,1]}、场景设置 {"render.resolution_x": 640}。' +
            'apply 的 targets 用 MAT:材质名 / OBJ:对象名 / SCENE（逗号分隔）；不给 targets 只预览（dry_run），给了就实际写入并回报 applied/skipped/errors。' +
            'export/import 走单个 JSON bundle，便于把配方分享到别的机器或会话。',
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
            overwrite: { type: 'boolean', description: 'save/import 是否覆盖同名（默认 true）' }
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
                a.tags = String(args.tags)
                    .split(',')
                    .map((x) => x.trim())
                    .filter(Boolean);
            if (args && args.names)
                a.names = String(args.names)
                    .split(',')
                    .map((x) => x.trim())
                    .filter(Boolean);
            if (args && args.targets)
                a.targets = String(args.targets)
                    .split(',')
                    .map((x) => x.trim())
                    .filter(Boolean);
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
                parts.push('共 ' +
                    String(res.count) +
                    ' 个：' +
                    (res.presets || []).map((p) => p.name + '(' + p.kind + ',' + p.keys + '键)').join(' · '));
                parts.push('目录：' + String(res.dir || ''));
            }
            else if (op === 'apply') {
                const rr = res.report || {};
                parts.push('配方 ' +
                    String(res.preset) +
                    ' · 目标 ' +
                    JSON.stringify(res.targets) +
                    ' · 写入 ' +
                    String((rr.applied || []).length) +
                    ' 项');
                if ((rr.skipped || []).length)
                    parts.push('跳过：' + JSON.stringify(rr.skipped).slice(0, 300));
                if ((rr.errors || []).length)
                    parts.push('错误：' + JSON.stringify(rr.errors).slice(0, 300));
            }
            else
                parts.push(JSON.stringify(res).slice(0, 900));
            return { text: parts.join(String.fromCharCode(10)) };
        }
    })), '@dsh-external/dsh-blender-plugin: rt-preset');
}
//# sourceMappingURL=preset.js.map