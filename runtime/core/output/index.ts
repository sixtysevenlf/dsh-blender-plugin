// 输出/目录入口（拆分）：catalog 页 / 标记解析 / 结果落盘
import fs from 'node:fs'
import path from 'node:path'
import { wslToWin } from '../../config.js'
import { errorMessage } from '../json.js'

export { extractTag, extractLoop, jsonFromStdout, clipMiddle, slipFixPath } from './parse.js'
export { catalogPayload } from './render.js'

export interface DumpResult {
  path: string | null
  bytes: number
  error?: string
}

export function createDumpResult(E: { RESULTS_DIR: string }): {
  dumpResult: (obj: unknown, runId2?: string) => DumpResult
} {
  const { RESULTS_DIR } = E
  function dumpResult(obj: unknown, runId2?: string): DumpResult {
    try {
      const s = JSON.stringify(obj)
      fs.mkdirSync(RESULTS_DIR, { recursive: true })
      const f = path.join(RESULTS_DIR, String(runId2 || 'r-' + Date.now().toString(36)) + '.json')
      fs.writeFileSync(f, s, 'utf8')
      return { path: wslToWin(f), bytes: s.length }
    } catch (e) {
      return { path: null, bytes: 0, error: errorMessage(e).slice(0, 200) }
    }
  }
  return { dumpResult }
}
