// engine API：Python 执行/命令透传
import { ACT_WRAPPER } from '../../paths.js'
import { KERNEL_BOOTSTRAP } from '../kernel/index.js'
import { enrichError } from '../errors.js'
import { planOpCrossChannelHint } from '../../catalog/index.js'
import type { EngineApiDeps } from './index.js'

export function createActApi(E: EngineApiDeps) {
  const { addon } = E
  return {
    async act(code: unknown, timeoutMs = 120000, file: unknown = null) {
      const src = file
        ? 'K.run(' + JSON.stringify(String(file)) + ')'
        : code === undefined || code === null
          ? ''
          : String(code)
      const wrapped = KERNEL_BOOTSTRAP + '\n' + ACT_WRAPPER(src)
      const t0 = Date.now()
      const res = await addon.send('execute_code', { code: wrapped }, timeoutMs)
      const resRec =
        res !== null && typeof res === 'object' ? (res as Record<string, unknown>) : null
      const raw = resRec && typeof resRec.result === 'string' ? resRec.result : ''
      const ms = Date.now() - t0
      const pick = (tag: string): string | null => {
        const ls = raw.split('\n').filter((x: string) => x.indexOf(tag + ' ') === 0)
        return ls.length ? ls[ls.length - 1] : null
      }
      const errLine = pick('DSH_ACT_ERR')
      const okLine = pick('DSH_ACT_OK')
      let p: Record<string, unknown> | null
      try {
        p = JSON.parse(
          (errLine || okLine || '').slice((errLine ? 'DSH_ACT_ERR ' : 'DSH_ACT_OK ').length).trim(),
        ) as Record<string, unknown>
      } catch (e) {
        p = null
      }
      if (p) {
        return {
          ok: !errLine,
          executed: true,
          ms: ms,
          mainThreadMs: ms,
          stdout: p.stdout || '',
          stderr: p.stderr || '',
          error: p.error ? enrichError(p.error, p.traceback) : null,
          traceback: p.traceback || null,
          sceneEpoch: p.epoch || null,
          file: file || null,
        }
      }
      return {
        ok: true,
        executed: true,
        ms: ms,
        mainThreadMs: ms,
        stdout: raw,
        stderr: '',
        error: null,
        traceback: null,
        marker_missing: true,
        file: file || null,
      }
    },
    /** 通用命令透传：任意 addon 命令名 + 参数（MCP 那层不暴露的也能调） */
    async cmd(name: unknown, params: Record<string, unknown> = {}, timeoutMs = 120000) {
      if (!name || typeof name !== 'string') throw new Error('cmd: name required')
      // v0.9.6：把 plan op 当 addon 命令发是实测出现过的弯路（qc_render_catalog），
      // 这里在**本地**就纠正，不再浪费一次 Blender 往返。
      const hint = planOpCrossChannelHint(name)
      if (hint) return { status: 'error', result: 'CMD_HINT ' + hint, hint_shown: true }
      return addon.send(name, params || {}, timeoutMs)
    },
    /** 命令清单：目录 + 当前可用性（受 scene 开关与其 API key 影响） */
  }
}
