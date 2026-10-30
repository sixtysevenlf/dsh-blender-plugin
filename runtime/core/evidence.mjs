// 版本/指纹自证、路径审计、场景卫生审计（T6）。ENGINE_SELF 指向 runtime/engine.mjs 门面。
// @imports
import fs from "node:fs";
import { IS_MAC, PKG_ROOT } from "../config.mjs";
import { createHash } from "node:crypto";
import { catalogFingerprint, probeDiskCatalog } from "../catalog.mjs";

import path from "node:path";
import { fileURLToPath } from "node:url";

const RUNTIME_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
export const PLUGIN_VERSION = (() => {
  try { return JSON.parse(fs.readFileSync(path.join(PKG_ROOT, 'package.json'), 'utf8')).version; } catch (e) { return null; }
})();
function sha12(buf) { return createHash('sha256').update(buf).digest('hex').slice(0, 12); }

const ENGINE_SOURCE_PATH = path.join(RUNTIME_DIR, "engine.mjs");
const ENGINE_SELF = (() => {
  const info = { sourcePath: ENGINE_SOURCE_PATH, loadedAt: Date.now(), loadedAtText: null,
                 loadedHash: null, loadedBytes: null, error: null };
  info.loadedAtText = new Date(info.loadedAt).toISOString();
  try {
    const buf = fs.readFileSync(ENGINE_SOURCE_PATH);
    info.loadedHash = sha12(buf);
    info.loadedBytes = buf.length;
  } catch (e) { info.error = String((e && e.message) || e).slice(0, 200); }
  return info;
})();

/** 按内容哈希（不是 size+mtime）—— 缓存键仍然是 (size, mtime) 以省 IO，但**返回的是内容指纹** */
const _fpCache = new Map();
function runtimeFingerprint() {
  const out = {};
  const walk = (dir, prefix) => {
    let ents = [];
    try { ents = fs.readdirSync(dir, { withFileTypes: true }); } catch (e) { return; }
    for (const ent of ents) {
      const p = path.join(dir, ent.name);
      const rel = prefix ? prefix + "/" + ent.name : ent.name;
      if (ent.isDirectory()) { if (ent.name !== "ext") walk(p, rel); continue; }
      if (!/.(py|mjs)$/.test(ent.name)) continue;
      try {
        const st = fs.statSync(p);
        const key = String(st.size) + ":" + String(Math.round(st.mtimeMs));
        let hit = _fpCache.get(p);
        if (!hit || hit.key !== key) { hit = { key: key, hash: sha12(fs.readFileSync(p)) }; _fpCache.set(p, hit); }
        out[rel] = hit.hash;
      } catch (e) { /* ignore */ }
    }
  };
  walk(RUNTIME_DIR, "");
  return out;
}

function pidAlive(pid) {
  const n = Number(pid);
  if (!Number.isFinite(n) || n <= 0) return false;
  try { process.kill(n, 0); return true; } catch (e) { return !!(e && e.code === 'EPERM'); }
}

function pathAudit(stdout, stderr) {
  const warns = [];
  const text = String(stdout || '') + '\n' + String(stderr || '');
  const re = /Saved:\s*'([^']*)'/g;
  let m;
  // macOS 上宿主与 Blender 同 OS，不存在「POSIX 路径被 Windows API 改写成 D:home…」这类问题
  while (!IS_MAC && (m = re.exec(text)) !== null) {
    const p = m[1];
    if (/^[A-Za-z]:(home|mnt|tmp|var|usr|root|opt)/i.test(p)) {
      warns.push({ code: 'posix_path_rewritten', evidence: p.slice(0, 200),
        hint: "Blender 是 Windows 进程：'/home/…' 被当成「当前盘根下的相对路径」→ 实际写到 C:\\home\\…。" +
              "脚本里用 K.win_path('/home/…')（或 dsh_win_path）换成 Windows 可用形式再交给 bpy。" });
    }
  }
  const re2 = /DSH_PATH_WARN (\{[^\n]*\})/g;
  while ((m = re2.exec(String(stdout || ''))) !== null) {
    try {
      const o = JSON.parse(m[1]);
      for (const w of (o && o.warnings) || []) warns.push(w);
    } catch (e) { /* ignore */ }
  }
  // 去重（同一路径可能被 Saved: 打多次）
  const seen = new Set();
  return warns.filter((w) => { const k = w.code + '|' + String(w.evidence || w.value || ''); if (seen.has(k)) return false; seen.add(k); return true; });
}

export function engineProvenance(opts) {
  const o = (opts && typeof opts === 'object') ? opts : {};
  const cat = catalogFingerprint();
  const out = {
    sourcePath: ENGINE_SOURCE_PATH, pluginVersion: PLUGIN_VERSION, hashAlgo: 'sha256:12',
    loadedAt: ENGINE_SELF.loadedAtText, loadedHash: ENGINE_SELF.loadedHash, loadedBytes: ENGINE_SELF.loadedBytes,
    loadedReadError: ENGINE_SELF.error,
    currentHash: null, currentBytes: null, currentMtime: null,
    // 三态：'' current = 进程里的就是磁盘上这份；stale = 磁盘被改过、须重启后端；unknown = 读不到/判不了
    verdict: 'unknown', stale: null, staleReason: null,
    catalogHash: cat.catalogHash, families: cat.families, opsCount: cat.opsCount,
    toolDetailKeys: cat.toolDetailKeys, commandCatalogKeys: cat.commandCatalogKeys,
    // mtime **只作参考**，不参与判定（这正是旧实现的问题）
    note: '判定用内容哈希（sha256:12），不是 size/mtime；stale=true 表示磁盘已改、本进程还是旧一代 → 重启后端再问一次',
  };
  try {
    const buf = fs.readFileSync(ENGINE_SOURCE_PATH);
    const st = fs.statSync(ENGINE_SOURCE_PATH);
    out.currentHash = sha12(buf);
    out.currentBytes = buf.length;
    out.currentMtime = new Date(st.mtimeMs).toISOString();
  } catch (e) { out.staleReason = '读不到磁盘上的 engine.mjs：' + String((e && e.message) || e).slice(0, 160); }
  if (!ENGINE_SELF.loadedHash) {
    out.verdict = 'unknown'; out.stale = null;
    out.staleReason = out.staleReason || ('加载时就没读到源码：' + String(ENGINE_SELF.error || 'unknown'));
  } else if (!out.currentHash) {
    out.verdict = 'unknown'; out.stale = null;
  } else if (out.currentHash === ENGINE_SELF.loadedHash) {
    out.verdict = 'current'; out.stale = false;
  } else {
    out.verdict = 'stale'; out.stale = true;
    out.staleReason = '磁盘内容已变（loaded ' + ENGINE_SELF.loadedHash + ' ≠ current ' + out.currentHash
      + '，加载于 ' + ENGINE_SELF.loadedAtText + '）→ 本进程的目录/路由是旧一代：重启后端再问一次，'
      + '或用 catalog args={verify:true} 起干净进程对拍';
  }
  if (o.verify) {
    const disk = probeDiskCatalog(o.verifyTimeoutMs);
    out.diskCatalog = disk;
    if (disk && disk.ok) {
      const sameHash = disk.catalogHash === cat.catalogHash;
      out.diskVerdict = {
        ok: true, matchesLoadedCatalog: sameHash,
        families: disk.families, opsCount: disk.opsCount, catalogHash: disk.catalogHash, pluginVersion: disk.pluginVersion,
        note: sameHash
          ? '干净进程读磁盘得到的 catalog 指纹与本进程一致 —— 目录不是旧版'
          : ('干净进程读磁盘是 ' + String(disk.families) + ' 个 family / ' + String(disk.opsCount) + ' 个 op（' + disk.catalogHash
             + '），本进程回答的是 ' + String(cat.families) + ' / ' + String(cat.opsCount) + '（' + cat.catalogHash
             + '）→ **本进程目录是旧版**，以干净进程那份为准（磁盘 ' + String(ENGINE_SOURCE_PATH) + '）'),
      };
    } else {
      out.diskVerdict = { ok: false, note: '干净进程对拍失败（不影响本进程回答）：' + String((disk && disk.error) || 'unknown') };
    }
  }
  return out;
}

/**
 * v2.6（本轮反馈 #3）：多灯卫生 —— EEVEE 阴影池溢出的**结构化告警**。
 *
 * 现场（4 小时里刷了几千行）：36 盏灯 → 控制台每一帧刷
 *   Error: Shadow buffer full, may result in missing shadows and lower performance. (3040 / 2048)
 * 而 scene.eevee.shadow_pool_size = 4096 **静默无效** —— 它是枚举，上限就是 2048（2 GB）。
 * 旧行为：告警只在 stderr 里刷屏，模型得自己看懂；新行为：进回执 warnings[]，附一条能直接粘贴的修法。
 */
export function sceneHygieneAudit(stdout, stderr) {
  const text = String(stdout || '') + String.fromCharCode(10) + String(stderr || '');
  const warns = [];
  const hits = text.match(/Shadow buffer full[^()]*\((\d+)\s*\/\s*(\d+)\)/g) || [];
  if (hits.length) {
    const last = hits[hits.length - 1].match(/\((\d+)\s*\/\s*(\d+)\)/);
    const FIX = ['import bpy',
      'bpy.context.scene.eevee.shadow_pool_size = "2048"   # 枚举上限就是 2048，写 4096 无效',
      'for o in bpy.data.objects:',
      '    if o.type == "LIGHT" and o.data.type in {"POINT", "SPOT"}:',
      '        o.data.use_shadow = False   # practical 灯基本不需要投影'].join(String.fromCharCode(10));
    warns.push({
      code: 'eevee_shadow_pool_full',
      occurrences: hits.length,
      used_mb: last ? Number(last[1]) : null,
      limit_mb: last ? Number(last[2]) : null,
      hint: 'EEVEE 阴影池溢出（' + (last ? last[1] : '?') + '/' + (last ? last[2] : '?') + ' MB，出现 ' + hits.length + ' 次）：'
          + '阴影会缺失、渲染变慢，但**不会报错退出** —— 不处理就是一张「阴影丢了」的图。'
          + '改法：① shadow_pool_size 是枚举且上限 2048（写 4096 会被静默忽略）；'
          + '② 主力是减投影灯：点光/面光的 use_shadow 全关，只留 2–4 盏真正要投影的；'
          + '③ 大场景再降 shadow_resolution_scale。',
      fix: FIX,
      also: 'K.light_hygiene(apply=True) 可一次做完（返回 before/after 计数）',
    });
  }
  return warns;
}

export { sha12, ENGINE_SELF, ENGINE_SOURCE_PATH, RUNTIME_DIR, runtimeFingerprint, pidAlive, pathAudit };
