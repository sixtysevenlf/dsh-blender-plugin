// Host-side runner for the /doctor install plans. The plan itself comes from
// the Gateway's capabilities table, so this stays a thin executor: it only runs
// the argv the user was shown, never composes a package-manager command on its
// own. Unsupported plans are displayed by the caller and are never executed.
import { spawn } from 'node:child_process'

export interface HostInstallPlan {
  id: string
  label: string
  ok: boolean
  needsRoot: boolean
  supported: boolean
  oneClick: string[] | null
  command: string | null
  reason?: string
}

export interface HostInstallResult {
  ok: boolean
  detail: string
}

const OUTPUT_LIMIT = 4096
const TIMEOUT_MS = 10 * 60 * 1000

export function runHostInstall(plan: HostInstallPlan): Promise<HostInstallResult> {
  return new Promise((resolve) => {
    const argv = plan.oneClick
    if (!argv || argv.length === 0) {
      resolve({ ok: false, detail: '没有可执行的一键安装命令' })
      return
    }
    const [command, ...args] = argv
    let settled = false
    let stderr = ''
    let stdout = ''
    let timer: NodeJS.Timeout | null = null
    const finish = (result: HostInstallResult) => {
      if (settled) return
      settled = true
      if (timer) clearTimeout(timer)
      resolve(result)
    }
    let child
    try {
      child = spawn(command as string, args, { stdio: ['ignore', 'pipe', 'pipe'] })
    } catch (e) {
      resolve({ ok: false, detail: (e as Error).message })
      return
    }
    const append = (text: string, chunk: Buffer | string) => {
      const next = text + chunk.toString()
      return next.length > OUTPUT_LIMIT ? next.slice(-OUTPUT_LIMIT) : next
    }
    child.stdout?.on('data', (chunk) => {
      stdout = append(stdout, chunk)
    })
    child.stderr?.on('data', (chunk) => {
      stderr = append(stderr, chunk)
    })
    child.on('error', (e) => finish({ ok: false, detail: e.message }))
    child.on('exit', (code) => {
      if (code === 0) {
        finish({ ok: true, detail: '安装命令已成功退出' })
        return
      }
      const tail = (stderr || stdout).trim().split(/\r?\n/).slice(-3).join(' ')
      finish({ ok: false, detail: '安装命令退出码 ' + String(code) + (tail ? '：' + tail : '') })
    })
    timer = setTimeout(() => {
      try {
        child.kill('SIGKILL')
      } catch {
        /* already gone */
      }
      finish({ ok: false, detail: '安装命令超时' })
    }, TIMEOUT_MS)
    if (typeof timer.unref === 'function') timer.unref()
  })
}
