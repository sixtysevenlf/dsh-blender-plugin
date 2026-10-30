import { vTool } from '../contracts/tool.js';
import { ANY_SCHEMA } from './schema-diagnostic.js';
import { T, BACKEND_HINT } from '../constants.js';
import { ensureBackend, sleep } from '../host/backend.js';
import { backendPost, fetchFrame, leasedText } from '../host/http.js';
import { toAttachment, readLocalPng } from '../utils/image.js';
import { renderOne } from '../ui/cards.js';
async function watchViaWorker(ctx, port, args, session) {
    const size = Math.max(120, Math.min(1600, Number((args && args.max_size) || 420)));
    const spec = {
        width: size,
        height: Math.max(120, Math.round((size * 9) / 16)),
        seconds: Math.max(0.2, Math.min(20, Number((args && args.seconds) || 2))),
        fps: Math.max(0.5, Math.min(8, Number((args && args.fps) || 4))),
        code: args && args.code ? String(args.code) : undefined
    };
    const nm = session ? String(session) : 'watch';
    try {
        await backendPost(port, '/worker', { op: 'start', name: nm, engine: 'workbench', warmup: true, isolated: true }, 120000);
        const r = await backendPost(port, '/worker', { op: 'watch', name: nm, spec: spec, isolated: true }, 180000);
        const res = (r && r.result) || {};
        if (!r || r.ok !== true || res.ok !== true)
            return { text: 'WATCH 无头失败 · ' + String((r && (r.error || res.error)) || 'unknown') };
        const fr = res.frames || [];
        const hashes = fr.filter((x) => x && x.ok).map((x) => x.hash);
        const distinct = new Set(hashes).size;
        const parts = [];
        if (res.code)
            parts.push('code: ' + (res.code.ok ? 'ok' : '失败') + (res.code.error ? ' · ' + String(res.code.error) : ''));
        parts.push('WATCH(headless) ' +
            String(res.sampled || fr.length) +
            ' 帧 · 不同画面 ' +
            String(distinct) +
            '/' +
            String(hashes.length) +
            ' · ' +
            String(res.ms || 0) +
            'ms' +
            (res.static_render ? ' · 无动画帧范围，静态重复采样' : ''));
        parts.push('timeline: ' +
            fr
                .slice(0, 16)
                .map((x) => String(x.frame) + ':' + String(x.hash))
                .join(' '));
        const images = [];
        let whyNot = '';
        const picks = Array.isArray(res.picks) ? res.picks.slice(0, 6) : [];
        for (let i = 0; i < picks.length; i++) {
            const p = picks[i];
            let data = null;
            if (p.png_b64) {
                try {
                    data = Buffer.from(String(p.png_b64), 'base64');
                }
                catch (e) {
                    data = null;
                }
            }
            if (!data && p.path)
                data = readLocalPng(p.path);
            if (!data) {
                if (!whyNot)
                    whyNot = '无 png_b64/path';
                continue;
            }
            const att = await toAttachment(ctx, data, 'watch-hl-' + String(p.frame) + '-' + String(p.hash) + '.png');
            if (att.ref)
                images.push(att.ref);
            else if (!whyNot)
                whyNot = att.why || '未知原因';
        }
        parts.push('附帧：' + String(images.length) + ' 张' + (whyNot ? ' · ⚠ ' + whyNot : ''));
        return images.length ? { text: parts.join(String.fromCharCode(10)), images: images } : { text: parts.join(String.fromCharCode(10)) };
    }
    catch (e) {
        return { text: 'WATCH 无头异常 · ' + String((e && e.message) || e) };
    }
}
export function registerWatch(ctx, port) {
    ctx.effect(() => ctx.tools.register(vTool({
        name: 'blender_rt_watch',
        description: '观察时间窗/帧序列：先跑 code，再采样最多 6 帧 + 逐帧 hash（判断动画/修改器有没有动）；gui 不可用时自动走无头 worker。',
        parameters: {
            code: { type: 'string', description: '可选：观察前先执行的 Python' },
            seconds: { type: 'number', description: '观察时长，默认 2 秒（0.2-20）' },
            fps: { type: 'number', description: '采样率，默认 4（0.5-8）' },
            max_size: { type: 'integer', description: '帧最长边像素，默认 420' },
            session: { type: 'string', description: '无头 worker 会话（不给则 GUI 失败自动降级）' }
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
            if (args && args.session)
                return await watchViaWorker(ctx, port, args, String(args.session));
            if (args && args.code) {
                const r = await backendPost(port, '/act', { code: String(args.code) }, 180000);
                const lt = leasedText(r);
                parts.push(lt
                    ? lt
                    : r && r.ok
                        ? 'ACT ok · ' + String(r.ms) + 'ms' + (r.stdout ? ' · stdout: ' + String(r.stdout).trim().slice(0, 600) : '')
                        : 'ACT 失败 · ' + String((r && (r.error || r.raw)) || ''));
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
            if (shots.length && shots.every((s) => s.hash === 'ERR')) {
                const fb = await watchViaWorker(ctx, port, args, undefined);
                if (fb && fb.text)
                    return fb;
            }
            const hashes = shots.map((s) => s.hash);
            const distinct = Array.from(new Set(hashes)).length;
            const avgMs = Math.round(shots.reduce((a, s) => a + Math.max(0, s.ms), 0) / Math.max(1, shots.length));
            parts.push('WATCH ' +
                String(seconds) +
                's @' +
                String(fps) +
                'fps → ' +
                String(shots.length) +
                ' 帧 · 平均 ' +
                String(avgMs) +
                'ms/帧 · 不同画面 ' +
                String(distinct) +
                '/' +
                String(hashes.length));
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
            parts.push('附帧：' + String(images.length) + ' 张（均匀抽取）' + (whyNot ? ' · ⚠ 有帧未回传：' + whyNot : ''));
            const out = { text: parts.join('\n') };
            if (images.length)
                out.images = images;
            return out;
        }
    })), '@dsh-external/dsh-blender-plugin: rt-watch');
}
//# sourceMappingURL=watch.js.map