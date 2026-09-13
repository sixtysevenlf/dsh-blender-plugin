import { AddonClient } from './engine.mjs';
const a = new AddonClient();
const out = {};
const tryCall = async (name, params = {}, timeout = 20000) => {
  const t0 = Date.now();
  try {
    const r = await a.send(name, params, timeout);
    out[name] = { ok: true, ms: Date.now() - t0, r: JSON.stringify(r).slice(0, 150) };
  } catch (e) {
    out[name] = { ok: false, ms: Date.now() - t0, err: String(e && e.message || e).slice(0, 110) };
  }
};
await tryCall('ping');
await tryCall('get_addon_info');
await tryCall('get_scene_info', {});
await tryCall('get_world_state_snapshot', {});
await tryCall('get_object_info', { name: 'Cube' });
await tryCall('get_telemetry_consent');
await tryCall('get_polyhaven_status');
await tryCall('get_hyper3d_status');
await tryCall('get_sketchfab_status');
await tryCall('get_polypizza_status');
await tryCall('get_hunyuan3d_status');
await tryCall('get_polyhaven_categories', { asset_type: 'hdris' }, 25000);
const code = [
  'import bpy, json',
  's = bpy.context.scene',
  'keys = ("blendermcp_use_polyhaven","blendermcp_use_hyper3d","blendermcp_use_sketchfab","blendermcp_use_polypizza","blendermcp_use_hunyuan3d")',
  'print(json.dumps({k: getattr(s, k, None) for k in keys}))',
].join('\n');
await tryCall('execute_code', { code });
console.log(JSON.stringify(out, null, 1));
a.close();
