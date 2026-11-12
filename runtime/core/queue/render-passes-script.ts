/**
 * Fixed internal Blender entry point for declarative render passes (render-pass design).
 *
 * The queue writes this text to `<job temp dir>/render_passes.py` and passes a
 * declarative `render-config.json` next to it; no caller Python ever reaches the
 * process. Keeping the script in TypeScript modules means it ships with the package
 * build (like GPU_PRELUDE in runtime/paths.ts) without registering a new Python module
 * in runtime/py/modules.json or a new ruff root.
 *
 * The Python program is split across two modules for the per-file line budget:
 * `render-passes-py-setup.ts` (scene + compositor) and `render-passes-py-capture.ts`
 * (per-frame capture + entry point). They are concatenated verbatim below.
 */
import { RENDER_PASSES_PY_CAPTURE } from './render-passes-py-capture.js'
import { RENDER_PASSES_PY_SETUP } from './render-passes-py-setup.js'

export const RENDER_PASSES_MANIFEST_VERSION = 1

export const RENDER_PASSES_PY = RENDER_PASSES_PY_SETUP + RENDER_PASSES_PY_CAPTURE
