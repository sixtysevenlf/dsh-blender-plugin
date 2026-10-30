/**
 * v0.9.4（P0 返回通道）：**工具返回值统一消毒**，保证过得了宿主的 lossless-JSON 门。
 *
 * 背景（外部反馈 2026-09-25《M1A1 分件建模》）：宿主用 dsh-util-values 的 walkJsonValue 校验
 * 每个工具的返回值，规则**比 JSON.stringify 严格得多**，以下一律判
 * `tool "…" returned invalid output: value is not lossless JSON` 并把整条工具通道打死：
 *   - 自有可枚举键的值为 undefined（stringify 会丢键 → 肉眼看不出来）
 *   - NaN / ±Infinity（stringify 变 null）· -0（stringify 变 0，符号丢失）
 *   - Date / Map / Set / 类实例 / 函数 / Symbol / 循环引用 / 带符号或非枚举键的对象
 * 实测后果：v0.9.3 的 envelope 里一个 `promoted: undefined` 让
 * blender_rt_headless **每次都失败**（任务其实跑完了、结果也在磁盘上，只是回执被拒），
 * rt_job 的 status/collect/wait 同病 —— 即"任务能交出去但收不回来"。
 *
 * 消毒口径（**不静默**：每一处改动都记进 fixes，回执文本里点名）：
 *   undefined → 丢键（数组元素 → null，长度不塌）· 非有限数 → null · -0 → 0
 *   bigint → number（超安全整数则 string）· Date → ISO 字符串 · Map/Set → 数组
 *   类实例 → 取自有可枚举属性 · 二进制视图 → {bytes:N} · 循环 → '[Circular]'
 */
export function losslessSanitize(root: any): { value: any; fixes: string[] } {
  const fixes: string[] = []
  const seen = new Set<any>()
  const walk = (v: any, at: string): any => {
    if (v === null) return null
    const t = typeof v
    if (t === 'string' || t === 'boolean') return v
    if (t === 'number') {
      if (!Number.isFinite(v)) {
        fixes.push(at + '=' + String(v) + '→null')
        return null
      }
      if (Object.is(v, -0)) {
        fixes.push(at + '=-0→0')
        return 0
      }
      return v
    }
    if (t === 'undefined') {
      fixes.push(at + '=undefined→丢弃')
      return undefined
    }
    if (t === 'bigint') {
      const n = Number(v)
      fixes.push(at + '=bigint→' + (Number.isSafeInteger(n) ? 'number' : 'string'))
      return Number.isSafeInteger(n) ? n : String(v)
    }
    if (t === 'function' || t === 'symbol') {
      fixes.push(at + '=' + t + '→丢弃')
      return undefined
    }
    if (seen.has(v)) {
      fixes.push(at + '=循环引用→[Circular]')
      return '[Circular]'
    }
    seen.add(v)
    let out: any
    if (Array.isArray(v)) {
      out = []
      for (let i = 0; i < v.length; i++) {
        const w = walk(v[i], at + '[' + String(i) + ']')
        out.push(w === undefined ? null : w) // 数组元素不能用 undefined
      }
    } else if (v instanceof Date) {
      const ms = v.getTime()
      fixes.push(at + '=Date→ISO')
      out = Number.isFinite(ms) ? v.toISOString() : null
    } else if (v instanceof Map) {
      fixes.push(at + '=Map→数组')
      out = Array.from(v.entries()).map((e: any, i: number) => walk(e, at + '<map' + String(i) + '>'))
    } else if (v instanceof Set) {
      fixes.push(at + '=Set→数组')
      out = Array.from(v.values()).map((e: any, i: number) => walk(e, at + '<set' + String(i) + '>'))
    } else if (ArrayBuffer.isView(v) || v instanceof ArrayBuffer) {
      const n = (v as any).byteLength
      fixes.push(at + '=二进制(' + String(n) + 'B)→{bytes}（要回传给模型请走 attachments/saveImage）')
      out = { bytes: n, why: 'binary-not-json' }
    } else if (typeof v.toJSON === 'function' && !isPlainObjectLike(v)) {
      // 宿主没定义 toJSON 语义，但它至少比类实例更可能给出 JSON 形状；失败就退回自有键
      try {
        return walk(v.toJSON(), at + '<toJSON>')
      } catch (e: any) {
        /* 退回自有可枚举键 */
      }
      out = {}
      for (const k of Object.keys(v)) {
        const w = walk(v[k], at + '.' + k)
        if (w !== undefined) out[k] = w
      }
    } else {
      out = {}
      for (const k of Object.keys(v)) {
        const w = walk(v[k], at + '.' + k)
        if (w !== undefined) out[k] = w // 丢键（宿主拒收 undefined 值）
      }
    }
    seen.delete(v) // DAG 共享（非循环）可以重复出现
    return out
  }
  const value = walk(root, 'value')
  return { value: value === undefined ? null : value, fixes: fixes }
}
/** 普通对象/数组判定（用于决定是否走 toJSON 分支） */
export function isPlainObjectLike(v: any): boolean {
  if (Array.isArray(v)) return true
  const proto = Object.getPrototypeOf(v)
  return proto === Object.prototype || proto === null
}
