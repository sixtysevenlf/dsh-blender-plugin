# DSH × Blender

[简体中文](README.zh-CN.md)

DSH × Blender connects DeepSeek Harness to Blender so an agent can build, inspect, validate, and export 3D scenes in a real Blender session. The normal workflow is GUI-first: start Blender, connect the addon, and let the agent look, change, and verify. When no GUI is available, a persistent headless session provides the same interactive loop, including frames.

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
- **Headless fallback with visual feedback.** If Blender has no GUI, start a persistent worker and the agent can still execute code and get Workbench frames. This is a fallback path, not the default; it exists so the agent is never blind.
- **Reference-image measurement.** Measure silhouettes, holes, symmetry, readable features, and scale from a reference image before modeling. If no scale is known, the tools return pixel units instead of inventing millimeters.
- **Parametric surfaces.** Build reproducible surface meshes from sections (loft), a profile swept along a rail, or a four-sided patch; run continuity, curvature, self-intersection, and thickness checks; then export GLB. The same spec yields the same `spec_hash` and vertices instead of hand-sculpted geometry.
- **Assembly audit and repair.** Validate an assembly manifest, classify clashes, hidden overlaps, contacts, gaps, floating parts, and contained parts, apply safe repairs, gate the result, and export GLB, FBX, or OBJ with a manifest and merge ledger.
- **Blockout and video-reference packs.** Build low-poly blockouts from declarative parts, render orthographic and perspective reference views, and produce a turntable MP4 for video models such as Seedance.
- **Visual acceptance and delivery gates.** Compare renders against references from the same camera, report defects with crops and measurements, run defect-injection evaluation, and block delivery when the gate is not pass.
- **Automation and long jobs.** Run one-shot headless scripts, long background jobs, parameter searches, and batch validation without occupying the GUI.
- **Native selection.** Select objects or collections in Blender and send them into the current DSH draft.
- **Works in dsh-TUI too.** In dsh-TUI, a Blender selection is attached to your next message as a context chip; the footer shows the Blender channel state (connected / AI paused / rendering / jobs); `@` mentions scene objects, materials, and collections; and `/blender status|doctor|jobs` checks the channel without going through the model. Each part needs the matching dsh-TUI seam and stays off when the seam is missing.

## Quick start

**Before anything else: install the bundled addon first.** Use only the addon shipped in this repository: **addon/dsh_blender_bridge.py**. Do not install MCP for Blender or any other Blender addon. Install and enable this addon in Blender before the other steps. If the installation below does not work, first check that this exact addon is installed, enabled, and connected.

### Requirements

- DSH (DeepSeek Harness) installed and working.
- Node.js 20 or newer for the backend.
- Blender 4.x or 5.x. GUI mode requires the bundled addon (install it first); headless mode only needs the Blender executable.
- Optional: a DSH source checkout if you want to rebuild the plugin.

### Option A: ask an AI agent

Copy this prompt into an agent that can run commands:

    First install and enable the bundled addon addon/dsh_blender_bridge.py
    from this repository; do not use MCP for Blender or any other addon.
    Then install the DSH × Blender plugin from
    https://github.com/sixtysevenlf/dsh-blender-plugin
    for my DSH profile. Read docs/AI-安装提示词.md first, follow it exactly,
    and finish with the acceptance check: doctor must return kind=ok for the
    GUI route, or worker start + see must return a frame for the headless-only route.
    Do not skip the check and do not claim success without it.

Fetch the full install prompt locally:

    curl -fsSL https://raw.githubusercontent.com/sixtysevenlf/dsh-blender-plugin/main/docs/AI-安装提示词.md -o AI-安装提示词.md

Install the standalone modeling skill (only needed for hosts that do not load the plugin; DSH gets it bundled):

    curl -fsSL https://raw.githubusercontent.com/sixtysevenlf/dsh-blender-plugin/main/skills/blender-modeling/install.sh | bash

### Option B: manual setup

1. **Install the bundled addon first.**

   Download **addon/dsh_blender_bridge.py** from the addon/ folder of this repository. In Blender, open Edit > Preferences > Add-ons > Install, select that file, enable DSH Blender Bridge, open the N panel in the 3D view, and click Connect. The addon listens on 127.0.0.1:9876.

   Use only this addon. Disable MCP for Blender or any other Blender addon before continuing. If the rest of the installation does not work, check this step first.

2. Clone the repository and, if needed, build it. The repository commits the built lib directory, so a normal clone is ready to use.

        git clone https://github.com/sixtysevenlf/dsh-blender-plugin.git
        cd dsh-blender-plugin
        # Optional: only after changing src/
        DSH_CHECKOUT=/path/to/dsh-harness bash scripts/build.sh

3. Add the package to your DSH profile as a bundle. Link the checkout into the profile, then add the dependency and bundle entry to the profile package.json.

        ln -s /absolute/path/to/dsh-blender-plugin <profile>/node_modules/@dsh-external/dsh-blender-plugin

    Profile package.json:

        {
          "dependencies": {
            "@dsh-external/dsh-blender-plugin": "link:/absolute/path/to/dsh-blender-plugin"
          },
          "dsh": {
            "profile": {
              "bundles": ["@dsh-external/dsh-blender-plugin"]
            }
          }
        }

    Validate the profile and restart DSH:

        dsh --profile <profile> --dump-config

4. Verify the installation.

    GUI route:

        curl -sS http://127.0.0.1:9877/health
        curl -sS http://127.0.0.1:9877/doctor

    The only acceptance criterion for the GUI route is doctor returning kind equal to ok. If it does not, the plugin is not installed correctly.

    After DSH restarts, you can also use blender_viewport with op equal to launch. It starts Blender, enables the addon, and connects automatically.

    Headless-only route (no addon):

        blender_rt_worker(op="start", name="smoke")
        blender_rt_worker(op="see", name="smoke")

    A returned frame means the headless fallback is working. The doctor bpy round-trip is expected to be unreachable in this mode.

## Tools

The plugin exposes 15 tools. All planning, audit, assembly, export, and evaluation operations live under blender_rt_plan, so the tool list stays small.

### Interaction and control

| Tool | What it gives you |
|---|---|
| blender_rt_see | A frame of the current GUI viewport or a custom camera angle. |
| blender_rt_do | Runs Python in Blender with a persistent kernel; can return a frame with the result. |
| blender_rt_watch | Samples a time window or frame range and reports whether the image changed. |
| blender_rt_worker | A persistent headless session for interactive work when no GUI is available. |

### Planning and operations

| Tool | What it gives you |
|---|---|
| blender_rt_plan | The capability catalog and the execution surface for modeling, audit, assembly, delivery, and evaluation operations. Start here when you are unsure which operation to use. |
| blender_viewport | Channel health, doctor, launch, and write-lease operations for coordinating multiple sessions. |

### Automation and jobs

| Tool | What it gives you |
|---|---|
| blender_rt_headless | Runs a standalone Blender background process for batch work, validation, or rendering. |
| blender_rt_job | Starts long work in the background and collects the result by job id; a timeout does not lose the result. |
| blender_rt_loop | Runs a parameter search loop inside Blender and returns the best candidates. |

### Scene utilities

| Tool | What it gives you |
|---|---|
| blender_rt_txn | File-level snapshots and object-level marks for rollback. |
| blender_rt_preset | Saves, lists, applies, and exports reusable parameter presets. |
| blender_rt_perf | Diagnoses and applies render performance settings. |
| blender_rt_opt | Finds safe object merges to reduce scene overhead. |

### Addon access

| Tool | What it gives you |
|---|---|
| blender_rt_cmd | Calls any addon command directly. |
| blender_rt_commands | Lists available addon commands and integration states. |

## Documentation

- Surface algorithm: docs/曲面算法.md
- Step-by-step guide: docs/操作教程.md
- Configuration: docs/配置参考.md
- AI install prompt: docs/AI-安装提示词.md
- Deployment SOP: docs/部署SOP.md
- Third-party checklist: docs/给第三方的安装清单.md
- Modeling skill: skills/blender-modeling/
- Release notes: GitHub Releases and CHANGELOG.md

## Troubleshooting

| Symptom | What to do |
|---|---|
| doctor does not return kind equal to ok | Check that Blender is running and the addon is connected. For the GUI route this is the only valid acceptance check. |
| No GUI is available | Use the persistent worker fallback: start a worker, then run code and request a frame. |
| Another session is writing to the same scene | Take the write lease with blender_viewport, or use separate headless workers for parallel work. |
| An old MCP for Blender addon is installed | Disable it and install the bundled DSH Blender Bridge addon instead. |
| Images are generated but not attached | Update to the latest build; the Windows path fix is included. If it persists, open the returned path directly. |
| Followed the steps and it still does not work | Check the bundled addon first: only addon/dsh_blender_bridge.py is supported, and it must be enabled and connected. |

## Development

- Run the offline test suite: npm test
- Rebuild after changing src: DSH_CHECKOUT=/path/to/dsh-harness bash scripts/build.sh
- Bundled skill source: skills/blender-modeling/

## Support

If this project is useful to you, you can support its development on Afdian (爱发电): <https://afdian.com/a/sixtysevenlf>

## License

The plugin is BSD-3-Clause. The bundled modeling skill is MIT; see skills/blender-modeling/LICENSE and NOTICE.md. Blender is a trademark of the Blender Foundation.
