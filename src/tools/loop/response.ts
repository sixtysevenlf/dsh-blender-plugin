/** loop 响应文本：session 通道与 GUI 通道两套口径。 */
export function loopSessionText(op: string, nm: string, result: unknown): string {
  return 'LOOP ' + op + ' (session=' + nm + ') ok · ' + JSON.stringify(result).slice(0, 6000)
}

export function loopResultText(op: string, result: unknown): string {
  return 'LOOP ' + op + ' ' + JSON.stringify(result)
}

export function loopSessionError(op: string, nm: string, detail: string): string {
  return 'LOOP ' + op + ' (session=' + nm + ') 失败 · ' + detail
}
