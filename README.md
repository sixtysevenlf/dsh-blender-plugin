# DSH × Blender

[简体中文](docs/zh/README.md)

DSH × Blender connects DeepSeek Harness to Blender so an agent can build, inspect, validate, and export 3D scenes in a real Blender session. The normal workflow is GUI-first: start Blender, connect the addon, and let the agent look, change, and verify. When no GUI is available, background headless jobs (`blender_run(kind="headless")`) still execute scripts and return rendered frames.

## Contents

- [Features](#features)
- [Quick start](#quick-start)
- [Tools](#tools)
- [Documentation](#documentation)
- [Troubleshooting](#troubleshooting)
- [Development](#development)
- [Support](#support)
- [License](#license)

## Features

- **GUI-first interactive modeling.** The agent works against the Blender session you are looking at. It can inspect the scene, run Python in a persistent kernel, take a viewport or custom-angle frame, and iterate one step at a time.
- **Headless jobs with visual feedback.** If Blender has no GUI, the agent submits a background headless job (`blender_run(kind="headless")`) and collects scripts, logs, and rendered frames from it. Live step-by-step interaction needs a GUI session; the job path exists so long or fresh-scene work still gets done.
- **Reference-image measurement.** Measure silhouettes, holes, symmetry, readable features, and scale from a reference image before modeling. If no scale is known, the tools return pixel units instead of inventing millimeters.
- **Parametric surfaces.** Build reproducible surface meshes from sections (loft), a profile swept along a rail, or a four-sided patch; run continuity, curvature, self-intersection, and thickness checks; then export GLB. The same spec yields the same `spec_hash` and vertices instead of hand-sculpted geometry.
- **Assembly audit and repair.** Validate an assembly manifest, classify clashes, hidden overlaps, contacts, gaps, floating parts, and contained parts, apply safe repairs, gate the result, and export GLB, FBX, or OBJ with a manifest and merge ledger.
- **Blockout and video-reference packs.** Build low-poly blockouts from declarative parts, render orthographic and perspective reference views, and produce a turntable MP4 for video models such as Seedance.
- **Visual acceptance and delivery gates.** Compare renders against references from the same camera, report defects with crops and measurements, run defect-injection evaluation, and block delivery when the gate is not pass.
- **Automation and long jobs.** `blender_run` submits render or headless work and returns a job id immediately; parameter searches and batch validation run inside the GUI session through `blender_op`. Neither occupies the GUI for the whole run.
- **Native selection.** Select objects or collections in Blender and send them into the current DSH draft.
- **Works in dsh-TUI too.** In dsh-TUI, a Blender selection is attached to your next message as a context chip; the footer shows the Blender channel state (connected / AI paused / rendering / jobs); `@` mentions scene objects, materials, and collections; and `/blender status|doctor|jobs|presence` checks the channel or lists which agents observed Blender, without going through the model. Each part needs the matching dsh-TUI seam and stays off when the seam is missing.

## Quick start

**Before anything else: install the bundled addon first.** Use the addon shipped in this repository: **extension/dsh_blender_bridge.zip**. It must be your only Blender-side *agent bridge*: do not install or run `MCP for Blender`, `blender_mcp`, or any other addon that serves Blender over its own socket. The 3.1 addon dials the Gateway and opens no inbound port, but a legacy MCP bridge still listens on 127.0.0.1:9876; two Blender-side agent bridges confuse the host. Unrelated addons (modeling, materials, rendering, asset libraries) are fine and are neither checked nor blocked. Install and enable this addon in Blender before the other steps. If the installation below does not work, first check that this exact addon is installed, enabled, and connected.

### Requirements

- DSH (DeepSeek Harness) installed and working.
- Node.js 24.9 or newer is needed only for source builds; installing from npm uses the runtime bundled with DSH. Node.js 24.9 is the minimum supported version; CI runs on Node 24.
- Blender 4.2 LTS is the tested version. Blender 4.x may work; Blender 5.x is not yet validated. GUI mode requires the bundled addon (install it first); headless mode only needs the Blender executable.
- Addon compatibility: the bundled addon is the only required addon and the only supported agent bridge. Other addons do not conflict; an old `MCP for Blender` / `blender_mcp` is unsupported (it still listens on 127.0.0.1:9876) — disable it, no need to uninstall.
- Optional: a DSH source checkout when rebuilding the host side; otherwise the build links against this repository's installed dependencies.

### Option A: install from npm

This is the supported installation path. It does not require `git clone`, `npm ci`, or `npm run build`.

1. **Install the bundled addon first.** Download `dsh_blender_bridge.zip` from the [latest GitHub Release](https://github.com/sixtysevenlf/dsh-blender-plugin/releases/latest), then in Blender open `Edit > Preferences > Extensions > Install from Disk...`, select the zip, enable **DSH Blender Bridge**, open the `N` panel in the 3D view, and click **Connect**.

2. **Install the DSH plugin from npm.** Choose the path for your DSH host:

   - **DSH Web or DSH TUI:** install into the profile you actually run:

         dsh plugin --profile <profile> add dsh-blender-plugin@3.1.0-alpha.1

     Then restart DSH in step 3.

   - **DSH Desktop:** open the Plugins page and install the bundle `dsh-blender-plugin@3.1.0-alpha.1`. DSH Desktop applies the bundle from the Plugins page and does not need a DSH restart.

3. **Restart DSH (DSH Web and DSH TUI only).** If you installed from the DSH Desktop Plugins page, skip this step.

4. **Verify the installation.** The only acceptance criterion for the GUI route is `doctor` returning `kind` equal to `ok`:

        curl -sS http://127.0.0.1:39877/doctor

   GUI route: `curl -sS http://127.0.0.1:39877/health` and `/doctor`; `doctor.kind` must be `ok`.

   Headless-only route (no addon): `blender_run(action="submit", kind="headless", script="print('ok')")` followed by `wait`/`collect` must return the job and its outputs.

The addon remains the only supported Blender-side agent bridge. Disable any other Blender-side agent bridge (a legacy MCP bridge still serves port 9876); ordinary modeling, material, rendering, and asset addons are unaffected.

### Option B: ask an AI agent

Copy this prompt into an agent that can run commands:

    First install and enable the bundled addon dsh_blender_bridge.zip
    from the latest GitHub Release. If a bridge addon is already enabled
    (MCP for Blender / blender_mcp, or anything else serving Blender on
    ), disable it; do not uninstall the user's other addons and
    do not ask them to.
    Then install the DSH × Blender plugin from npm for my DSH profile:
    dsh plugin --profile <profile> add dsh-blender-plugin@3.1.0-alpha.1
    Read docs/AI-安装提示词.md first, follow it exactly,
    and finish with the acceptance check: doctor must return kind=ok for the
    GUI route, or a blender_run headless submit + wait must return outputs for the headless-only route.
    Do not skip the check and do not claim success without it.

Fetch the full install prompt locally:

    curl -fsSL https://raw.githubusercontent.com/sixtysevenlf/dsh-blender-plugin/main/docs/AI-安装提示词.md -o AI-安装提示词.md

Install the standalone modeling skill (only needed for hosts that do not load the plugin; DSH gets it bundled):

    curl -fsSL https://raw.githubusercontent.com/sixtysevenlf/dsh-blender-plugin/main/skills/blender-modeling/install.sh | bash

### Option C: build from source (developers)

1. **Install the bundled addon first.**

   Download **extension/dsh_blender_bridge.zip** from the extension/ folder of this repository. In Blender, open Edit > Preferences > Extensions > Install from Disk..., select that zip, enable DSH Blender Bridge, open the N panel in the 3D view, and click Connect. The addon dials the DSH Gateway at 127.0.0.1:39877 after Connect; it does not listen on a port. Replacing or upgrading the addon requires a Blender restart: there is no hot reload, and a 3.0.x addon keeps its old 9876 listener until Blender exits. If you previously installed the single-file `dsh_blender_bridge.py`, disable and remove it first: 3.1 installs only this Blender extension.

   Keep this addon as your only agent bridge. If `MCP for Blender` / `blender_mcp` (or any other Blender-side agent bridge) is enabled, turn it off — uninstalling is not required. Your other addons can stay. If the rest of the installation does not work, check this step first.

2. **Developer path:** clone the repository and build it. `lib/` is a build artifact and is not committed, so a fresh clone must build once before use. `npm run setup:dev` installs the npm and Python development dependencies (creating `.venv`); unless you are developing the plugin, use the npm install above.

        git clone https://github.com/sixtysevenlf/dsh-blender-plugin.git
        cd dsh-blender-plugin
        npm run setup:dev
        npm run build
        # Optional: DSH_CHECKOUT=/path/to/dsh-harness npm run build links DSH host packages

3. Add the package to your DSH profile as a bundle. Link the checkout into the profile, then add the dependency and bundle entry to the profile package.json.

        ln -s /absolute/path/to/dsh-blender-plugin <profile>/node_modules/dsh-blender-plugin

    Profile package.json:

        {
          "dependencies": {
            "dsh-blender-plugin": "link:/absolute/path/to/dsh-blender-plugin"
          },
          "dsh": {
            "profile": {
              "bundles": ["dsh-blender-plugin"]
            }
          }
        }

    Validate the profile and restart DSH:

        dsh --profile <profile> --dump-config

4. Verify the installation.

    GUI route:

        curl -sS http://127.0.0.1:39877/health
        curl -sS http://127.0.0.1:39877/doctor

    The only acceptance criterion for the GUI route is doctor returning kind equal to ok. If it does not, the plugin is not installed correctly.

    Blender and the Gateway are started by the host: open Blender yourself, enable the addon, and click Connect. There is no model-facing launch tool in 3.1.

    Headless-only route (no addon):

        blender_run(action="submit", kind="headless", script="print('ok')")
        blender_run(action="wait", id="<jobId>")

    A returned job whose outputs include the script log means the headless path is working. The doctor bpy round-trip is expected to be unreachable in this mode.

## Tools

The plugin exposes exactly 6 model-facing tools; all planning, audit, assembly, export, and evaluation operations live behind `blender_op` (34 families / 228 ops), so the surface stays small.

| Tool | What it gives you |
|---|---|
| blender_session | GUI session lifecycle and ownership: list/status/doctor, attach, claim/release, grant/revoke write scopes, pause state, presence, and `action=txn` snapshots/marks for rollback. Blender and the Gateway are host-managed. |
| blender_op | The capability catalog (`op="catalog"`, 34 families / 228 ops) and the execution surface for modeling, audit, assembly, delivery, and evaluation; also `action=loop|opt|preset`. Executing ops needs a ready GUI session. |
| blender_py | Runs Python in the attached GUI session with a persistent kernel; `see=true` returns a frame. GUI-only: there is no headless fallback. |
| blender_see | GUI capture only: `viewpoint=viewport|camera|custom`, `mode=frame|sequence`, fixed aspect/size tiers. Camera animation and batch renders go to `blender_run(kind="render")`. |
| blender_run | The only job entry point: `action=submit` with `kind=render|headless` returns a job id immediately; `status/wait/collect/cancel/list` manage it; `action=tune` covers render performance. |
| blender_inspect | Lists the addon's executable read-only commands and runs one with `command=` + `args=`; removed write commands return a `wrong_tool` hint. |

Old 3.0 tool names are no longer registered; the migration table is in [MIGRATIONS.md](MIGRATIONS.md) §0.3.

## Agent presence frusta

After each successful `blender_see`, the Gateway freezes that observation's pose, projection matrix, and dimensions into presence. Moving the viewport later does not move the cone: it stays where the agent looked. Blender draws a blue frustum and a short-id label (A plus a number by default). The Agent Presence section of the N-panel can change the colour, switch to the Gateway's per-agent palette, show or hide short ids, and hide individual cones. Short ids are display-only; the backend identity is a stable hash.

The DSH Web client provides a Blender Presence tab in the right sidebar (also reachable from its guide page), listing each agent's short id, latest activity, and observation mode. Running `/blender-presence` in the composer opens that tab without submitting a message. A `full=true` whole-window capture has no independent observation pose; it shows a transient status-bar notice and never overwrites the previous cone.

## Virtual display (virtual-gui)

On Windows and Linux the Gateway can launch an isolated Blender GUI for the agent. It defaults to `auto` on Windows after the V0 acceptance and is opt-in preview on Linux. Agent starts are still denied by default: the plugin config `allowVirtualGui` defaults to `false`, and a human must enable the switch in the DSH Blender Presence tab (the switch is runtime-only and resets on reload). The tab also shows how many user-started Blender windows are connected. Missing prerequisites never start silently: `blender_session start kind=virtual-gui` returns `stage=config` with the exact command, and `/blender doctor --fix` (or the add-on preferences button) installs only the same plan. One-click is limited to `xvfb` on Ubuntu 22.04/24.04 and Debian 12 through `pkexec`; VirtualGL must come from its official repository, GPU drivers are detected but never installed, and unsupported platforms get the manual command. See [configuration §5.7](docs/配置参考.md).

## Documentation

- Surface algorithm: [skills/blender-modeling/references/曲面算法.md](skills/blender-modeling/references/曲面算法.md)
- Step-by-step guide: [docs/操作教程.md](docs/操作教程.md)
- Configuration: [docs/配置参考.md](docs/配置参考.md)
- AI install prompt: [docs/AI-安装提示词.md](docs/AI-安装提示词.md)
- Deployment SOP: [docs/部署SOP.md](docs/部署SOP.md)
- Third-party checklist: [docs/给第三方的安装清单.md](docs/给第三方的安装清单.md)
- DSH upgrade compatibility: [docs/DSH-更新适配.md](docs/DSH-更新适配.md)
- Architecture: [docs/architecture.md](docs/architecture.md)
- Modeling skill: [skills/blender-modeling/](skills/blender-modeling/)
- Release notes: [GitHub Releases](https://github.com/sixtysevenlf/dsh-blender-plugin/releases) and [CHANGELOG.md](CHANGELOG.md)

## Troubleshooting

| Symptom | What to do |
|---|---|
| doctor does not return kind equal to ok | Check that Blender is running and the addon is connected. For the GUI route this is the only valid acceptance check. |
| No GUI is available | Live interaction needs a GUI session. Submit background work with `blender_run(kind="headless")` and collect it with `action="wait"`/`collect`; rendered frames come from the job outputs. |
| Another session is writing to the same scene | Claim a write scope with `blender_session(action="grant", scopes=...)`, or attach a separate GUI session; parallel jobs are fine through `blender_run`. |
| An old MCP for Blender / blender_mcp addon is still enabled | Disable it (uninstalling is not required) and use the bundled DSH Blender Bridge addon; the 3.1 bridge dials the Gateway, but two Blender-side agent bridges still confuse the host; the legacy MCP bridge listens on 127.0.0.1:9876. |
| Images are generated but not attached | Update to the latest build; the Windows path fix is included. If it persists, open the returned path directly. |
| Followed the steps and it still does not work | Check the bundled addon first: only extension/dsh_blender_bridge.zip is supported, and it must be enabled and connected. |

## Development

- Node.js **24.9** is the minimum supported version; CI runs on Node 24.
- Development environment: `npm run setup:dev` after cloning discovers Python 3.11+, creates or reuses
  `.venv`, installs `requirements-dev.txt`, `requirements-typecheck.txt`, and npm dependencies
  (`npm ci --legacy-peer-deps` only when `node_modules` is missing). `npm run setup:dev -- --check`
  verifies without writing; `npm run setup:dev -- --force` recreates `.venv`. `.venv` and
  `node_modules` are never committed.
- Python tests need a virtualenv with `pytest` (created by `npm run setup:dev`). Tests auto-detect
  `.venv`; point `PYTHON` at another interpreter when needed (`PYTHONIOENCODING=utf-8` and
  `PYTHONUTF8=1` on Windows); tests import the TypeScript sources directly.
- The canonical command table — build, typecheck, test, lint, pack — is
  [`AGENTS.md` §1](AGENTS.md#1-commands-single-source). `lib/` is rebuilt from `runtime/` and `src/`; never hand-edit it.
- Test layers, fixtures, and per-layer commands: [`tests/README.md`](tests/README.md).
- Contribution workflow: [`CONTRIBUTING.md`](CONTRIBUTING.md) · [`docs/zh/CONTRIBUTING.md`](docs/zh/CONTRIBUTING.md).
- Bundled skill source: `skills/blender-modeling/`.

## Support

If this project is useful to you, you can support its development on Afdian (爱发电): <https://afdian.com/a/sixtysevenlf>

## License

The DSH host plugin is BSD-3-Clause (see [LICENSE](LICENSE)). The bundled Blender extension under [
`extension/`](extension/) is GPL-3.0-or-later (see [extension/LICENSE](extension/LICENSE)) and contains
MIT-licensed code derived from [ahujasid/blender-mcp](https://github.com/ahujasid/blender-mcp), whose
copyright and MIT notice are preserved in [extension/NOTICE](extension/NOTICE). The bundled modeling skill is MIT; see
skills/blender-modeling/LICENSE and NOTICE.md. Blender is a trademark of the Blender Foundation.