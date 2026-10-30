// render_guard 标记文件读写（零 Blender 往返）（T6）
// @imports
import path from "node:path";
import { winToWsl } from "../config.mjs";
import { WIN_TMP } from "../paths.mjs";
import fs from "node:fs";


export function createRenderState(E) {
  // deps: none
  const RENDER_FLAG_WSL = path.join(winToWsl(WIN_TMP), 'renders', 'render_state.json');
  const RENDER_FLAG_WIN = path.win32.join(WIN_TMP, 'renders', 'render_state.json');
  const RENDER_STALE_MS = Number(process.env.DSH_RENDER_STALE_MS || 15 * 60 * 1000);
  function readRenderFlag() {
    try {
      const st = fs.statSync(RENDER_FLAG_WSL);
      const info = JSON.parse(fs.readFileSync(RENDER_FLAG_WSL, 'utf8'));
      const ageMs = Number(info.since) ? Math.round(Date.now() - Number(info.since) * 1000) : Math.round(Date.now() - st.mtimeMs);
      return { present: true, stale: ageMs > RENDER_STALE_MS, ageMs: ageMs, info: info };
    } catch (e) { return { present: false, stale: false, ageMs: null, info: null }; }
  }
  function clearRenderFlag() { try { fs.unlinkSync(RENDER_FLAG_WSL); return true; } catch (e) { return false; } }
  return { readRenderFlag, clearRenderFlag, RENDER_FLAG_WSL, RENDER_FLAG_WIN, RENDER_STALE_MS };
}
