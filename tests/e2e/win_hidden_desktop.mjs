#!/usr/bin/env node
// End-to-end checks for the Windows hidden-desktop helper (tasks 5.2/5.3).
// Windows only. Builds no source: pass --helper (or use the default build
// output) and optionally --blender for the real GUI case.
//
// Usage:
//   node tests/e2e/win_hidden_desktop.mjs [--helper <exe>] [--blender <exe>] [--json <path>]
//
// The helper is started exactly like the Windows provider will start it. The
// probe Blender script reports through stdout (a Low-labelled Blender cannot
// write up into a Medium temp directory, which is also why job outputs must go
// to a directory the child can write).

import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { spawn, spawnSync } from 'node:child_process'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..')
const args = process.argv.slice(2)
const option = (name) => {
  const index = args.indexOf(name)
  return index >= 0 ? args[index + 1] : ''
}
const helper = resolve(
  option('--helper') ||
    join(root, 'tools', 'win-hidden-desktop', 'out', 'dsh-blender-hidden-desktop.exe'),
)
const blender = option('--blender') ? resolve(option('--blender')) : ''
const jsonPath = option('--json') ? resolve(option('--json')) : ''

const checks = []
const record = (name, ok, detail) => {
  checks.push({ name, ok, detail })
  console.log((ok ? 'PASS ' : 'FAIL ') + name + (detail ? ': ' + detail : ''))
}

if (process.platform !== 'win32') {
  console.error('win_hidden_desktop.mjs requires Windows')
  process.exit(2)
}
if (!existsSync(helper)) {
  console.error('helper not found: ' + helper)
  process.exit(2)
}

const pkg = JSON.parse(readFileSync(join(root, 'package.json'), 'utf8'))
const firstJsonLine = (text) =>
  (text || '')
    .split(/\r?\n/)
    .map((line) => line.trim())
    .find((line) => line.startsWith('{'))

// 1. version
{
  const result = spawnSync(helper, ['--version'], { encoding: 'utf8', timeout: 30000 })
  let info = null
  try {
    info = JSON.parse(firstJsonLine(result.stdout))
  } catch {}
  record(
    'version',
    result.status === 0 &&
      info &&
      info.version === pkg.version &&
      typeof info.commit === 'string' &&
      info.commit.length > 0,
    'version=' + (info && info.version) + ' commit=' + (info && info.commit),
  )
}

// 2. self-test (parsing, quoting, desktop validation, label lowering)
{
  const result = spawnSync(helper, ['--self-test'], { encoding: 'utf8', timeout: 60000 })
  let info = null
  try {
    info = JSON.parse(firstJsonLine(result.stdout))
  } catch {}
  record(
    'self-test',
    result.status === 0 && info && info.ok === true,
    info ? JSON.stringify((info.checks || []).filter((check) => !check.ok)) : 'no output',
  )
}

// 3. exit code proxy
{
  const result = spawnSync(
    helper,
    [
      '--exe',
      process.env.ComSpec || 'C:\\Windows\\System32\\cmd.exe',
      '--desktop',
      'dsh-e2e-exit',
      '--',
      '/c',
      'exit',
      '42',
    ],
    { encoding: 'utf8', timeout: 60000 },
  )
  let ready = null
  try {
    ready = JSON.parse(firstJsonLine(result.stdout))
  } catch {}
  record(
    'exit-code-proxy',
    result.status === 42 && ready && ready.event === 'ready',
    'status=' + result.status,
  )
}

// 4. failure path never starts the child (bad desktop name)
{
  const result = spawnSync(
    helper,
    [
      '--exe',
      process.env.ComSpec || 'C:\\Windows\\System32\\cmd.exe',
      '--desktop',
      'bad\\name',
      '--',
      '/c',
      'exit',
      '0',
    ],
    { encoding: 'utf8', timeout: 30000 },
  )
  let error = null
  try {
    error = JSON.parse(firstJsonLine(result.stdout))
  } catch {}
  record(
    'failure-path',
    result.status !== 0 &&
      error &&
      error.event === 'error' &&
      error.code === 'bad_desktop_name' &&
      !result.stdout.includes('"ready"'),
    'status=' + result.status + ' code=' + (error && error.code),
  )
}

// 5. optional real GUI case
async function guiCase() {
  const probeLines = [
    'import bpy, ctypes, json, os',
    'from ctypes import wintypes',
    "user32 = ctypes.WinDLL('user32', use_last_error=True)",
    'def desktop_name(handle):',
    '    if not handle:',
    '        return None',
    '    buffer = ctypes.create_unicode_buffer(256)',
    '    needed = wintypes.DWORD()',
    '    if user32.GetUserObjectInformationW(handle, 2, buffer, ctypes.sizeof(buffer), ctypes.byref(needed)):',
    '        return buffer.value',
    '    return None',
    'class WINDOWPLACEMENT(ctypes.Structure):',
    '    _fields_ = [',
    "        ('length', wintypes.UINT),",
    "        ('flags', wintypes.UINT),",
    "        ('showCmd', wintypes.UINT),",
    "        ('ptMinPosition', wintypes.POINT),",
    "        ('ptMaxPosition', wintypes.POINT),",
    "        ('rcNormalPosition', wintypes.RECT),",
    '    ]',
    'windows = []',
    'by_thread = {}',
    'WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)',
    'def callback(hwnd, lparam):',
    '    pid = wintypes.DWORD()',
    '    thread_id = user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))',
    '    if pid.value != os.getpid():',
    '        return True',
    '    title = ctypes.create_unicode_buffer(512)',
    '    user32.GetWindowTextW(hwnd, title, 512)',
    '    cls = ctypes.create_unicode_buffer(256)',
    '    user32.GetClassNameW(hwnd, cls, 256)',
    '    placement = WINDOWPLACEMENT()',
    '    placement.length = ctypes.sizeof(WINDOWPLACEMENT)',
    '    user32.GetWindowPlacement(hwnd, ctypes.byref(placement))',
    '    if thread_id not in by_thread:',
    '        by_thread[thread_id] = desktop_name(user32.GetThreadDesktop(thread_id))',
    '    windows.append({',
    '        "title": title.value,',
    '        "class": cls.value,',
    '        "visible": bool(user32.IsWindowVisible(hwnd)),',
    '        "iconic": bool(user32.IsIconic(hwnd)),',
    '        "showCmd": placement.showCmd,',
    '        "desktop": by_thread[thread_id],',
    '    })',
    '    return True',
    'user32.EnumWindows(WNDENUMPROC(callback), 0)',
    'info = {"background": bool(bpy.app.background), "windows": windows}',
    'try:',
    '    import gpu',
    '    info["vendor"] = str(gpu.platform.vendor_get())',
    '    info["renderer"] = str(gpu.platform.renderer_get())',
    '    info["gl"] = str(gpu.platform.version_get())',
    'except Exception as error:',
    '    info["gpu_error"] = str(error)',
    "print('DSH_PROBE ' + json.dumps(info), flush=True)",
    'bpy.ops.wm.quit_blender()',
    '',
  ].join('\n')

  const dir = mkdtempSync(join(tmpdir(), 'dsh-vgui-e2e-'))
  const scriptPath = join(dir, 'probe.py')
  writeFileSync(scriptPath, probeLines)
  const desktop = 'dsh-e2e-gui'
  const child = spawn(
    helper,
    ['--exe', blender, '--desktop', desktop, '--', '--factory-startup', '--python', scriptPath],
    { stdio: ['ignore', 'pipe', 'pipe'] },
  )
  let stdout = ''
  let stderr = ''
  child.stdout.on('data', (chunk) => (stdout += chunk))
  child.stderr.on('data', (chunk) => (stderr += chunk))
  let ready = null
  let probe = null
  const startedAt = Date.now()
  while (Date.now() - startedAt < 90000) {
    if (!ready) {
      const line = firstJsonLine(stdout)
      if (line) {
        try {
          ready = JSON.parse(line)
        } catch {}
      }
    }
    const probeLine = stdout
      .split(/\r?\n/)
      .map((line) => line.trim())
      .find((line) => line.startsWith('DSH_PROBE '))
    if (probeLine) {
      try {
        probe = JSON.parse(probeLine.slice('DSH_PROBE '.length))
      } catch {}
      if (probe) break
    }
    await new Promise((resolve) => setTimeout(resolve, 200))
  }
  const elapsed = (Date.now() - startedAt) / 1000
  child.kill()
  await new Promise((resolve) => setTimeout(resolve, 1500))
  let leftover = false
  if (ready && ready.blender) {
    const tasklist = spawnSync('tasklist', ['/FI', 'PID eq ' + ready.blender, '/NH'], {
      encoding: 'utf8',
    })
    leftover = (tasklist.stdout || '').toLowerCase().includes('blender')
  }
  const windows = (probe && probe.windows) || []
  const main = windows.find((window) => window.class === 'GHOST_WindowClass')
  record(
    'gui-ready',
    Boolean(ready && ready.event === 'ready'),
    'display=' + (ready && ready.display),
  )
  if (!probe) {
    console.log('helper stderr: ' + stderr.slice(-2000))
    console.log('child stdout tail: ' + stdout.slice(-2000))
  }
  record(
    'gui-background-false',
    Boolean(probe && probe.background === false),
    'background=' + (probe && probe.background),
  )
  record(
    'gui-hardware-renderer',
    Boolean(probe && probe.renderer && !/llvmpipe|software|basic render/i.test(probe.renderer)),
    'renderer=' + (probe && probe.renderer),
  )
  record(
    'gui-not-minimized',
    Boolean(main && main.iconic === false && main.showCmd !== 2),
    'showCmd=' + (main && main.showCmd) + ' iconic=' + (main && main.iconic),
  )
  record(
    'gui-hidden-desktop',
    windows.length > 0 && windows.every((window) => window.desktop === desktop),
    'desktops=' + JSON.stringify([...new Set(windows.map((window) => window.desktop))]),
  )
  record(
    'gui-no-orphan',
    !leftover,
    'blenderPid=' + (ready && ready.blender) + ' leftover=' + leftover,
  )
  record('gui-elapsed', elapsed < 90, elapsed.toFixed(1) + 's')
  rmSync(dir, { recursive: true, force: true })
  return { ready, probe, stderr, elapsed }
}

let gui = null
if (blender) {
  if (!existsSync(blender)) {
    record('gui-blender-exists', false, blender)
  } else {
    gui = await guiCase()
  }
} else {
  console.log('SKIP gui (no --blender)')
}

if (jsonPath) {
  writeFileSync(jsonPath, JSON.stringify({ helper, blender, gui, checks }, null, 2) + '\n')
}
const failed = checks.filter((check) => !check.ok)
console.log('\n' + (checks.length - failed.length) + '/' + checks.length + ' checks passed')
if (failed.length > 0) {
  process.exit(1)
}
