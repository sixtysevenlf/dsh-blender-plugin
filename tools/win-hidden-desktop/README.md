# Windows hidden-desktop helper

`dsh-blender-hidden-desktop.exe` launches one Blender GUI process on a private
Win32 desktop, so virtual-gui sessions never appear in the user's taskbar,
Alt+Tab, or screen capture. The Gateway (`runtime/display`) starts one helper
per virtual-gui session and talks to the Blender addon through the normal
reverse connection; the helper only owns the process, desktop, and job object.

## Layout

| Path | Purpose |
|---|---|
| `helper.cpp` | C++17 source; Win32 only, no third-party dependencies |
| `out/` | Build output (gitignored, never committed) |

## Build

Requirements: Windows + Visual Studio 2022 Build Tools with the component
`Microsoft.VisualStudio.Component.VC.Tools.x86.x64` (MSVC x64). CI builds the
helper on a Windows runner with the same script.

    npm run build:win-helper

The script locates `vswhere.exe`, runs `vcvars64.bat`, compiles with
`/std:c++17 /EHsc /O2 /MT /W4`, writes a generated `build-info.h` with the
package version and git commit, and verifies the binary with `--version` and
`--self-test`. Output: `out/dsh-blender-hidden-desktop.exe` and
`out/build.json` (sha256, size, version, commit).

## Protocol

stdout carries one JSON line first:

- ready: `{"event":"ready","helper":<pid>,"blender":<pid>,"display":"...","exe":"..."}`
- error: `{"event":"error","stage":"...","code":"...","message":"...","win32":<code>}`

After the ready line the child's stdout and stderr are forwarded byte for byte.
The helper exit code is the Blender exit code; helper failures use 2-9.

Arguments:

    dsh-blender-hidden-desktop --exe <blender.exe> [--desktop <name>] [--cwd <dir>]
        [--parent-pid <pid>] [-- <blender args...>]

`--parent-pid` enables a watchdog: when the parent process dies, the job object
is terminated and Blender is killed. The private desktop is created with
`CreateDesktopW`; a failure is reported and Blender is not started (there is no
fallback to the default desktop). `--desktop` names are validated (1-180
characters, no backslash, slash, or control characters).

## Manual checks

    out\dsh-blender-hidden-desktop.exe --version
    out\dsh-blender-hidden-desktop.exe --self-test

Failure path (must print an error event and start no Blender):

    out\dsh-blender-hidden-desktop.exe --exe <blender.exe> --desktop bad\name

Real GUI run (the helper exits with Blender's exit code):

    out\dsh-blender-hidden-desktop.exe --exe <blender.exe> -- --factory-startup

Verify with Task Manager that `blender.exe` exists while no Blender window is
visible; the addon connects to the Gateway as usual.

## Integrity labels

Windows applies an image file's mandatory integrity label to the child token:
a Low-labelled `blender.exe` (for example an install extracted by a sandboxed
tool) runs at Low even when the helper is Medium. A hidden desktop is created at
the helper's own integrity level, and a Low process cannot write up to a Medium
desktop; the child then fails in user32 initialization with
`0xC0000142 (STATUS_DLL_INIT_FAILED)`.

The helper therefore reads the child image's label, lowers the hidden desktop's
label to `min(helper, image)` before creating the process, and fails closed with
`desktop_label_failed` if the label cannot be lowered. It never retries on the
default desktop. The chosen levels are logged on stderr:

    [dsh-helper] integrity helper=0x2000 image=0x1000 desired=0x1000

## Notes

- The helper is a build artifact. Do not commit `out/` or the executable.
- It never opens a default desktop, never runs a shell, and passes arguments to
  Blender with CommandLineToArgvW quoting rules.
- `--self-test` creates and closes a temporary desktop, validates argument
  parsing/quoting/JSON escaping, and checks that an invalid desktop name fails.
