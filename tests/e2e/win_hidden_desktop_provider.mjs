#!/usr/bin/env node
// End-to-end checks for the Windows hidden-desktop DisplayProvider (task 5.4).
//
// Windows only, manual: requires a Blender 4.2.10 LTS executable and a built
// lib/ (npm run build). It starts Blender through runtime/display/win-hidden-
// desktop.ts exactly like blender_session start will, then verifies the GUI
// facts the task asks for and that two providers stay isolated.
//
// Usage:
//   node tests/e2e/win_hidden_desktop_provider.mjs --blender <blender.exe> [--json <path>]
//
// The probe reports through stdout (a Low-labelled Blender cannot write up into
// a Medium temp directory). Blender is stopped only through provider.stop().

import { existsSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { spawnSync } from 'node:child_process'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..')
const argv = process.argv.slice(2)
const option = (name) => {
  const index = argv.indexOf(name)
  return index >= 0 ? argv[index + 1] : ''
}
const blenderArg = option('--blender') || process.env.DSH_BLENDER_EXE || ''
const blender = blenderArg ? resolve(blenderArg) : ''
const jsonPath = option('--json') ? resolve(option('--json')) : ''

const checks = []
const record = (name, ok, detail) => {
  checks.push({ name, ok, detail })
  console.log((ok ? 'PASS ' : 'FAIL ') + name + (detail ? ': ' + detail : ''))
}

if (process.platform !== 'win32') {
  console.error('win_hidden_desktop_provider.mjs requires Windows')
  process.exit(2)
}
if (!blender || !existsSync(blender)) {
  console.error('pass --blender <blender.exe> or set DSH_BLENDER_EXE')
  process.exit(2)
}
const libPath = join(root, 'lib', 'runtime', 'display', 'win-hidden-desktop.js')
if (!existsSync(libPath)) {
  console.error('built provider not found; run npm run build first: ' + libPath)
  process.exit(2)
}

const { WinHiddenDesktopProvider } = await import(pathToFileURL(libPath).href)

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
  '',
].join('\n')

const probeDir = mkdtempSync(join(tmpdir(), 'dsh-provider-54-'))
const probePath = join(probeDir, 'probe.py')
writeFileSync(probePath, probeLines)

const provider = new WinHiddenDesktopProvider({ blenderExe: blender })
const started = []

const parseProbe = (instance) => {
  const output = provider.output(instance)
  if (!output) return null
  const line = output.stdout.find((text) => text.trim().startsWith('DSH_PROBE '))
  if (!line) return null
  try {
    return JSON.parse(line.trim().slice('DSH_PROBE '.length))
  } catch (error) {
    return null
  }
}

async function waitProbe(instance, timeoutMs) {
  const until = Date.now() + timeoutMs
  while (Date.now() < until) {
    const probe = parseProbe(instance)
    if (probe) return probe
    await new Promise((resolve) => setTimeout(resolve, 200))
  }
  return null
}

function tasklistHas(pid) {
  if (!pid) return false
  const result = spawnSync('tasklist', ['/FI', 'PID eq ' + pid, '/NH'], { encoding: 'utf8' })
  return (result.stdout || '').toLowerCase().includes('blender.exe')
}

async function runOne(sessionId, waitMs) {
  const start = Date.now()
  const instance = await provider.start({
    sessionId,
    blenderArgs: ['--python', probePath],
  })
  started.push(instance)
  const readyElapsed = (Date.now() - start) / 1000
  const probe = await waitProbe(instance, waitMs)
  const health = await provider.health(instance)
  return { instance, probe, health, readyElapsed }
}

const failDetail = (value) => {
  try {
    return JSON.stringify(value)
  } catch (error) {
    return String(value)
  }
}

let first = null
let second = null
try {
  first = await runOne('e2e-54-a', 60000)
  record(
    'provider-start',
    Boolean(first.instance && first.instance.display.startsWith('dsh-vgui-')),
    'display=' +
      (first.instance && first.instance.display) +
      ' pid=' +
      (first.instance && first.instance.pid),
  )
  record('provider-ready-under-60s', first.readyElapsed < 60, first.readyElapsed.toFixed(1) + 's')
  record('provider-health-ok', first.health.ok, first.health.detail)
  if (!first.probe) {
    console.log('provider stderr tail: ' + failDetail(provider.output(first.instance)?.stderr))
    console.log('provider stdout tail: ' + failDetail(provider.output(first.instance)?.stdout))
  }
  record(
    'gui-background-false',
    Boolean(first.probe && first.probe.background === false),
    'background=' + (first.probe && first.probe.background),
  )
  record(
    'gui-hardware-renderer',
    Boolean(
      first.probe &&
      first.probe.renderer &&
      !/llvmpipe|software|basic render/i.test(first.probe.renderer),
    ),
    'renderer=' + (first.probe && first.probe.renderer),
  )
  const windows = (first.probe && first.probe.windows) || []
  const main = windows.find((window) => window.class === 'GHOST_WindowClass')
  record(
    'gui-not-minimized',
    Boolean(main && main.iconic === false && main.showCmd !== 2),
    'showCmd=' + (main && main.showCmd) + ' iconic=' + (main && main.iconic),
  )
  record(
    'gui-hidden-from-default-desktop',
    windows.length > 0 && windows.every((window) => window.desktop === first.instance.display),
    'desktops=' + JSON.stringify([...new Set(windows.map((window) => window.desktop))]),
  )

  second = await runOne('e2e-54-b', 60000)
  record(
    'two-displays-are-distinct',
    Boolean(
      second.instance &&
      second.instance.display !== first.instance.display &&
      second.instance.pid !== first.instance.pid,
    ),
    'a=' + first.instance.display + ' b=' + second.instance.display,
  )
  record(
    'two-backgrounds-false',
    Boolean(
      first.probe &&
      second.probe &&
      first.probe.background === false &&
      second.probe.background === false,
    ),
    'a=' +
      (first.probe && first.probe.background) +
      ' b=' +
      (second.probe && second.probe.background),
  )

  await provider.stop(first.instance)
  record('stop-a-no-orphan', !tasklistHas(first.instance.pid), 'blenderPid=' + first.instance.pid)
  await provider.stop(second.instance)
  record('stop-b-no-orphan', !tasklistHas(second.instance.pid), 'blenderPid=' + second.instance.pid)
  await expectStopIdempotent()
} catch (error) {
  record('provider-e2e', false, error && error.stack ? error.stack : String(error))
} finally {
  for (const instance of started) {
    try {
      await provider.stop(instance)
    } catch (error) {
      /* best effort cleanup */
    }
  }
  rmSync(probeDir, { recursive: true, force: true })
}

async function expectStopIdempotent() {
  try {
    await provider.stop(first.instance)
    record('stop-is-idempotent', true, 'second stop returned')
  } catch (error) {
    record('stop-is-idempotent', false, String(error))
  }
}

if (jsonPath) {
  writeFileSync(
    jsonPath,
    JSON.stringify(
      {
        blender,
        first: first && {
          instance: first.instance,
          probe: first.probe,
          health: first.health,
        },
        second: second && second.instance,
        checks,
      },
      null,
      2,
    ) + '\n',
  )
}
const failed = checks.filter((check) => !check.ok)
console.log('\n' + (checks.length - failed.length) + '/' + checks.length + ' checks passed')
if (failed.length > 0) process.exit(1)
