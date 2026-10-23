/**
 * F-09 · file= 参数守卫（engine 与工具端共用）——
 * 「file= 缺失 / 空串 / 参数名拼错」不再被静默包装成一次成功执行
 * （旧症状：ok=true / exitCode=0 / inputFile=null / 零警告 / 脚本拿到出厂空场景）。
 *
 * 纯函数模块，三个消费面共用同一份判定：
 *  - runtime/engine.mjs（直连 /headless 的兜底守卫 + 回执语义）
 *  - src/index.ts（工具端 ALIAS / 空串拦截 —— 工具端在 body 组装前就把 falsy 剥成
 *    undefined，引擎侧守卫对工具路径**永不触发**，所以 ALIAS 必须落在这一侧）
 *  - tests/headless_file_guard_selftest.mjs（离线单测，不需要 Blender / 后端）
 */

/** file= 的常见误用参数名。工具端见到这些键存在（≠undefined）就必须明确报错，
 *  而不是静默当成"没传"——静默丢参数正是 F-09 签名之一（blend=<好路径> → 空场景 ok=true）。 */
export const FILE_ALIAS_KEYS = ['blend', 'blendFile', 'path', 'filepath', 'blend_file', 'input'];

/** 返回 args 里出现过的 file= 别名键（用于报错文案点出"正名是 file"）。 */
export function detectFileAlias(args) {
  if (!args || typeof args !== 'object') return [];
  return FILE_ALIAS_KEYS.filter((k) => args[k] !== undefined && args[k] !== null);
}

/**
 * 引擎侧守卫：返回规范化的 file=（非空字符串）或 undefined（= 未传）。
 *  - '' / 纯空白 → throw code='file-empty'：显式拒绝"传了但为空"，不再静默当没传
 *  - null / undefined → undefined（未传语义；回执侧由 describeMissingInput + no_input_file 警告补话）
 *  - 单元素字符串数组 → 取唯一元素（保留既有 String([p])===p 的便利行为，不借修复之名改语义）
 *  - 其余非字符串 → throw code='file-type'（对象/数组旧行为是拼成 "[object Object]" 走 ENOENT，
 *    现在提前一步给出可读错误，错误路径不变式：ok=false）
 */
export function validateFileOpt(raw) {
  if (raw === undefined || raw === null) return undefined;
  let v = raw;
  if (Array.isArray(v) && v.length === 1 && typeof v[0] === 'string') v = v[0];
  if (typeof v !== 'string') {
    const e = new Error('file= 必须是字符串路径，收到 ' + (Array.isArray(v) ? 'array' : typeof v));
    e.code = 'file-type';
    e.hint = '例：file="D:\\x\\y.blend"；要跑 .py 脚本请用 script_file=（file= 是 .blend）';
    throw e;
  }
  if (!v.trim()) {
    const e = new Error('file= 传了空字符串 —— 本次不会打开任何 .blend（脚本会拿到出厂空场景）');
    e.code = 'file-empty';
    e.hint = '要么给真实 .blend 路径，要么就不要传 file=；若本意是 .blend 却传成 .py，'
           + '会被当成脚本内联执行而不会开档（见 fileLooksPython）。';
    throw e;
  }
  return v;
}

/**
 * 「没开档」的回执语义：不再用一个裸 null —— given/opened/reason 让
 * 「没给档」与「给了 .py」在回执里可区分。
 * 形态冻结（issue 兼容性条款）：正常路径的 fileFingerprint 返回值**一字不动**；
 * 只把原 null 分支换成带语义的对象。仓内 truthiness 消费点（src/index.ts 的
 * `if (r.inputFile && r.inputFile.md5)`）由 .md5 存在性把关，不受影响。
 */
export function describeMissingInput(fileOpt, fileLooksPython) {
  if (fileLooksPython) {
    return { given: true, opened: false, reason: 'file= 是 .py —— 已按脚本内联执行，未打开任何 .blend' };
  }
  if (fileOpt !== undefined && fileOpt !== null && !String(fileOpt).trim()) {
    return { given: true, opened: false, reason: 'file= 为空 —— 未打开任何 .blend' };
  }
  return { given: false, opened: false, reason: '本次未传 file=，Blender 以出厂空场景启动' };
}

/** 「成功但没开档」不许再无声：升级为回执 warnings 里的显式条目（不必翻 scriptFile 中继数据才知道）。 */
export function noInputFileWarning(fileLooksPython) {
  return {
    code: 'no_input_file',
    hint: '本次执行没有打开任何 .blend（'
        + (fileLooksPython ? 'file= 是 .py，已当脚本执行' : '未传 file=')
        + '）。若你以为打开了文件，请检查参数名是否为 file（不是 blend/path）。',
  };
}

/**
 * stdout/stderr log 文件名去同毫秒撞名（与 staging 脚本撞名同根，issue A2）：
 * 旧档名只精确到毫秒，同毫秒两个 run 的 createWriteStream 追加写同一文件、stdout 互脏。
 * runId 每 run 唯一（客户端生成或引擎生成），拼进档名后同毫秒也不再相撞。
 */
export function headlessLogNames(stamp, runId) {
  const tag = runId ? ('_' + String(runId).replace(/[^A-Za-z0-9_-]/g, '_')) : '';
  return {
    stdout: 'dsh_headless_' + stamp + tag + '.stdout.log',
    stderr: 'dsh_headless_' + stamp + tag + '.stderr.log',
  };
}
