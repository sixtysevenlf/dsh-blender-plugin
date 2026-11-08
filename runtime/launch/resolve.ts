// 启动解析：Windows 代理 / addon 名字提示 / 注入脚本
import { CFG } from '../config.js'
import { errorMessage } from '../core/json.js'

export interface LaunchOpts {
  waitMs?: unknown
  exe?: unknown
  file?: unknown
  force?: unknown
  interop?: unknown
  addonFile?: unknown
  addonModule?: unknown
  dryRun?: unknown
  [k: string]: unknown
}

export async function tryLaunchAgent(
  opts: LaunchOpts,
  steps: string[],
  base: Record<string, unknown>,
): Promise<Record<string, unknown> | null> {
  const url = String(CFG.launchAgentUrl || '')
  if (!url || url.toLowerCase() === 'off') return null
  if (opts.interop === true) return null
  try {
    const ac = new AbortController()
    const t = setTimeout(() => ac.abort(), 1500)
    const h = await fetch(url + '/health', { signal: ac.signal })
    clearTimeout(t)
    if (!h.ok) return null
  } catch (e) {
    return null
  }
  steps.push('Windows 启动代理在线：' + url + '（本次启动 0 次互操作调用）')
  const ac = new AbortController()
  const t = setTimeout(() => ac.abort(), 15000)
  try {
    const r = await fetch(url + '/launch', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({
        file: opts.file ? String(opts.file) : null,
        force: !!opts.force,
        blenderExe: opts.exe ? String(opts.exe) : null,
      }),
      signal: ac.signal,
    })
    const j = (await r.json()) as Record<string, unknown>
    steps.push(
      '代理已受理：already=' + String(!!j.already) + ' dispatched=' + String(!!j.dispatched),
    )
    return Object.assign({}, base, j, { via: 'windows-agent' })
  } catch (e) {
    steps.push('代理调用未确认（' + errorMessage(e) + '）—— 按已派发处理，不回退互操作以免双开')
    return Object.assign({}, base, {
      ok: false,
      already: false,
      dispatched: true,
      launched: true,
      listening: false,
      via: 'windows-agent',
    })
  } finally {
    clearTimeout(t)
  }
}
/**
 * addon 自动探测的**名字提示**：只认本仓 `DSH Blender Bridge`（模块 `dsh_blender_bridge`、前缀 `dshbb_*`）。
 * v2.3 起不再扫描/兼容旧 `MCP for Blender`（模块 `blender_mcp`）。
 * LAUNCH_BOOT_PY 里由 JSON.stringify 内联成**同一个**列表，`addonNameHit()` 是它的 JS 同口径镜像（测试用）。
 */
export const ADDON_NAME_HINTS = ['blender_bridge', 'dshbb', 'dsh_blender_bridge']

/** 与 LAUNCH_BOOT_PY 里 `_name_hit()` 同口径的 JS 判定（小写子串命中）——测试用 */
export function addonNameHit(name: unknown): boolean {
  const n = String(name || '').toLowerCase()
  return ADDON_NAME_HINTS.some((k) => n.includes(k))
}

/** 注进 Blender 的启动脚本源码（导出供自检断言"落盘的那份 Python 真的双名命中"） */

export const LAUNCH_BOOT_PY = [
  '"""DSH launch boot (v0.9.4): enable the Blender addon and start its socket server (GUI only)."""',
  'import importlib.util, json, os, sys',
  '',
  '# ---- DSH addon 名字提示：只认 dsh_blender_bridge / dshbb_*（旧 MCP for Blender 不再支持）----',
  '_HINTS = ' + JSON.stringify(ADDON_NAME_HINTS),
  'def _name_hit(name):',
  '    n = (name or "").lower()',
  '    return any(k in n for k in _HINTS)',
  '# ---- /DSH addon 名字提示 ----',
  '',
  '_res = {',
  '    "ok": False, "background": None, "enabled": [], "loaded_by": None, "server_running": False,',
  '    "addon_file": None, "errors": [], "addon_modules_seen": [],',
  '    "addon_module": None, "addon_version": None, "addon_protocol": None, "addon_ok": False,',
  '    "addon_issue": None,',
  '    "env": {"BLENDER_USER_SCRIPTS": os.environ.get("BLENDER_USER_SCRIPTS", ""),',
  '            "DSH_BLENDER_ADDON_FILE": os.environ.get("DSH_BLENDER_ADDON_FILE", ""),',
  '            "DSH_BLENDER_ADDON_MODULE": os.environ.get("DSH_BLENDER_ADDON_MODULE", "")},',
  '}',
  '',
  '',
  'def _flush():',
  '    p = os.environ.get("DSH_BLENDER_LAUNCH_STATUS", "")',
  '    if not p:',
  '        return',
  '    try:',
  '        d = os.path.dirname(p)',
  '        if d and not os.path.isdir(d):',
  '            os.makedirs(d, exist_ok=True)',
  '        with open(p, "w", encoding="utf-8") as fh:',
  '            json.dump(_res, fh, ensure_ascii=False, indent=1)',
  '    except Exception as exc:',
  '        _res["status_write_error"] = repr(exc)',
  '',
  '',
  'def _installed_addon_names():',
  '    names = []',
  '    try:',
  '        import addon_utils',
  '        for mod in addon_utils.modules():',
  '            n = getattr(mod, "__name__", "")',
  '            if n and _name_hit(n) and n not in names:',
  '                names.append(n)',
  '    except Exception:',
  '        pass',
  '    try:',
  '        for n in bpy.context.preferences.addons.keys():',
  '            if _name_hit(n) and n not in names:',
  '                names.append(n)',
  '    except Exception:',
  '        pass',
  '    return names',
  '',
  '',
  'def _set_issue(code, detail, fix):',
  '    _res["addon_issue"] = {"code": code, "detail": detail, "fix": fix}',
  '',
  '',
  'def _find_addon_module():',
  '    for name, mod in list(sys.modules.items()):',
  '        if _name_hit(name) and hasattr(mod, "__path__"):',
  '            return mod',
  '    try:',
  '        for name in bpy.context.preferences.addons.keys():',
  '            if not _name_hit(name):',
  '                continue',
  '            mod = sys.modules.get(name)',
  '            if mod is not None and hasattr(mod, "__path__"):',
  '                return mod',
  '    except Exception:',
  '        pass',
  '    return None',
  '',
  '',
  'def _addon_meta():',
  '    _res["addon_issue"] = None',
  '    mod = _find_addon_module()',
  '    if mod is None:',
  '        installed = _installed_addon_names()',
  '        if installed:',
  '            _set_issue("addon_disabled", "addon package is installed but not loaded: " + ", ".join(installed), "Open Edit > Preferences > Add-ons, enable DSH Blender Bridge, then relaunch GUI Blender.")',
  '            _res["errors"].append("addon disabled: installed but not loaded (" + ", ".join(installed) + ")")',
  '        else:',
  '            _set_issue("addon_not_installed", "no dsh_blender_bridge / bl_ext.*.dsh_blender_bridge module loaded", "Install extension/dsh_blender_bridge.zip via Blender 4.2 > Edit > Preferences > Extensions > Install from Disk, then relaunch GUI Blender.")',
  '            _res["errors"].append("addon metadata unavailable: no dsh_blender_bridge / bl_ext.*.dsh_blender_bridge module loaded")',
  '        return',
  '    if not hasattr(mod, "__path__"):',
  '        _set_issue("addon_single_file", "single-file addon installs are unsupported", "Install the packaged extension/dsh_blender_bridge.zip as a Blender 4.2 extension, then relaunch GUI Blender.")',
  '        _res["errors"].append("addon metadata unavailable: single-file installs are unsupported")',
  '        return',
  '    version = getattr(mod, "ADDON_VERSION", None)',
  '    if isinstance(version, str):',
  '        try:',
  '            version = tuple(int(part) for part in version.split("."))',
  '        except Exception:',
  '            version = None',
  '    protocol = getattr(mod, "ADDON_PROTOCOL_VERSION", None)',
  '    _res["addon_module"] = getattr(mod, "__name__", None)',
  '    _res["addon_version"] = list(version) if version else None',
  '    _res["addon_protocol"] = protocol',
  '    try:',
  '        _res["addon_ok"] = bool(protocol == 12 and version and tuple(version) >= (2, 0))',
  '    except Exception:',
  '        _res["addon_ok"] = False',
  '    if not _res["addon_ok"]:',
  '        _set_issue("addon_version_mismatch", "addon contract mismatch: version=%r protocol=%r (need >=2.0 / 12)" % (version, protocol), "Replace the addon with the packaged dsh_blender_bridge.zip 2.0.0 (protocol 12), then restart Blender.")',
  '        _res["errors"].append("addon contract mismatch: version=%r protocol=%r (need >=2.0 / 12)" % (version, protocol))',
  '',
  '',
  'def _report():',
  '    _res["server_running"] = bool(_server_running())',
  '    _addon_meta()',
  '    _res["ok"] = bool(_res["server_running"] and _res["addon_ok"])',
  '    _flush()',
  '    print("DSH_LAUNCH " + json.dumps(_res, ensure_ascii=False), flush=True)',
  '',
  '',
  'def _server_cls():',
  '    return getattr(bpy.types, "dshbb_server", None)',
  '',
  '',
  'def _server_running():',
  '    t = _server_cls()',
  '    try:',
  '        return bool(t and getattr(t, "running", False))',
  '    except Exception:',
  '        return False',
  '',
  '',
  'def main():',
  '    _res["background"] = bool(bpy.app.background)',
  '    if bpy.app.background:',
  '        _res["errors"].append("background 模式：addon 明确拒绝在 -b 下起 server，请用 GUI（不带 -b）")',
  '        return',
  '    want = [m.strip() for m in (os.environ.get("DSH_BLENDER_ADDON_MODULE", "") or "").split(",") if m.strip()]',
  '    try:',
  '        import addon_utils',
  '        mods = list(addon_utils.modules())',
  '        _res["addon_modules_seen"] = [getattr(m, "__name__", "") for m in mods][:60]',
  '        names = want or [getattr(m, "__name__", "") for m in mods if _name_hit(getattr(m, "__name__", ""))]',
  '        for n in names:',
  '            try:',
  '                addon_utils.enable(n, default_set=True, persistent=True)',
  '                _res["enabled"].append(n)',
  '                if _server_running():',
  '                    _res["loaded_by"] = "addon_utils:" + n',
  '                    return',
  '            except Exception as exc:',
  '                _res["errors"].append("enable %s: %r" % (n, exc))',
  '    except Exception as exc:',
  '        _res["errors"].append("addon_utils: %r" % (exc,))',
  '    # 兜底：直接按文件 import + register()',
  '    # （Blender 5.x 换成 extension 布局后，addon_utils 可能扫不到老的 scripts/addons/*.py）',
  '    cands = []',
  '    given = os.environ.get("DSH_BLENDER_ADDON_FILE", "")',
  '    if given:',
  '        cands.append(given)',
  '    addons_dir = os.path.join(os.environ.get("BLENDER_USER_SCRIPTS", ""), "addons")',
  '    try:',
  '        for f in sorted(os.listdir(addons_dir)):',
  '            p = os.path.join(addons_dir, f)',
  '            if f.lower().endswith(".py") and _name_hit(f):',
  '                cands.append(p)',
  '            elif os.path.isdir(p) and os.path.isfile(os.path.join(p, "__init__.py")) and _name_hit(f):',
  '                cands.append(p)',
  '            elif f.lower().endswith(".zip") and _name_hit(f):',
  '                cands.append(p)',
  '    except Exception:',
  '        pass',
  '    for p in cands:',
  '        try:',
  '            if os.path.isdir(p):',
  '                name = os.path.basename(p)',
  '                init = os.path.join(p, "__init__.py")',
  '                spec = importlib.util.spec_from_file_location(name, init, submodule_search_locations=[p])',
  '                mod = importlib.util.module_from_spec(spec)',
  '                sys.modules[name] = mod',
  '                spec.loader.exec_module(mod)',
  '            elif p.lower().endswith(".zip"):',
  '                if p not in sys.path:',
  '                    sys.path.insert(0, p)',
  '                mod = importlib.import_module("dsh_blender_bridge")',
  '            else:',
  '                spec = importlib.util.spec_from_file_location("dsh_launch_" + os.path.basename(p)[:-3], p)',
  '                mod = importlib.util.module_from_spec(spec)',
  '                sys.modules[spec.name] = mod',
  '                spec.loader.exec_module(mod)',
  '            if not _server_cls() and hasattr(mod, "register"):',
  '                mod.register()',
  '            _res["addon_file"] = p',
  '            _res["loaded_by"] = "direct-import:" + os.path.basename(p)',
  '            if _server_running():',
  '                return',
  '        except Exception as exc:',
  '            _res["errors"].append("direct import %s: %r" % (p, exc))',
  '',
  '',
  'try:',
  '    import bpy',
  'except Exception as _e:',
  '    _res["errors"].append("no bpy: %r" % (_e,))',
  '    _report()',
  'else:',
  '    main()',
  '    _report()',
].join(String.fromCharCode(10))
