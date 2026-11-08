import { EventEmitter } from 'node:events'
import { PassThrough } from 'node:stream'
import { describe, expect, it } from 'vitest'
import {
  buildInstallPlans,
  detectDistro,
  runInstallPlan,
} from '../../../runtime/display/install.js'
import type {
  DisplayCapabilities,
  DisplayPrerequisiteStatus,
} from '../../../runtime/display/types.js'

function status(
  id: string,
  ok = false,
  extra: Partial<DisplayPrerequisiteStatus> = {},
): DisplayPrerequisiteStatus {
  return {
    id,
    label: id,
    check: 'check ' + id,
    install: 'manual install for ' + id,
    needsRoot: id !== 'blender',
    ok,
    detail: ok ? 'present' : 'missing',
    ...extra,
  }
}

function capabilities(
  prerequisites: DisplayPrerequisiteStatus[],
  platform = 'linux',
): DisplayCapabilities {
  return {
    platform,
    provider: 'linux-xvfb-virtualgl',
    provider_implemented: true,
    feature: 'on',
    feature_source: 'config',
    enabled: true,
    preview: true,
    hardware_gl: false,
    ready: false,
    needs_approval: false,
    prerequisites,
    missing: prerequisites.filter((p) => !p.ok).map((p) => p.id),
    reason: 'prerequisites_missing',
    fix: 'install the missing prerequisites',
  }
}

const ubuntu = { id: 'ubuntu', version: '24.04' }
const read = (raw: string) => () => raw

function plan(plans: ReturnType<typeof buildInstallPlans>, id: string) {
  const found = plans.find((p) => p.id === id)
  if (!found) throw new Error('missing plan ' + id)
  return found
}

describe('install plans', () => {
  it('parses quoted os-release values and lowercases the id', () => {
    const distro = detectDistro({
      platform: 'linux',
      readFile: read('ID="Ubuntu"\nVERSION_ID="24.04"\n'),
    })
    expect(distro).toEqual({ id: 'ubuntu', version: '24.04' })
  })

  it('offers one authenticated apt-get install for Ubuntu 22.04/24.04 with pkexec', () => {
    const plans = buildInstallPlans(capabilities([status('xvfb')]), {
      platform: 'linux',
      distro: ubuntu,
      hasPkexec: true,
    })
    const xvfb = plan(plans, 'xvfb')
    expect(xvfb.supported).toBe(true)
    expect(xvfb.oneClick).toEqual(['pkexec', 'apt-get', 'install', '-y', 'xvfb'])
    expect(xvfb.command).toBe('pkexec apt-get install -y xvfb')
    expect(xvfb.reason).toBeUndefined()
  })

  it('degrades to sudo when pkexec is absent', () => {
    const plans = buildInstallPlans(capabilities([status('xvfb')]), {
      platform: 'linux',
      distro: ubuntu,
      hasPkexec: false,
    })
    const xvfb = plan(plans, 'xvfb')
    expect(xvfb.supported).toBe(false)
    expect(xvfb.oneClick).toBeNull()
    expect(xvfb.command).toContain('sudo apt-get install -y xvfb')
    expect(xvfb.reason).toContain('pkexec')
  })

  it('does not offer one click on an unsupported Ubuntu release', () => {
    const plans = buildInstallPlans(capabilities([status('xvfb')]), {
      platform: 'linux',
      distro: { id: 'ubuntu', version: '20.04' },
      hasPkexec: true,
    })
    const xvfb = plan(plans, 'xvfb')
    expect(xvfb.supported).toBe(false)
    expect(xvfb.reason).toContain('not a supported one-click')
  })

  it('offers one click on Debian 12 and degrades elsewhere', () => {
    const debian = buildInstallPlans(capabilities([status('xvfb')]), {
      platform: 'linux',
      distro: { id: 'debian', version: '12' },
      hasPkexec: true,
    })
    expect(plan(debian, 'xvfb').supported).toBe(true)
    const thirteen = buildInstallPlans(capabilities([status('xvfb')]), {
      platform: 'linux',
      distro: { id: 'debian', version: '13' },
      hasPkexec: true,
    })
    expect(plan(thirteen, 'xvfb').supported).toBe(false)
  })

  it('gives exact package manager commands for Fedora and Arch', () => {
    const fedora = buildInstallPlans(capabilities([status('xvfb')]), {
      platform: 'linux',
      distro: { id: 'fedora', version: '41' },
      hasPkexec: true,
    })
    expect(plan(fedora, 'xvfb').command).toBe('sudo dnf install -y xorg-x11-server-Xvfb')
    expect(plan(fedora, 'xvfb').supported).toBe(false)
    const arch = buildInstallPlans(capabilities([status('xvfb')]), {
      platform: 'linux',
      distro: { id: 'arch', version: '' },
      hasPkexec: true,
    })
    expect(plan(arch, 'xvfb').command).toBe('sudo pacman -S --needed xorg-server-xvfb')
  })

  it('never offers a one-click VirtualGL install and puts the repository step first', () => {
    const plans = buildInstallPlans(
      capabilities([status('virtualgl', false, { optional: true })]),
      {
        platform: 'linux',
        distro: ubuntu,
        hasPkexec: true,
      },
    )
    const virtualgl = plan(plans, 'virtualgl')
    expect(virtualgl.supported).toBe(false)
    expect(virtualgl.oneClick).toBeNull()
    expect(virtualgl.steps[0]!.command).toContain('github.com/VirtualGL/repo')
    expect(virtualgl.steps.map((s) => s.command).join(' ')).toContain(
      'apt-get install -y virtualgl',
    )
    expect(virtualgl.reason).toContain('never a one-click install')
  })

  it('only suggests group membership for the GPU device', () => {
    const plans = buildInstallPlans(
      capabilities([status('gpu-device', false, { optional: true })]),
      {
        platform: 'linux',
        distro: ubuntu,
      },
    )
    expect(plan(plans, 'gpu-device').command).toContain('usermod -aG video,render')
    expect(plan(plans, 'gpu-device').supported).toBe(false)
  })

  it('keeps Windows helper and Blender as instructions without an installer', () => {
    const plans = buildInstallPlans(
      capabilities([status('win-hidden-desktop-helper'), status('blender', true)], 'win32'),
      { platform: 'win32' },
    )
    expect(plan(plans, 'win-hidden-desktop-helper').supported).toBe(false)
    expect(plan(plans, 'win-hidden-desktop-helper').reason).toContain('3.1 package')
    expect(plan(plans, 'blender').ok).toBe(true)
  })

  it('returns an error instead of spawning when no one-click command exists', async () => {
    const result = await runInstallPlan({
      id: 'virtualgl',
      label: 'VirtualGL',
      ok: false,
      needsRoot: true,
      supported: false,
      oneClick: null,
      command: 'manual',
      steps: [],
    })
    expect(result.ok).toBe(false)
    expect(result.error).toContain('no one-click')
  })

  it('runs the displayed argv and preserves stderr on failure', async () => {
    const calls: Array<{ command: string; args: readonly string[] }> = []
    const child = Object.assign(new EventEmitter(), {
      stdout: new PassThrough(),
      stderr: new PassThrough(),
      kill: () => true,
    })
    const spawnImpl = ((command: string, args: readonly string[]) => {
      calls.push({ command, args })
      process.nextTick(() => {
        child.stderr.write('E: Unable to locate package xvfb')
        child.stdout.write('Reading package lists')
        child.emit('exit', 1)
      })
      return child
    }) as unknown as typeof import('node:child_process').spawn
    const result = await runInstallPlan(
      {
        id: 'xvfb',
        label: 'Xvfb',
        ok: false,
        needsRoot: true,
        supported: true,
        oneClick: ['pkexec', 'apt-get', 'install', '-y', 'xvfb'],
        command: 'pkexec apt-get install -y xvfb',
        steps: [],
      },
      { spawnImpl },
    )
    expect(calls).toEqual([{ command: 'pkexec', args: ['apt-get', 'install', '-y', 'xvfb'] }])
    expect(result.ok).toBe(false)
    expect(result.code).toBe(1)
    expect(result.stderr).toContain('Unable to locate package xvfb')
    expect(result.stdout).toContain('Reading package lists')
  })
})
