// Guided prerequisite installation for virtual-gui providers.
//
// Detection and planning are side-effect free and shared by the host doctor
// (doctor --fix) and the add-on preferences button: both read the same
// installPlans payload from GET /doctor. Only runInstallPlan() spawns a
// process, and only for a supported one-click path (currently apt-get xvfb on
// Ubuntu 22.04/24.04 and Debian 12 with pkexec). Everything else returns the
// exact command for the user to run; nothing is installed silently.
import { spawn } from 'node:child_process'
import type { ChildProcess } from 'node:child_process'
import fs from 'node:fs'
import { onPath } from './prerequisites.js'
import type { DisplayCapabilities, DisplayPrerequisiteStatus } from './types.js'

export interface InstallStep {
  description: string
  command: string
}

export interface InstallPlan {
  id: string
  label: string
  /** Current detection result: true means this prerequisite is already present. */
  ok: boolean
  needsRoot: boolean
  /** True only when oneClick is a complete, testable install command. */
  supported: boolean
  /** argv passed to spawn; null when the user must run command manually. */
  oneClick: string[] | null
  /** Displayed command; the elevated argv is this command split on spaces. */
  command: string | null
  steps: InstallStep[]
  reason?: string
}

export interface Distro {
  id: string
  version: string
}

export interface InstallPlanInput {
  platform?: string
  env?: NodeJS.ProcessEnv
  distro?: Distro
  readFile?: (file: string) => string
  hasPkexec?: boolean
}

export interface InstallRunResult {
  ok: boolean
  code: number | null
  stdout: string
  stderr: string
  error?: string
}

const OUTPUT_LIMIT = 8192
const INSTALL_TIMEOUT_MS = 10 * 60 * 1000
const UBUNTU_ONE_CLICK = new Set(['22.04', '24.04'])
const DEBIAN_ONE_CLICK = new Set(['12'])

function readOsRelease(readFile: (file: string) => string): Distro {
  let raw: string
  try {
    raw = readFile('/etc/os-release')
  } catch {
    return { id: '', version: '' }
  }
  const values: Record<string, string> = {}
  for (const line of raw.split(/\r?\n/)) {
    const match = /^([A-Z_]+)=(.*)$/.exec(line)
    if (!match) continue
    values[match[1] as string] = match[2]!.replace(/^"|"$/g, '')
  }
  return { id: (values.ID || '').toLowerCase(), version: values.VERSION_ID || '' }
}

export function detectDistro(input: InstallPlanInput = {}): Distro {
  if (input.distro) return input.distro
  if ((input.platform || process.platform) !== 'linux') return { id: '', version: '' }
  return readOsRelease(input.readFile || ((file) => fs.readFileSync(file, 'utf8')))
}

function debianFamily(distro: Distro): boolean {
  return distro.id === 'ubuntu' || distro.id === 'debian'
}

function oneClickAllowed(distro: Distro): boolean {
  if (distro.id === 'ubuntu') return UBUNTU_ONE_CLICK.has(distro.version)
  if (distro.id === 'debian') return DEBIAN_ONE_CLICK.has(distro.version)
  return false
}

function manualCommand(distro: Distro): string {
  if (debianFamily(distro)) return 'sudo apt-get install -y xvfb'
  if (distro.id === 'fedora') return 'sudo dnf install -y xorg-x11-server-Xvfb'
  if (distro.id === 'arch') return 'sudo pacman -S --needed xorg-server-xvfb'
  return 'install Xvfb with your distribution package manager'
}

function xvfbPlan(
  status: DisplayPrerequisiteStatus,
  distro: Distro,
  hasPkexec: boolean,
): InstallPlan {
  const base = { id: status.id, label: status.label, ok: status.ok, needsRoot: true }
  if (debianFamily(distro) && oneClickAllowed(distro) && hasPkexec) {
    const argv = ['pkexec', 'apt-get', 'install', '-y', 'xvfb']
    return {
      ...base,
      supported: true,
      oneClick: argv,
      command: argv.join(' '),
      steps: [
        {
          description: 'Authenticate with polkit and install Xvfb from the distribution archive',
          command: argv.join(' '),
        },
      ],
    }
  }
  const reason =
    debianFamily(distro) && !oneClickAllowed(distro)
      ? 'this distribution release is not a supported one-click target'
      : debianFamily(distro)
        ? 'polkit/pkexec is not available; run the command with sudo'
        : 'no supported one-click path for this distribution; run the command manually'
  return {
    ...base,
    supported: false,
    oneClick: null,
    command: manualCommand(distro),
    steps: [
      {
        description:
          'Install Xvfb with the distribution package manager, then re-check capabilities',
        command: manualCommand(distro),
      },
    ],
    reason,
  }
}

function virtualglPlan(status: DisplayPrerequisiteStatus): InstallPlan {
  const steps: InstallStep[] = [
    {
      description:
        'Add the official VirtualGL APT repository (packages.ubuntu.com and packages.debian.org have no virtualgl package)',
      command: 'add the official VirtualGL APT repository: https://github.com/VirtualGL/repo',
    },
    { description: 'Refresh package lists', command: 'sudo apt-get update' },
    {
      description: 'Install VirtualGL from that repository',
      command: 'sudo apt-get install -y virtualgl',
    },
    {
      description: 'Verify the EGL transport on the real GPU (llvmpipe is preview-only)',
      command: 'vglrun -d egl glxinfo -B',
    },
  ]
  return {
    id: status.id,
    label: status.label,
    ok: status.ok,
    needsRoot: true,
    supported: false,
    oneClick: null,
    command: steps.map((step) => step.command).join('; '),
    steps,
    reason:
      'VirtualGL is not in the Ubuntu 22.04/24.04 or Debian 12 archives; the official repository step must run first, so this is never a one-click install',
  }
}

function gpuPlan(status: DisplayPrerequisiteStatus): InstallPlan {
  const command = 'sudo usermod -aG video,render $USER  # then log out and back in'
  return {
    id: status.id,
    label: status.label,
    ok: status.ok,
    needsRoot: true,
    supported: false,
    oneClick: null,
    command,
    steps: [
      {
        description: 'Grant the Gateway user read access to /dev/dri, then re-open the session',
        command,
      },
    ],
    reason: 'GPU driver installation is never automated; only group membership is suggested',
  }
}

function manualPlan(status: DisplayPrerequisiteStatus, reason: string): InstallPlan {
  return {
    id: status.id,
    label: status.label,
    ok: status.ok,
    needsRoot: status.needsRoot,
    supported: false,
    oneClick: null,
    command: status.install,
    steps: [{ description: status.install, command: status.install }],
    reason,
  }
}

function planFor(
  status: DisplayPrerequisiteStatus,
  input: InstallPlanInput,
  distro: Distro,
  hasPkexec: boolean,
): InstallPlan {
  const platform = input.platform || process.platform
  if (status.id === 'xvfb') return xvfbPlan(status, distro, hasPkexec)
  if (status.id === 'virtualgl') return virtualglPlan(status)
  if (status.id === 'gpu-device') return gpuPlan(status)
  if (status.id === 'win-hidden-desktop-helper')
    return manualPlan(
      status,
      'the helper ships with the 3.1 package; there is no separate installer',
    )
  if (status.id === 'blender')
    return manualPlan(
      status,
      platform === 'linux'
        ? 'install Blender 4.2.10 LTS or point DSH_BLENDER_EXE at it'
        : 'install Blender 4.2.10 LTS or set DSH_BLENDER_EXE',
    )
  if (status.id === 'mac-provider')
    return manualPlan(
      status,
      'virtual-gui has no 3.1 provider on macOS; use user-gui or blender_run',
    )
  return manualPlan(status, 'no automated installer for this prerequisite')
}

/** Build one plan per prerequisite row; callers filter plan.ok === false. */
export function buildInstallPlans(
  capabilities: DisplayCapabilities,
  input: InstallPlanInput = {},
): InstallPlan[] {
  const distro = detectDistro(input)
  const env = input.env || process.env
  const hasPkexec = input.hasPkexec ?? onPath('pkexec', env) !== null
  return capabilities.prerequisites.map((status) => planFor(status, input, distro, hasPkexec))
}

function tail(text: string): string {
  return text.length > OUTPUT_LIMIT ? text.slice(-OUTPUT_LIMIT) : text
}

/** Run a supported one-click plan and return the raw result; never retries. */
export function runInstallPlan(
  plan: InstallPlan,
  opts: { spawnImpl?: typeof spawn; timeoutMs?: number } = {},
): Promise<InstallRunResult> {
  return new Promise((resolve) => {
    if (!plan.oneClick || plan.oneClick.length === 0) {
      resolve({
        ok: false,
        code: null,
        stdout: '',
        stderr: '',
        error: 'no one-click install command for ' + plan.id,
      })
      return
    }
    const [command, ...args] = plan.oneClick
    let settled = false
    let child: ChildProcess
    try {
      child = (opts.spawnImpl || spawn)(command as string, args, {
        stdio: ['ignore', 'pipe', 'pipe'],
        env: process.env,
      })
    } catch (e) {
      resolve({
        ok: false,
        code: null,
        stdout: '',
        stderr: '',
        error: (e as Error).message,
      })
      return
    }
    let stdout = ''
    let stderr = ''
    const finish = (result: InstallRunResult) => {
      if (settled) return
      settled = true
      clearTimeout(timer)
      resolve(result)
    }
    const timer = setTimeout(() => {
      try {
        child.kill('SIGKILL')
      } catch {
        /* already gone */
      }
      finish({
        ok: false,
        code: null,
        stdout: tail(stdout),
        stderr: tail(stderr),
        error: 'install timed out',
      })
    }, opts.timeoutMs || INSTALL_TIMEOUT_MS)
    child.stdout?.on('data', (chunk) => {
      stdout = tail(stdout + chunk.toString())
    })
    child.stderr?.on('data', (chunk) => {
      stderr = tail(stderr + chunk.toString())
    })
    child.on('error', (e) => {
      finish({
        ok: false,
        code: null,
        stdout: tail(stdout),
        stderr: tail(stderr),
        error: e.message,
      })
    })
    child.on('exit', (code) => {
      finish({ ok: code === 0, code, stdout: tail(stdout), stderr: tail(stderr) })
    })
  })
}
