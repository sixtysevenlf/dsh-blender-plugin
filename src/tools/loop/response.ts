/** loop 响应文本：session 通道与 GUI 通道两套口径。 */
export function loopSessionText(op: string, nm: string, result: unknown): string {
  return 'LOOP ' + op + ' (session=' + nm + ') ok · ' + JSON.stringify(result).slice(0, 6000)
}

export function loopResultText(op: string, result: unknown): string {
  return 'LOOP ' + op + ' ' + JSON.stringify(result)
}

export function loopSessionError(_op: string, _nm: string, detail: string): string {
  // v2.6.2 行为冻结：旧实现失败文案只有 'LOOP session 失败 · '，不带 op/session。
  return 'LOOP session 失败 · ' + detail
}
