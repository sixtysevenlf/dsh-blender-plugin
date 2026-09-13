/**
 * addon 协议适配层 —— 让直连通道同时支持两种 Blender 侧 addon 实现。
 *
 * 背景：直连通道原先假定 addon 是 ahujasid 血统的 `MCP for Blender`（README §2 的必需项）。
 * 但 Blender 侧实际还存在另一种广泛使用的实现 —— harveyxiacn 增强版 `blender_mcp_addon`，
 * 它的**分帧方式与命令词汇都不同**，直接连会表现为"端口通、addon 也活着，但命令全部超时"，
 * 排查成本很高。本模块把两者的差异收敛成两个协议描述符，由 `addonProtocol` 配置项选择，
 * 默认 `auto` 自动探测。
 *
 * 两种协议的真实差异（实测）：
 *
 *   ahujasid（MCP for Blender v1.6）
 *     请求  {"type":"get_scene_info","params":{}}          ← 无分帧
 *     应答  {"status":"success","result":{…}} / {"status":"error","message":"…"}
 *     词汇  扁平名（ping / execute_code / get_viewport_screenshot …）
 *
 *   category-action（harveyxiacn blender_mcp_addon）
 *     请求  {"id":"1","type":"command","category":"scene","action":"get_info","params":{}}\n
 *     应答  {"id":"1","success":true,"data":{…}}\n
 *     词汇  category.action（system.get_info / utility.execute_python …）
 *
 * 适配面很小：engine.mjs 实际只用到 2 个 addon 命令 + 1 个体检命令
 * （`execute_code` / `get_viewport_screenshot` / `ping`），其余是 `blender_rt_cmd` 的透传。
 * 其中 `get_viewport_screenshot` 与 `get_world_state_snapshot` 在 category-action 侧没有
 * 等价 handler（同名 handler 的参数与 API 都不兼容），改为由本模块生成一段 python，
 * 借对面的 `utility.execute_python` 执行。
 */

import net from 'node:net';

const INTEGRATION_STUB = (name) => ({
  enabled: false,
  message: '当前 addon 未提供 ' + name + ' 集成（直连通道该集成不可用）',
});

/** category-action 侧缺失的资产集成命令 → 明确报错，不假装成功 */
const ASSET_COMMANDS = {
  get_polyhaven_categories: 'PolyHaven', search_polyhaven_assets: 'PolyHaven', download_polyhaven_asset: 'PolyHaven', set_texture: 'PolyHaven',
  create_rodin_job: 'Hyper3D Rodin', poll_rodin_job_status: 'Hyper3D Rodin', import_generated_asset: 'Hyper3D Rodin',
  search_sketchfab_models: 'Sketchfab', get_sketchfab_model_preview: 'Sketchfab', download_sketchfab_model: 'Sketchfab',
  search_polypizza_models: 'Poly Pizza', download_polypizza_model: 'Poly Pizza',
  create_hunyuan_job: 'Hunyuan3D', poll_hunyuan_job_status: 'Hunyuan3D', import_generated_asset_hunyuan: 'Hunyuan3D',
};
const STATUS_COMMANDS = ['get_polyhaven_status', 'get_hyper3d_status', 'get_sketchfab_status', 'get_polypizza_status', 'get_hunyuan3d_status'];

/**
 * 把一次扁平的通道调用翻译成 category/action。
 * @returns {{local:()=>any} | {wire:{category,action,params}, post?:(data:any)=>any}}
 */
function planCategoryAction(type, params) {
  const p = params || {};
  switch (type) {
    case 'ping':
      return { wire: { category: 'system', action: 'get_info', params: {} }, post: (d) => ({ pong: true, blender: d && d.version_string }) };
    case 'get_scene_info':
      return { wire: { category: 'scene', action: 'get_info', params: {} } };
    case 'get_object_info':
      return { wire: { category: 'object', action: 'get_info', params: { name: p.name !== undefined ? p.name : p.object_name } } };
    case 'get_addon_info':
      return {
        wire: { category: 'system', action: 'get_info', params: {} },
        post: (d) => ({ name: 'blender_mcp_addon (harveyxiacn)', protocol: 'category-action', blender: d && d.version_string, filepath: d && d.filepath, scene: d && d.scene }),
      };
    case 'execute_code': {
      if (typeof p.code !== 'string') throw new Error('execute_code 需要 code 参数');
      return {
        wire: { category: 'utility', action: 'execute_python', params: { code: p.code } },
        // 通道侧统一按 {executed, result:"<stdout>"} 取正文（/act 的 stdout、LOOP 标记解析都依赖它）
        post: (d) => {
          const out = d && typeof d.output === 'string' ? d.output : '';
          const err = d && typeof d.stderr === 'string' ? d.stderr : '';
          return { executed: true, result: err ? out + '\n--- stderr ---\n' + err : out };
        },
      };
    }
    case 'get_viewport_screenshot': {
      const filepath = p.filepath || p.output_path;
      if (!filepath) throw new Error('get_viewport_screenshot 需要 filepath 参数');
      return {
        wire: { category: 'utility', action: 'execute_python', params: { code: pyViewportShot(filepath, p.max_size) } },
        post: (d) => {
          const out = (d && d.output) || '';
          const m = String(out).match(/VIEW_OK (\S+) (\d+)/);
          if (!m) throw new Error('视口帧失败: ' + String(out).slice(-300));
          return { filepath: filepath, bytes: Number(m[2]), format: p.format || 'png', method: 'category-action:viewport-opengl' };
        },
      };
    }
    case 'get_world_state_snapshot':
      return {
        wire: { category: 'utility', action: 'execute_python', params: { code: pySnapshot() } },
        post: (d) => {
          const snap = jsonAfterMarker((d && d.output) || '', 'SNAP ');
          if (!snap) throw new Error('快照解析失败: ' + String((d && d.output) || '').slice(-300));
          return snap;
        },
      };
    case 'get_telemetry_consent':
      return { local: () => ({ consent: false }) };
    case 'set_telemetry_consent':
      return { local: () => ({ ok: true, consent: !!p.consent, note: '适配层桩：当前 addon 无遥测开关' }) };
    case 'drain_human_activity':
      return { local: () => ({ events: [], note: '适配层桩：当前 addon 无人类操作事件缓冲' }) };
  }
  if (STATUS_COMMANDS.includes(type)) return { local: () => INTEGRATION_STUB(type.replace('get_', '').replace('_status', '')) };
  if (ASSET_COMMANDS[type]) throw new Error('当前 addon 无 ' + ASSET_COMMANDS[type] + ' 集成通道：' + type + ' 不可用（可用 blender_rt_do 自行实现）');
  throw new Error('当前 addon 不支持命令: ' + type + '（category-action 协议只实现了通道自用的命令集）');
}

/** 协议描述符：encode/decode/plan 三件事，AddonClient 与 freshPing 共用 */
export const PROTOCOLS = {
  ahujasid: {
    id: 'ahujasid',
    label: 'ahujasid / MCP for Blender v1.6（扁平协议，无分帧）',
    framing: 'none',
    panelHint: '「MCP for Blender」面板 → Connect',
    plan(type, params) { return { wire: { type: type, params: params || {} } }; },
    encode(wire) { return JSON.stringify(wire); },
    decode(raw) {
      const o = JSON.parse(raw);
      if (o.status === 'error') return { ok: false, message: String(o.message || 'addon error') };
      return { ok: true, data: o.result === undefined ? {} : o.result };
    },
  },
  'category-action': {
    id: 'category-action',
    label: 'harveyxiacn blender_mcp_addon（category/action，换行分帧）',
    framing: 'newline',
    panelHint: '「MCP for Blender」/ 该 addon 自己的面板 → Connect（两者监听同一端口，注意别同时开两个 addon）',
    plan: planCategoryAction,
    encode(wire, ctx) {
      const o = ctx || {};
      return JSON.stringify({
        id: o.id || 'rt1',
        type: 'command',
        category: wire.category,
        action: wire.action,
        params: Object.assign({}, wire.params, o.timeoutMs ? { timeout: Math.max(1, Math.min(3600, Math.ceil(o.timeoutMs / 1000))) } : {}),
      }) + '\n';
    },
    decode(raw) {
      const o = JSON.parse(raw);
      if (o.success === false) {
        const e = o.error || {};
        return { ok: false, message: String(e.message || e.code || 'addon error') };
      }
      return { ok: true, data: o.data === undefined ? {} : o.data };
    },
  },
};

export const PROTOCOL_IDS = Object.keys(PROTOCOLS);

/**
 * 探测一次：发一条 ping，只看"有没有回包"，回复内容交给调用方判形状。
 * 判据故意放宽成"回什么都不重要"——对面只要能解析我们的 JSON 并回一个 JSON，就证明它用的是这套协议
 * （例如 ahujasid 侧不一定有 ping 这个命令名，它会回 {"status":"error","message":"Unknown command type: ping"}，
 *  这同样是"讲扁平协议"的确证）。
 * @returns {Promise<object|null>} 解析出来的回包；没回包 → null
 */
function probeOnce(proto, host, port, timeoutMs) {
  return new Promise((resolve) => {
    const s = net.connect({ host, port });
    let buf = '';
    let settled = false;
    const done = (v) => { if (settled) return; settled = true; clearTimeout(t); try { s.destroy(); } catch (e) {} resolve(v); };
    const t = setTimeout(() => done(null), timeoutMs);
    s.once('connect', () => {
      try {
        const plan = proto.plan('ping', {});
        s.write(proto.encode(plan.wire, { id: 'probe', timeoutMs: timeoutMs }));
      } catch (e) { done(null); }
    });
    s.once('error', () => done(null));
    s.on('data', (d) => {
      buf += d.toString('utf8');
      try { done(JSON.parse(buf.trim())); } catch (e) { /* 继续攒 */ }
    });
  });
}

/** 扁平封套 {status, result|message}（category/action 的是 {success, data|error}） */
function isFlatEnvelope(o) {
  return !!o && typeof o === 'object' && !('success' in o) && ('status' in o || 'result' in o);
}

/**
 * 自动探测 addon 用哪种协议。
 *   ① 先发**不带换行**的扁平 ping：category-action 的 addon 等不到换行，必然静默 → 有回包就一定是扁平 addon；
 *   ② 再发带换行的 category/action ping —— 若对面其实是"上一轮忙没回"的扁平 addon，它会回扁平封套，
 *      按封套形状仍能纠正回 ahujasid（不让一次忙导致的超时把协议判错）。
 * @returns {Promise<{id:string, ms:number}|null>} null = 两种都没回包（addon 不在/不可用）
 */
export async function detectProtocol(host, port, timeoutMs = 1000) {
  const t0 = Date.now();
  const flatReply = await probeOnce(PROTOCOLS.ahujasid, host, port, timeoutMs);
  if (flatReply) return { id: 'ahujasid', ms: Date.now() - t0, via: 'flat-probe' };
  const caReply = await probeOnce(PROTOCOLS['category-action'], host, port, timeoutMs);
  if (!caReply) return null;
  if (isFlatEnvelope(caReply)) return { id: 'ahujasid', ms: Date.now() - t0, via: 'native-probe-flat-envelope' };
  return { id: 'category-action', ms: Date.now() - t0, via: 'native-probe' };
}

/** 解析结果缓存（进程级）：探测一次就记住，避免每条命令都探 */
const cache = new Map();

/** 测试用：清掉缓存 */
export function resetProtocolCache() { cache.clear(); }

/**
 * 得到实际要用的协议描述符。
 * @param {{host:string, port:number, prefer?:string, timeoutMs?:number}} opts
 *        prefer = 'auto' | 'ahujasid' | 'category-action'（通常来自 CFG.addonProtocol）
 * @returns {Promise<{proto:object, from:'config'|'auto'|'fallback', detected?:object}>}
 */
export async function resolveProtocol(opts = {}) {
  const host = opts.host || '127.0.0.1';
  const port = Number(opts.port) || 9876;
  const prefer = opts.prefer || 'auto';
  const key = host + ':' + port + '/' + prefer;
  if (cache.has(key)) return cache.get(key);

  // 显式指定：不探测，直接信任（探测失败也不回退，否则用户无法强制某一种）
  if (prefer !== 'auto') {
    const proto = PROTOCOLS[prefer];
    if (!proto) throw new Error('未知 addonProtocol: ' + prefer + '（可选 ' + PROTOCOL_IDS.join(' / ') + ' / auto）');
    const r = { proto: proto, from: 'config' };
    cache.set(key, r);
    return r;
  }

  const detected = await detectProtocol(host, port, opts.timeoutMs || 1000);
  const r = detected
    ? { proto: PROTOCOLS[detected.id], from: 'auto', detected: detected }
    : { proto: PROTOCOLS.ahujasid, from: 'fallback' };   // 没探到：按默认协议走，让后续报错保持原样
  cache.set(key, r);
  return r;
}

/* ───────────── 自建 python 载荷（借对面 utility.execute_python 执行） ───────────── */

/** 紧凑世界状态快照（对齐扁平协议 get_world_state_snapshot 的语义） */
export function pySnapshot() {
  return [
    'import bpy, json',
    'sc = bpy.context.scene',
    'objs = []',
    'all_objs = sorted(sc.objects, key=lambda o: o.name)',
    'for o in all_objs[:2000]:',
    '    rot = getattr(o, "rotation_euler", None)',
    '    loc = getattr(o, "location", None)',
    '    scl = getattr(o, "scale", None)',
    '    try:',
    '        vis = bool(o.visible_get())',
    '    except Exception:',
    '        vis = True',
    '    mats = [s.material.name for s in getattr(o, "material_slots", []) if s.material]',
    '    objs.append({',
    '        "name": o.name, "type": o.type,',
    '        "location": [round(float(v), 3) for v in loc] if loc else None,',
    '        "rotation": [round(float(v), 3) for v in rot] if rot else None,',
    '        "scale": [round(float(v), 3) for v in scl] if scl else None,',
    '        "visible": vis, "materials": mats,',
    '    })',
    'snap = {',
    '    "name": sc.name,',
    '    "object_count": len(sc.objects),',
    '    "objects_listed": len(objs),',
    '    "objects_truncated": len(all_objs) > 2000,',
    '    "selected": [o.name for o in bpy.context.selected_objects],',
    '    "frame_current": sc.frame_current,',
    '    "frame_start": sc.frame_start,',
    '    "frame_end": sc.frame_end,',
    '    "fps": round(float(sc.render.fps) / float(sc.render.fps_base or 1), 3),',
    '    "active_camera": sc.camera.name if sc.camera else None,',
    '    "materials_count": len(bpy.data.materials),',
    '    "blender_version": bpy.app.version_string,',
    '    "snapshot_source": "category-action-adapter",',
    '    "objects": objs,',
    '}',
    'print("SNAP " + json.dumps(snap, ensure_ascii=False))',
  ].join('\n');
}

/**
 * 视口帧：写 PNG 到 filepath，长边缩到 max_size。
 * 不用对面同名 handler —— 它的参数是 output_path/width/height，且用的是 Blender 4 时代的
 * `bpy.ops.render.opengl(override, write_still=True)` 老 API，在 5.x 下会失效。
 * 这里三级降级：opengl(view_context) → opengl → screen.screenshot_area。
 */
export function pyViewportShot(filepath, maxSize) {
  const pLit = JSON.stringify(String(filepath));
  const mx = String(Math.max(64, Math.min(4096, Number(maxSize) || 560)));
  return [
    'import bpy, os',
    '_p = ' + pLit,
    '_mx = ' + mx,
    '_d = os.path.dirname(_p)',
    'if _d and not os.path.isdir(_d):',
    '    os.makedirs(_d, exist_ok=True)',
    '_pre = os.path.getmtime(_p) if os.path.exists(_p) else -1.0',
    '_sc = bpy.context.scene',
    '_ow, _oh, _op = _sc.render.resolution_x, _sc.render.resolution_y, _sc.render.resolution_percentage',
    '_ofmt = _sc.render.image_settings.file_format',
    '_area = None',
    'for _a in bpy.context.screen.areas:',
    '    if _a.type == "VIEW_3D":',
    '        _area = _a',
    '        break',
    '_region = None',
    '_win = None',
    'if _area is not None:',
    '    for _r in _area.regions:',
    '        if _r.type == "WINDOW":',
    '            _region = _r',
    '            break',
    '    for _w in bpy.context.window_manager.windows:',
    '        if _w.screen == bpy.context.screen:',
    '            _win = _w',
    '            break',
    '_w = _region.width if _region else _ow',
    '_h = _region.height if _region else _oh',
    '_s = min(1.0, float(_mx) / float(max(_w, _h)))',
    '_errs = []',
    '_fresh = lambda: os.path.exists(_p) and os.path.getmtime(_p) != _pre',
    'try:',
    '    _sc.render.resolution_x = max(1, int(_w * _s))',
    '    _sc.render.resolution_y = max(1, int(_h * _s))',
    '    _sc.render.resolution_percentage = 100',
    '    _sc.render.image_settings.file_format = "PNG"',
    '    _sc.render.filepath = _p',
    'except Exception as _e:',
    '    _errs.append("res:" + str(_e)[:70])',
    'if _area is not None and _win is not None:',
    '    try:',
    '        with bpy.context.temp_override(window=_win, area=_area, region=_region):',
    '            bpy.ops.render.opengl(write_still=True, view_context=True)',
    '    except Exception as _e:',
    '        _errs.append("opengl-view:" + str(_e)[:70])',
    'if not _fresh() and _area is not None and _win is not None:',
    '    try:',
    '        with bpy.context.temp_override(window=_win, area=_area, region=_region):',
    '            bpy.ops.render.opengl(write_still=True)',
    '    except Exception as _e:',
    '        _errs.append("opengl:" + str(_e)[:70])',
    'if not _fresh() and _area is not None and _win is not None:',
    '    try:',
    '        with bpy.context.temp_override(window=_win, area=_area, region=_region):',
    '            bpy.ops.screen.screenshot_area(filepath=_p)',
    '    except Exception as _e:',
    '        _errs.append("area:" + str(_e)[:70])',
    'try:',
    '    _sc.render.resolution_x, _sc.render.resolution_y, _sc.render.resolution_percentage = _ow, _oh, _op',
    '    _sc.render.image_settings.file_format = _ofmt',
    'except Exception:',
    '    pass',
    'if _fresh():',
    '    print("VIEW_OK " + _p + " " + str(os.path.getsize(_p)))',
    'else:',
    '    raise RuntimeError("视口帧未生成; " + " | ".join(_errs))',
  ].join('\n');
}

/** 从 stdout 里找出标记之后的 JSON 对象 */
export function jsonAfterMarker(text, marker) {
  const s = String(text);
  const i = s.indexOf(marker);
  if (i < 0) return null;
  const rest = s.slice(i + marker.length);
  const j = rest.indexOf('{');
  if (j < 0) return null;
  let depth = 0, inStr = false, esc = false;
  for (let k = j; k < rest.length; k++) {
    const c = rest[k];
    if (inStr) { if (esc) esc = false; else if (c === '\\') esc = true; else if (c === '"') inStr = false; continue; }
    if (c === '"') { inStr = true; continue; }
    if (c === '{') depth++;
    else if (c === '}') { depth--; if (depth === 0) { try { return JSON.parse(rest.slice(j, k + 1)); } catch { return null; } } }
  }
  return null;
}
