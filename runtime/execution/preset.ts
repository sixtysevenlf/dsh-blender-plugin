// 配方库 API 调用（T6）
// @imports
import { extractLoop } from "../core/output.js";
import { KERNEL_BOOTSTRAP } from "../core/kernel.js";


export interface PresetDeps {
  addon: import("../transport/probes.js").AddonClient;
  ensurePreset: () => Promise<unknown>;
}

export function createPresetApi(E: PresetDeps) {
  const { addon, ensurePreset } = E;
  /** 配方库：save / list / get / delete / apply / export / import / help */
  async function presetCall(op: unknown, payload: unknown): Promise<unknown> {
    await ensurePreset();
    const body = 'print("LOOP " + K.dsh_preset_api["dispatch"](' + JSON.stringify(String(op || 'list')) + ', _json.dumps(_json.loads('
      + JSON.stringify(JSON.stringify(payload || {})) + '))))';
    return extractLoop(await addon.send('execute_code', { code: KERNEL_BOOTSTRAP + '\nimport json as _json\n' + body }, 300000));
  }
  return { presetCall };
}
