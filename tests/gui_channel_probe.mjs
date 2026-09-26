/**
 * GUI 通道覆盖探针 —— 需要**真机**：Blender GUI 正在跑 + `MCP for Blender` addon 已 Connect
 * （macOS 上一条命令即可拉起：见 README §2 与 blender_viewport op=launch）。
 *
 *   node tests/gui_channel_probe.mjs
 *
 * 跑不了也没关系：addon 不在时每条都会明确 FAIL，不会假装成功。
 * 覆盖通道命令、取帧、自定义视角、持久内核 K、命令透传、perf/opt 预设。
 */
import { createEngine } from '../runtime/engine.mjs';
const R = (p, ms, l) => Promise.race([p, new Promise((_, rj) => setTimeout(() => rj(new Error(l+' TIMEOUT')), ms))]);
const e = createEngine();
const results = [];
const step = async (name, fn, ms = 60000) => {
  const t0 = Date.now();
  try { const r = await R(fn(), ms, name); results.push([name, 'ok', Date.now()-t0, r]); return r; }
  catch (err) { results.push([name, 'FAIL', Date.now()-t0, String(err.message).slice(0,120)]); return null; }
};

await step('viewport op=doctor',  () => e.doctor(), 20000);
await step('viewport op=status',  () => e.status(), 20000);
await step('rt_see (frame)',      () => e.frame(480), 40000);
await step('rt_see (custom view)',() => e.view({ from: [9,-9,6], look_at: [0,0,1], max_size: 400 }), 90000);
await step('rt_do (mutate)',      () => e.act("K.hits = getattr(K,'hits',0)+1\nprint('hits', K.hits)"), 30000);
await step('rt_do (kernel K)',    () => e.act("print('K.hits =', K.hits)"), 30000);
await step('rt_cmd (passthru)',   () => e.cmd('get_scene_info', {}), 20000);
await step('rt_commands',         () => e.commands(), 30000);
await step('rt_perf status',      () => e.perf('status'), 30000);
await step('rt_opt analyze',      () => e.opt('analyze'), 40000);
await step('viewport op=who',     () => e.cmd('execute_code', { code: 'print(1)' }), 20000);

console.log('\n=== GUI 通道覆盖（macOS + Blender 5.2.2 + MCP for Blender 1.7）===');
for (const [n, s, ms, r] of results) {
  const extra = s === 'ok' ? JSON.stringify(r).slice(0, 70) : r;
  console.log('%s %s %sms  %s', s === 'ok' ? '✓' : '✗', n.padEnd(20), String(ms).padStart(6), extra);
}
const pass = results.filter(r => r[1] === 'ok').length;
console.log('\n%d / %d passed', pass, results.length);
process.exit(pass === results.length ? 0 : 1);
