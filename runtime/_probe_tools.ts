import { AddonClient, GATE_FLAGS } from './engine.js';
import { errorMessage } from "./core/json.js";
const a = new AddonClient();
const out: Record<string, { ok: boolean; ms: number; r?: string; err?: string }> = {};
const tryCall = async (name: string, params: Record<string, unknown> = {}, timeout = 20000): Promise<void> => {
  const t0 = Date.now();
  try {
    const r = await a.send(name, params, timeout);
    out[name] = { ok: true, ms: Date.now() - t0, r: JSON.stringify(r).slice(0, 150) };
  } catch (e) {
    out[name] = { ok: false, ms: Date.now() - t0, err: errorMessage(e).slice(0, 110) };
  }
};
await tryCall('ping');
await tryCall('get_addon_info');
await tryCall('get_scene_info', {});
await tryCall('get_world_state_snapshot', {});
await tryCall('get_object_info', { name: 'Cube' });
await tryCall('get_activity_state');
await tryCall('get_selection');
await tryCall('get_telemetry_consent');
await tryCall('get_polyhaven_status');
await tryCall('get_hyper3d_status');
await tryCall('get_sketchfab_status');
await tryCall('get_polypizza_status');
await tryCall('get_hunyuan3d_status');
await tryCall('get_polyhaven_categories', { asset_type: 'hdris' }, 25000);
// 5 个集成开关：只读本仓 addon 的 dshbb_use_*（v2.3 起不再兼容旧 addon）
const code = [
  'import bpy, json',
  's = bpy.context.scene',
  'names = ' + JSON.stringify(GATE_FLAGS),
  'out = {}',
  'for n in names:',
  '    out[n] = bool(getattr(s, n, None))',
  'print(json.dumps(out))',
].join('\n');
await tryCall('execute_code', { code });
console.log(JSON.stringify(out, null, 1));
a.close();
