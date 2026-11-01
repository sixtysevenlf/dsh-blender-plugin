/** host/backend/port-holders：Windows 端口监听者查询（纯函数，经 runtime-bridge）。 */
import { winSamePortHolders as winSamePortHoldersRaw } from '../runtime-bridge.js'

export function winSamePortHolders(port: number): number[] {
  return winSamePortHoldersRaw(port)
}
