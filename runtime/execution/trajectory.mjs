// 轨迹 JSONL（T6）
// @imports
import path from "node:path";
import { winToWsl } from "../config.mjs";
import { WIN_TMP } from "../paths.mjs";
import fs from "node:fs";


export function createTrajectory(E) {
  // deps: none
  const TRAJ_DIR = path.join(winToWsl(WIN_TMP), 'trajectory');
  const TRAJ_OFF = process.env.DSH_TRAJ === '0' || process.env.DSH_TRAJ === 'false';
  const TRAJ_FULL = process.env.DSH_TRAJ_FULL === '1';
  const TRAJ_MAX_BYTES = Number(process.env.DSH_TRAJ_MAX_BYTES || 16000000);
  // 轨迹跳过只读诊断（轨迹台账是 appendFileSync 到 workDir，几十 ms；诊断路由不该被拖慢）。
  const TRAJ_SKIP = new Set(['metrics', 'status', 'protocol', 'sceneEpoch']);
  function trajShortHash(s) {
    let h = 0;
    for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0;
    return (h >>> 0).toString(16).padStart(8, '0');
  }
  function trajRecord(route, op, args, ms, ok, err, extra) {
    if (TRAJ_OFF) return;
    // v0.9.6（D1）：加 family / args_keys / hint_shown —— 用来做"家族覆盖率"和"意图→工具"周报。
    // family 只对 plan 路由有意义（frame 的"op"其实是尺寸、act 的"op"是代码片段，切出来是噪声）。
    const fam = (route === 'plan' && op) ? String(op).split('_')[0] : null;
    try {
      fs.mkdirSync(TRAJ_DIR, { recursive: true });
      const d = new Date();
      const key = String(d.getFullYear()) + String(d.getMonth() + 1).padStart(2, '0') + String(d.getDate()).padStart(2, '0');
      const f = path.join(TRAJ_DIR, 'blender_rt-' + key + '.jsonl');
      try {
        const st = fs.statSync(f);
        if (st.size > TRAJ_MAX_BYTES) fs.renameSync(f, path.join(TRAJ_DIR, 'blender_rt-' + key + '-' + Date.now() + '.jsonl'));
      } catch (e) { /* 首次写 */ }
      const s = args === undefined || args === null ? '' : (typeof args === 'string' ? args : JSON.stringify(args));
      fs.appendFileSync(f, JSON.stringify({
        t: d.toISOString(), route: route, op: op === undefined || op === null ? null : String(op),
        ms: ms, ok: !!ok, err: err ? String(err).slice(0, 300) : null,
        args_bytes: s.length, args_digest: trajShortHash(s),
        args_head: s ? s.slice(0, TRAJ_FULL ? 8000 : 300) : null,
        // v0.9.6（D1）：可统计"家族覆盖率"与"参数键分布"（不记值，只记键名）
        family: fam,
        args_keys: (args && typeof args === 'object' && !Array.isArray(args)) ? Object.keys(args).slice(0, 20) : null,
        hint_shown: !!(extra && extra.hint_shown),
      }) + String.fromCharCode(10), 'utf8');
    } catch (e) { /* 轨迹是旁路：出错就丢这一行 */ }
  }
  /** 给返回对象裹一层记录：this 仍指向原对象（内部 this.xxx() 不会重复记账） */
  function withTrajectory(obj) {
    const out = {};
    for (const k of Object.keys(obj)) {
      const v = obj[k];
      if (typeof v === 'function') {
        out[k] = function (...a) {
          const t0 = Date.now();
          const rawOp = a.length ? a[0] : null;
          const opArg = (typeof rawOp === 'string' || typeof rawOp === 'number') ? rawOp : null;
          const argsArg = a.length > 1 ? a[1] : a[0];
          const rec = (ok, err, res) => {
            if (TRAJ_SKIP.has(k)) return;
            const hinted = !!(res && typeof res === 'object' && (res.hint_shown ||
              (typeof res.result === 'string' && res.result.indexOf('CMD_HINT') === 0)));
            trajRecord(k, opArg, argsArg, Date.now() - t0, ok, err, { hint_shown: hinted });
          };
          let r;
          try { r = v.apply(obj, a); } catch (e) { rec(false, e && e.message); throw e; }
          if (r && typeof r.then === 'function') {
            return r.then((x) => { rec(true, null, x); return x; },
                          (e) => { rec(false, e && e.message, null); throw e; });
          }
          rec(true, null, r);
          return r;
        };
      } else if (v && typeof v === 'object' && !Array.isArray(v)) {
        out[k] = withTrajectory(v);
      } else {
        out[k] = v;
      }
    }
    return out;
  }
  return { withTrajectory, TRAJ_DIR, TRAJ_OFF, TRAJ_FULL };
}
