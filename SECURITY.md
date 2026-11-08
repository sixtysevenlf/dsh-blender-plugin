# Security Policy

[简体中文](docs/zh/SECURITY.md)

Security fixes are provided for the **latest release only** (see the `version` field in `package.json`).
Upgrade older versions first.

## Reporting a vulnerability

Please **do not** report security issues in public issues. Use GitHub's private channel instead:

1. <https://github.com/sixtysevenlf/dsh-blender-plugin/security/advisories/new>
   (repository -> **Security** -> **Advisories** -> *Report a vulnerability*).
2. If that channel is unavailable, open an issue containing **only** the sentence
   "I would like to report a security issue; please provide a private contact." Do **not** include details.

Please include the affected version, reproduction steps, impact (who can trigger it and what
preconditions are required), and a proof of concept if available. We aim to respond within 7 days.
After a fix is released, we will credit you in the advisory unless you ask to stay anonymous.

## Execution surface (consider it when assessing trust)

This plugin **by design** executes code and starts processes; it is not a read-only data plugin:

| Surface | Description |
|---|---|
| Blender-side Python | `blender_py` (GUI session) and `blender_run(kind="headless")` pass caller-provided Python to Blender's `bpy`; `K` is a persistent kernel inside a GUI session |
| Local processes | Starts the backend `node lib/runtime/gateway.js` (after `npm run build`), headless `blender -b`, and external validators under `runtime/ext/` (glTF-Validator / open3d); uses `SIGTERM` then `SIGKILL` to clean up a residual backend when needed |
| Local network | The Gateway listens only on `127.0.0.1` (default 39877). The Blender add-on dials the Gateway over WebSocket and opens no inbound port. The plugin itself makes **no** telemetry calls besides that local control channel |
| File I/O | Default work directory is `~/.dsh-blender-rt` (override with `DSH_BLENDER_*` environment variables or a root `dsh-blender.config.json`): renders, logs, and the job ledger |
| Gate expressions | `gate_run`'s `pass_if` / `degrade_if` are **restricted expressions**: first checked against the syntax whitelist in `runtime/py/core/gate.py` (`_check_ast`), then evaluated node by node (`_eval_node`); **no `eval` / `exec`**; underscore attributes are denied; functions are limited to `min` / `max` / `len` / `abs` / `round` / `all` / `any` / `sum` / `float` / `int` / `str` |
| Credentials | The host plugin does **not** read, store, or transmit any API key, token, or credential file. The bundled addon offers optional asset-integration credentials (Hyper3D, Sketchfab, Poly Pizza, Hunyuan3D) and keeps them in Blender add-on preferences as password fields; they can also come from `DSHBB_*` environment variables (legacy `BLENDERMCP_*` names are still read) and are sent only to the partner API you invoke |
| Telemetry | None. The bundled add-on has no telemetry, analytics, consent preference, or manual-edit event buffer; activity state is computed in memory and never persisted or sent off the machine |

## Third-party dependencies / Blender addon

- The Blender side requires the addon bundled with this repository (`DSH Blender Bridge` v2.0.0 / protocol 12),
  shipped as `extension/dsh_blender_bridge.zip` and built from `extension/dsh_blender_bridge/` in this repository.
  The old `MCP for Blender` / category-action implementation is **not** part of this repository and has been
  unsupported since v2.3; do not install it for this plugin.
- The release package is produced by `npm pack` and contains only the files in the `files` whitelist in
  `package.json`. Build dependencies come from the DSH checkout; runtime dependencies are injected by
  the host (`peerDependencies`).
- We recommend enabling Dependabot for this repository (see `.github/dependabot.yml`).
