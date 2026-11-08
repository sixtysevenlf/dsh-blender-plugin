// One prerequisites table shared by capabilities, doctor and the pre-spawn gate.
// Detection is local and side-effect free (no package manager, no sudo).
import fs from 'node:fs'
import path from 'node:path'
import { CFG } from '../config.js'
import { resolveVirtualGuiFeature, VIRTUAL_GUI_V0_RELEASED } from './feature.js'
import { resolveHiddenDesktopHelper } from './helper-path.js'
import type {
  DisplayCapabilities,
  DisplayPrerequisiteSpec,
  DisplayPrerequisiteStatus,
} from './types.js'

const WINDOWS: DisplayPrerequisiteSpec[] = [
  {
    id: 'win-hidden-desktop-helper',
    label: 'Windows hidden-desktop helper',
    check: 'bundled dsh-blender-hidden-desktop helper is present',
    install: 'install a 3.1 build that ships the hidden-desktop helper (task 5.2/5.3)',
    needsRoot: false,
  },
  {
    id: 'blender',
    label: 'Blender 4.2.10 LTS',
    check: 'DSH_BLENDER_EXE or auto-detected blender.exe exists',
    install: 'set DSH_BLENDER_EXE or install Blender 4.2.10 LTS',
    needsRoot: false,
  },
]

const LINUX: DisplayPrerequisiteSpec[] = [
  {
    id: 'xvfb',
    label: 'Xvfb',
    check: 'Xvfb is on PATH',
    install: 'Ubuntu/Debian: sudo apt install xvfb; Fedora: sudo dnf install xorg-x11-server-Xvfb',
    needsRoot: true,
  },
  {
    id: 'virtualgl',
    label: 'VirtualGL (vglrun)',
    check:
      'vglrun is on PATH (required for hardware GL; without it Linux runs preview-only llvmpipe)',
    install:
      'add the official VirtualGL APT repository (github.com/VirtualGL/repo), run apt update, then sudo apt install virtualgl; Ubuntu 22.04/24.04 and Debian 12 package archives have no virtualgl package, so apt install virtualgl without that repository is not a valid step',
    needsRoot: true,
    optional: true,
  },
  {
    id: 'gpu-device',
    label: '/dev/dri readable by the Gateway user',
    check: '/dev/dri exists and a render node is readable (required for hardware GL)',
    install:
      'add the Gateway user to the video/render group; fix udev permissions instead of running as root',
    needsRoot: true,
    optional: true,
  },
  {
    id: 'blender',
    label: 'Blender 4.2.10 LTS',
    check: 'DSH_BLENDER_EXE or PATH blender exists',
    install: 'set DSH_BLENDER_EXE to the Blender 4.2.10 LTS binary',
    needsRoot: false,
  },
]

const MAC: DisplayPrerequisiteSpec[] = [
  {
    id: 'mac-provider',
    label: 'virtual-gui provider',
    check: 'a macOS provider is implemented',
    install: 'use user-gui or headless render on macOS; virtual-gui is not supported in 3.1',
    needsRoot: false,
  },
]

export function prerequisiteTable(platform: string): DisplayPrerequisiteSpec[] {
  if (platform === 'win32') return WINDOWS
  if (platform === 'linux') return LINUX
  return MAC
}

export function onPath(command: string, env: NodeJS.ProcessEnv): string | null {
  const exts = process.platform === 'win32' ? ['', '.exe', '.cmd', '.bat'] : ['']
  for (const dir of String(env.PATH || '').split(path.delimiter)) {
    if (!dir) continue
    for (const ext of exts) {
      const candidate = path.join(dir, command + ext)
      try {
        if (fs.statSync(candidate).isFile()) return candidate
      } catch (e) {
        /* not here */
      }
    }
  }
  return null
}

export function canReadLinuxGpu(): { ok: boolean; detail: string } {
  try {
    const names = fs.readdirSync('/dev/dri')
    const node = names.find((n) => n.startsWith('renderD'))
    if (!node) return { ok: false, detail: '/dev/dri has no renderD node' }
    fs.accessSync(path.join('/dev/dri', node), fs.constants.R_OK)
    return { ok: true, detail: '/dev/dri/' + node + ' readable' }
  } catch (e) {
    return { ok: false, detail: 'cannot read /dev/dri: ' + String((e as Error).message || e) }
  }
}

function probeOne(
  spec: DisplayPrerequisiteSpec,
  platform: string,
  env: NodeJS.ProcessEnv,
  overrides: Record<string, boolean> = {},
  blenderExe?: string,
): DisplayPrerequisiteStatus {
  if (Object.prototype.hasOwnProperty.call(overrides, spec.id))
    return { ...spec, ok: overrides[spec.id] === true, detail: 'test override' }
  if (spec.id === 'blender') {
    const exe = String(blenderExe || CFG.blenderExe || '')
    const ok = Boolean(exe) && fs.existsSync(exe)
    return { ...spec, ok, detail: ok ? exe : 'blender executable not found' }
  }
  if (spec.id === 'xvfb') {
    const found = onPath('Xvfb', env)
    return { ...spec, ok: Boolean(found), detail: found || 'Xvfb not on PATH' }
  }
  if (spec.id === 'virtualgl') {
    const found = onPath('vglrun', env)
    return {
      ...spec,
      ok: Boolean(found),
      detail: found || 'vglrun not on PATH (VirtualGL is not required on Windows)',
    }
  }
  if (spec.id === 'gpu-device') {
    const gpu = canReadLinuxGpu()
    return { ...spec, ok: gpu.ok, detail: gpu.detail }
  }
  if (spec.id === 'win-hidden-desktop-helper') {
    // The packaged helper lives in vendor/; a development checkout may have it
    // under tools/.../out. The resolver owns that order and the env override.
    const helper = resolveHiddenDesktopHelper({ env })
    return {
      ...spec,
      ok: Boolean(helper.path),
      detail: helper.detail,
    }
  }
  return {
    ...spec,
    ok: false,
    detail: platform === 'darwin' ? 'not implemented for macOS' : 'not implemented',
  }
}

export interface DisplayCapabilityOptions {
  platform?: string
  configured?: unknown
  source?: string
  env?: NodeJS.ProcessEnv
  providerImplemented?: boolean
  /** Test seam: override one probe result without touching the machine. */
  checks?: Record<string, boolean>
  blenderExe?: string
}

export function buildDisplayCapabilities(
  options: DisplayCapabilityOptions = {},
): DisplayCapabilities {
  const platform = options.platform || process.platform
  const env = options.env || process.env
  const feature = resolveVirtualGuiFeature({
    configured: options.configured,
    source: options.source,
    platform,
    v0Released: VIRTUAL_GUI_V0_RELEASED,
  })
  const prerequisites = prerequisiteTable(platform).map((spec) =>
    probeOne(spec, platform, env, options.checks, options.blenderExe),
  )
  // Optional rows (Linux VirtualGL and /dev/dri) name a hardware-GL degradation.
  // They are reported in `missing` but do not gate a preview-only start: a
  // software-GL provider MUST be labeled preview-only instead of pretending.
  const missingRequired = prerequisites.filter((p) => !p.ok && p.optional !== true).map((p) => p.id)
  const missingOptional = prerequisites.filter((p) => !p.ok && p.optional === true).map((p) => p.id)
  const missing = prerequisites.filter((p) => !p.ok).map((p) => p.id)
  const providerImplemented = options.providerImplemented === true
  const enabled = feature.value !== 'off'
  const preview = platform === 'linux' && feature.value === 'on'
  const hardware_gl = missingRequired.length === 0 && missingOptional.length === 0
  const ready = enabled && providerImplemented && missingRequired.length === 0
  const reason = !enabled
    ? feature.source.startsWith('default')
      ? 'feature_disabled'
      : 'feature_disabled:' + feature.source
    : !providerImplemented
      ? 'provider_unavailable'
      : missingRequired.length
        ? 'prerequisites_missing'
        : null
  const requiredRows = prerequisites.filter((p) => !p.ok && p.optional !== true)
  const optionalRows = prerequisites.filter((p) => !p.ok && p.optional === true)
  const fix = !enabled
    ? 'set features.virtualGui=on in dsh-blender.config.json (or DSH_BLENDER_VIRTUAL_GUI=on) after the platform prerequisites are installed'
    : !providerImplemented
      ? 'virtual-gui provider is not implemented in this build; use user-gui or blender_run headless'
      : requiredRows.length
        ? 'install the missing prerequisites: ' +
          requiredRows.map((p) => p.label + ' (' + p.install + ')').join('; ') +
          (optionalRows.length
            ? '; hardware GL additionally needs ' +
              optionalRows.map((p) => p.label + ' (' + p.install + ')').join('; ')
            : '')
        : optionalRows.length
          ? 'preview-only: hardware GL unavailable; install ' +
            optionalRows.map((p) => p.label + ' (' + p.install + ')').join('; ')
          : ''
  return {
    platform,
    provider:
      platform === 'win32'
        ? 'win-hidden-desktop'
        : platform === 'linux'
          ? 'linux-xvfb-virtualgl'
          : null,
    provider_implemented: providerImplemented,
    feature: feature.value,
    feature_source: feature.source,
    enabled,
    preview,
    hardware_gl,
    ready,
    needs_approval: enabled,
    prerequisites,
    missing,
    reason,
    fix,
  }
}

export interface DisplayCapabilitiesQuery {
  platform?: string
  env?: NodeJS.ProcessEnv
}

export function displayCapabilities(options: DisplayCapabilitiesQuery = {}): DisplayCapabilities {
  const platform = options.platform || process.platform
  return buildDisplayCapabilities({
    platform,
    configured: CFG.features.virtualGui,
    source: CFG.features.source,
    env: options.env,
    // The Windows W1 and Linux L1 implementations ship in the bundle;
    // prerequisites decide whether this machine can actually use them.
    providerImplemented: platform === 'win32' || platform === 'linux',
  })
}
