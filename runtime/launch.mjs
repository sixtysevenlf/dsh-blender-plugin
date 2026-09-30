/**
 * S3-c · 拉起 GUI Blender（从 engine.mjs 外移，非闭包部分）
 *
 * LAUNCH_BOOT_PY：注进 Blender 的启动脚本（连 addon）；launchBlender：detached spawn + 轮询 addon 端口。
 * engine.mjs 原样重导出，对外面不变（tmp/snap.mjs 对拍）。
 */
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { CFG, PATHS, IS_WIN, winToWsl, wslToWin } from './config.mjs';
import { WIN_TMP, WSL_TMP, blenderJoin, BLENDER_EXE, USER_CONFIG_WIN, USER_SCRIPTS_WIN } from './paths.mjs';
// S8 修：tcpProbe 原先定义在 engine.mjs，S3-c 把 launchBlender 搬出来时漏了导入 ⇒ op=launch/dry_run 直接 ReferenceError。
// 复用 runtime/backend_probe.mjs 的 probeTcp（同一语义：能连上就算在监听），保持单一实现。
import { probeTcp } from './backend_probe.mjs';
import { writeHeadlessScript } from './staging.mjs';
import { baseChildEnv, withWslEnv, readJsonSafe } from './proc_env.mjs';   // S8：S3-c 搬移时漏了这个依赖

/**
 * S11 · Windows 侧启动代理（零互操作）
 *
 * 从 WSL 直接 spawn blender.exe 会走 WSLInterop，每次调用生成一个 "Interop" 中继线程；
 * 该线程有已知缺陷：控制 socket EOF 时若 ReadFile 同步返回 0 字节，循环没有对应分支，
 * 于是永久忙读、吃满一个核心且无法释放（microsoft/WSL#41173；本机实测泄漏过一次
 * 5084 CPU秒）。上游 2.7.10 与 master 逐字节相同 ⇒ 升级 WSL 修不掉，只能少产生调用。
 *
 * 所以先探 Windows 侧启动代理（D:/DSH/blender/launch-agent.mjs，默认 127.0.0.1:9878）：
 * 在线 ⇒ 由它原生拉起 Blender，本次启动 0 次互操作调用；不在线 ⇒ 回退互操作 spawn。
 *
 * 代理是「派发即返回」：它只负责原生拉起，端口等待由本函数完成。这样两边超时互不干扰；
 * 且代理一旦受理就绝不再回退互操作，避免堆出第二个 Blender（v1 阻塞版实测踩过）。
 * 关掉：DSH_BLENDER_LAUNCH_AGENT_URL=off
 */
async function tryLaunchAgent(opts, addonPort, steps, base) {
  const url = String(CFG.launchAgentUrl || "");
  if (!url || url.toLowerCase() === "off") return null;
  if (opts.interop === true) return null;
  try {
    const ac = new AbortController();
    const t = setTimeout(() => ac.abort(), 1500);
    const h = await fetch(url + "/health", { signal: ac.signal });
    clearTimeout(t);
    if (!h.ok) return null;
  } catch (e) {
    return null;
  }
  steps.push("Windows 启动代理在线：" + url + "（本次启动 0 次互操作调用）");
  const ac = new AbortController();
  const t = setTimeout(() => ac.abort(), 15000);
  try {
    const r = await fetch(url + "/launch", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        addonPort: addonPort,
        file: opts.file ? String(opts.file) : null,
        force: !!opts.force,
        blenderExe: opts.exe ? String(opts.exe) : null,
      }),
      signal: ac.signal,
    });
    const j = await r.json();
    steps.push("代理已受理：already=" + String(!!j.already) + " dispatched=" + String(!!j.dispatched));
    return Object.assign({}, base, j, { via: "windows-agent" });
  } catch (e) {
    steps.push("代理调用未确认（" + String((e && e.message) || e) + "）—— 按已派发处理，不回退互操作以免双开");
    return Object.assign({}, base, { ok: false, already: false, dispatched: true, launched: true,
                                     listening: false, addonPort: addonPort, via: "windows-agent" });
  } finally {
    clearTimeout(t);
  }
}



const LAUNCH_BOOT_PY = [
  '"""DSH launch boot (v0.9.4): enable the Blender MCP addon and start its socket server (GUI only)."""',
  'import importlib.util, json, os, sys',
  '',
  '_res = {',
  '    "ok": False, "background": None, "enabled": [], "loaded_by": None, "server_running": False,',
  '    "addon_file": None, "errors": [], "addon_modules_seen": [],',
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
  'def _report():',
  '    _res["server_running"] = bool(_server_running())',
  '    _res["ok"] = bool(_res["server_running"])',
  '    _flush()',
  '    print("DSH_LAUNCH " + json.dumps(_res, ensure_ascii=False), flush=True)',
  '',
  '',
  'def _server_running():',
  '    t = getattr(bpy.types, "blendermcp_server", None)',
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
  '        names = want or [getattr(m, "__name__", "") for m in mods if "mcp" in getattr(m, "__name__", "").lower()]',
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
  '            if f.lower().endswith(".py") and "mcp" in f.lower():',
  '                cands.append(os.path.join(addons_dir, f))',
  '    except Exception:',
  '        pass',
  '    for p in cands:',
  '        try:',
  '            spec = importlib.util.spec_from_file_location("dsh_launch_" + os.path.basename(p)[:-3], p)',
  '            mod = importlib.util.module_from_spec(spec)',
  '            sys.modules[spec.name] = mod',
  '            spec.loader.exec_module(mod)',
  '            if not getattr(bpy.types, "blendermcp_server", None) and hasattr(mod, "register"):',
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
].join(String.fromCharCode(10));

/** 读一个 JSON 小文件（不存在/坏了都返回 null，绝不抛） */

export async function launchBlender(opts = {}) {
  const started = Date.now();
  const addonPort = Math.max(1, Math.min(65535, Number(opts.addonPort || CFG.addonPort) || 9876));
  const waitMs = Math.max(2000, Math.min(600000, Number(opts.waitMs || process.env.DSH_BLENDER_LAUNCH_WAIT_MS) || 90000));
  const exe = String(opts.exe || BLENDER_EXE || '');
  const steps = [];
  const statusWsl = path.join(winToWsl(WIN_TMP), 'launch-status.json');
  const base = { exe: exe || null, addonPort: addonPort, waitMs: waitMs, statusFile: statusWsl,
                 statusFileWin: wslToWin(statusWsl), configFrom: CFG.source ? CFG.source.blenderExe : null };

  // ① 已经在监听 → 什么都不做（幂等：agent 反复调用不会堆出第二个 Blender）
  if (await probeTcp('127.0.0.1', addonPort, 800)) {
    const boot0 = readJsonSafe(statusWsl);
    return Object.assign({}, base, { ok: true, already: true, launched: false, waitedMs: Date.now() - started, steps: steps,
      boot: boot0, hint: 'addon 已在本机 ' + addonPort + ' 监听，无需启动（要重启先关掉那个 Blender，或用 force 另起一个）' });
  }
  // 1.5 优先走 Windows 侧启动代理：原生拉起 ⇒ 0 次互操作调用；端口等待由本函数完成
  const agentRes = await tryLaunchAgent(opts, addonPort, steps, base);
  if (agentRes) {
    let up = !!agentRes.listening;
    while (!up && Date.now() - started < waitMs) {
      if (await probeTcp("127.0.0.1", addonPort, 700)) { up = true; break; }
      await new Promise((r) => setTimeout(r, 500));
    }
    const st = steps.concat([up
      ? ("addon 端口 " + addonPort + " 已监听（" + String(Date.now() - started) + "ms）")
      : ("等待 " + String(waitMs) + "ms 端口仍未开")]);
    return Object.assign({}, agentRes, {
      ok: up, listening: up, already: up, launched: agentRes.launched !== false,
      waitedMs: Date.now() - started, steps: st,
      hint: up
        ? ("GUI Blender 已起来且 addon 已 Connect（端口 " + addonPort + "）—— 现在可以用 rt_do / rt_see 了（Windows 代理拉起，0 次互操作调用）")
        : ("Windows 代理已派发启动，但 addon 端口 " + addonPort + " 在 " + String(waitMs) + "ms 内未监听；看 D:/DSH/blender/logs/startup-status.json"),
    });
  }

  if (!exe) {
    return Object.assign({}, base, { ok: false, launched: false, already: false, waitedMs: Date.now() - started, steps: steps,
      error: '没找到 blender.exe', hint: '设 DSH_BLENDER_EXE 或 dsh-blender.config.json 的 blenderExe（见 docs/配置参考.md）' });
  }

  const sp = writeHeadlessScript(LAUNCH_BOOT_PY);
  const args = ['--python', sp.win];
  if (opts.file) args.push(wslToWin(String(opts.file)));
  const childEnv = withWslEnv(baseChildEnv({}), null);
  if (USER_SCRIPTS_WIN) childEnv.BLENDER_USER_SCRIPTS = USER_SCRIPTS_WIN;
  if (USER_CONFIG_WIN) childEnv.BLENDER_USER_CONFIG = USER_CONFIG_WIN;
  if (opts.addonFile) childEnv.DSH_BLENDER_ADDON_FILE = String(opts.addonFile);
  if (opts.addonModule) childEnv.DSH_BLENDER_ADDON_MODULE = String(opts.addonModule);
  childEnv.DSH_BLENDER_LAUNCH_STATUS = wslToWin(statusWsl);
  try { fs.rmSync(statusWsl, { force: true }); } catch (e) { /* 旧状态删不掉也无所谓 */ }

  if (opts.dryRun) {
    return Object.assign({}, base, { ok: true, dryRun: true, launched: false, already: false, args: args, bootScript: sp.win,
      steps: ['dryRun：只给出将要执行的命令'], hint: '去掉 dry_run 即真正启动' });
  }

  // 先做一次能做的存在性检查（/mnt/... 这类 WSL 可见路径；Windows 原生路径查不了，交给下面的异步 error）
  if (exe.charAt(0) === '/' && !fs.existsSync(exe)) {
    return Object.assign({}, base, { ok: false, launched: false, already: false, waitedMs: Date.now() - started, steps: steps,
      error: 'blender.exe 不存在：' + exe, hint: '设 DSH_BLENDER_EXE 或 dsh-blender.config.json 的 blenderExe（当前值来自 ' + String(base.configFrom) + '）' });
  }
  let child = null;
  let spawnErr = null;
  try {
    child = spawn(exe, args, { env: childEnv, stdio: 'ignore', detached: true });
  } catch (e) {
    spawnErr = e;
  }
  if (child) {
    // ⚠ 实测坑（v0.9.4 自检抓到）：spawn 不存在的可执行文件**不会抛**，而是异步 emit 'error'。
    // 不挂 handler 的话整个后端进程会被 unhandled 'error' 打挂（自检第一版就是这样挂的）。
    child.on('error', (e) => { spawnErr = e; });
    try { child.unref(); } catch (e) { /* ignore */ }
  } else if (!spawnErr) {
    spawnErr = new Error('spawn 返回空句柄');
  }

  let listening = false;
  while (!spawnErr && Date.now() - started < waitMs) {
    if (await probeTcp('127.0.0.1', addonPort, 700)) { listening = true; break; }
    await new Promise((r) => setTimeout(r, 500));
  }
  if (spawnErr) {
    return Object.assign({}, base, { ok: false, launched: false, already: false, waitedMs: Date.now() - started, steps: steps,
      pid: child ? child.pid : null,
      error: '起不来 Blender 进程（' + exe + '）：' + String((spawnErr && spawnErr.message) || spawnErr),
      hint: '可能是路径不存在 / 不可执行 / 不是 Windows 可执行文件。设 DSH_BLENDER_EXE 指到真的 blender.exe（WSL 形如 /mnt/d/.../blender.exe）' });
  }
  steps.push('已 spawn ' + exe + ' pid=' + String(child.pid) + '（detached，与后端进程解耦）');
  const boot = readJsonSafe(statusWsl);
  if (listening) steps.push('addon 端口 ' + addonPort + ' 已监听（' + String(Date.now() - started) + 'ms）');
  return Object.assign({}, base, {
    ok: listening, launched: true, already: false, listening: listening, pid: child.pid,
    waitedMs: Date.now() - started, steps: steps, boot: boot, bootScript: sp.win, args: args,
    hint: listening
      ? 'GUI Blender 已起来且 addon 已 Connect（端口 ' + addonPort + '）—— 现在可以用 rt_do / rt_see 了（写操作记得 blender_viewport op=lease 拿租约）'
      : '等待 ' + String(waitMs) + 'ms 端口仍未开：看 statusFile 里的 boot 结论（addon 没被扫到 / 不是 GUI 模式 / exe 起了但崩了）',
  });
}

/**
 * 三级诊断：把"连不上/超时"拆成可行动的原因（多 agent 场景的第一痛点）。
 *   TCP 不通            → blender-unreachable（Blender 没跑 / addon 没监听 9876）
 *   TCP 通 + ping 通     → main-thread-busy（addon 活着，但 bpy 命令被主线程占用挡住）
 *   TCP 通 + ping 不通   → addon-thread-stuck（客户端线程卡住，通常上一条长命令还在跑）
 * ping 是 addon 里唯一"不碰 bpy"的命令 → 它通不通能区分"传输层"还是"数据层"。
 */