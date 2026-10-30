// 同步无头进程（T6；plan 表未单列，为满足 <=600 行从 engine 拆出，见 review.md）
// @imports
import { clipMiddle, slipFixPath } from "../core/output.js";
import { IS_MAC, IS_WIN, winToWsl, wslToWin } from "../config.js";
import { BLENDER_EXE, GPU_PRELUDE, USER_CONFIG_WIN, USER_SCRIPTS_WIN, WIN_TMP, pyModuleFile } from "../paths.js";
import { describeMissingInput, headlessLogNames, noInputFileWarning, validateFileOpt } from "../file_guard.js";
import fs from "node:fs";
import { PATH_GUARD, envPrelude, fileFingerprint, scriptFileBlock, shotsBlock, workdirBlock } from "../core/scripts.js";
import path from "node:path";
import { PLUGIN_VERSION, pathAudit, sceneHygieneAudit } from "../core/evidence.js";
import { KERNEL_BOOTSTRAP } from "../core/kernel.js";
import { hardExitSyncTail } from "../hard_exit.js";
import { writeHeadlessScript } from "../staging.js";
import { baseChildEnv, withWslEnv, workdirStagingWarning } from "../proc_env.js";
import { makeStageScanner, startHeartbeat } from "../core/progress.js";
import type { StageRecord } from "../core/progress.js";
import type { StagingInfo } from "../staging.js";
import type { ChildProcess } from "node:child_process";
import { spawn } from "node:child_process";
import { enrichError } from "../core/errors.js";
import { errorMessage } from "../core/json.js";


interface HeadlessOpts { [k: string]: unknown }

interface SpawnResult {
  so: string;
  se: string;
  exitCode: number | null;
  signal: NodeJS.Signals | null;
  spawnErr: Error | null;
  timedOut: boolean;
}

interface HeadlessRunRec extends StageRecord {
  [k: string]: unknown;
  pathWarnings: unknown[];
  artifacts: string[];
  logs: { stdout: string; stderr: string; stdoutWsl: string; stderrWsl: string } | null;
  scriptStaging: StagingInfo | null;
  child: ChildProcess | null;
  pid: number | null | undefined;
}

export interface HeadlessDeps {
  HERE: string;
  LEDGER: string;
  SESSION_NAME: string;
  dumpResult: (obj: unknown, runId?: string) => { path: string | null; bytes: number; error?: string };
  jobStart: (opts?: Record<string, unknown>) => Record<string, unknown> & { id: string };
  ledgerAppend: (rec: Record<string, unknown>) => void;
  metrics: { headlessRuns: number; lastHeadlessMs?: number; [k: string]: unknown };
  preloadChunk: (name: string) => string;
  readScriptPath: (p: unknown) => { win: string; wsl: string };
  runs: Map<string, HeadlessRunRec>;
}

/** GPU 回执里的 engine 字段（Python 侧 JSON，形状动态） */
function gpuAfterEngine(gpu: Record<string, unknown> | null): unknown {
  if (!gpu) return null;
  const after = gpu.after;
  if (!after || typeof after !== 'object') return null;
  return (after as Record<string, unknown>).engine || null;
}

/** 动态边界解析：从 expect 观测里取指定层对象的字段（JSON 来自 Python 侧，形状由脚本决定） */
function objField(rec: Record<string, unknown> | null, group: string, field: string): unknown {
  if (!rec) return null;
  const g = rec[group];
  if (!g || typeof g !== 'object') return null;
  const v = (g as Record<string, unknown>)[field];
  return v === undefined ? null : v;
}
function deltaObjects(rec: Record<string, unknown> | null): unknown {
  return objField(rec, 'delta', 'objects') || 0;
}

export function createHeadlessRun(E: HeadlessDeps) {
  const { LEDGER, SESSION_NAME, dumpResult, jobStart, ledgerAppend, metrics, preloadChunk, readScriptPath, runs } = E;
  return {
    async headless(opts: HeadlessOpts = {}): Promise<Record<string, unknown>> {
      const t0 = Date.now();
      // ---- v0.9.3（D4）：outdir 接受 WSL 路径（/home/…、/mnt/d/…）与 Windows 路径。
      // 旧版把入参**原样**塞给 Windows 的 blender.exe → '/home/x' 被当相对盘根，静默写到 C:\home\x。
      // 现在：Node 侧用 outdirWsl 读写、Blender 侧用 outdirWin（/home/… → \\wsl.localhost\<distro>\home\…）。
      const outdirArg = opts.outdir ? slipFixPath(String(opts.outdir), null) : null;
      const outdirWsl = outdirArg ? winToWsl(outdirArg) : null;
      const outdirWin = outdirArg ? wslToWin(outdirArg) : WIN_TMP;
      // v0.9.1（93-B3）：scriptFile= 显式收 .py；file= 传 .py 时自动改当脚本（老语义 file= 仍是 .blend）
      let scriptFileInfo: { given: string; resolved: string; bytes: number; auto_from_file: boolean } | null = null;
      let script = opts.script ? String(opts.script) : '';
      const fileOpt = validateFileOpt(opts.file);   // F-09：空串/类型错显式报错（直连 /headless 的兜底；工具端另有 ALIAS 拦截）
      const fileLooksPython = !!(fileOpt && /\.py$/i.test(String(fileOpt)));
      if (opts.scriptFile || fileLooksPython) {
        const given = String(opts.scriptFile || fileOpt);
        try {
          const rp = readScriptPath(given);
          script = fs.readFileSync(rp.wsl, 'utf8');
          scriptFileInfo = { given: given, resolved: rp.win, bytes: script.length,
                             auto_from_file: !opts.scriptFile && fileLooksPython };
        } catch (e) {
          throw Object.assign(new Error('读不到脚本文件：' + given + ' —— ' + errorMessage(e).slice(0, 160)),
                              { hint: 'scriptFile 用 .py 路径（Windows D:\\… 或 WSL /home/… 都行）；file= 是 .blend' });
        }
      }
      // ---- v0.9.3（F6）：inputFile 指纹 —— 探针跑在 02:55 版、04:07 才发现文件已被重存（浪费 4 次探针）
      // （必须在 fileLooksPython 之后：否则 TDZ ReferenceError —— 实测踩过）
      const inputFileInfo = (fileOpt && !fileLooksPython)
        ? fileFingerprint(wslToWin(String(fileOpt)), winToWsl(String(fileOpt)))
        : describeMissingInput(fileOpt, fileLooksPython);   // F-09：null → {given,opened,reason}（形态冻结：truthy 分支一字未动）
      // preload: 把 runtime 里的 python 模块（view/perf/runner/contract/planner...）源码拼进脚本开头
      const mods: unknown[] = Array.isArray(opts.preload) ? opts.preload : (opts.preload ? String(opts.preload).split(',') : []);
      // ---- v0.9.3（F1）：shots=[…] 多视角渲染一体化 —— 自动带上 qc_render（渲染 harness 就在里面）
      const shotsSpec: Record<string, unknown> | null = Array.isArray(opts.shots) ? { views: opts.shots }
        : (opts.shots && typeof opts.shots === 'object' ? Object.assign({}, opts.shots as Record<string, unknown>) : null);
      const shotsWanted = !!(shotsSpec && Array.isArray(shotsSpec.views) && shotsSpec.views.length);
      if (shotsWanted && !mods.some((m) => String(m).trim().replace(/\.py$/, '') === 'qc_render')) mods.push('qc_render');
      let pre = '';
      for (const m of mods) {
        const name = String(m).trim().replace(/\.py$/, '');
        if (!name) continue;
        // T8：同作业层，取 runtime/py/<domain>/ 的真实文件（旧路径是 shim）。
        const f = pyModuleFile(name);
        if (!fs.existsSync(f)) throw new Error('preload 找不到模块：' + name + '（' + f + '）');
        pre += preloadChunk(name) + '\n';
      }
      // ---- 引擎前导（v0.8.0）：默认 eevee + 光追；可选 cycles / keep（向后兼容 gpu:"false"=keep）
      const engineMode = String(opts.engine === undefined || opts.engine === null ? 'eevee' : opts.engine).toLowerCase();
      const legacyGpu = String(opts.gpu === undefined || opts.gpu === null ? '' : opts.gpu).toLowerCase();
      const gpuOff = (engineMode === 'keep' || engineMode === 'off' || engineMode === 'none'
        || legacyGpu === 'false' || legacyGpu === 'off' || legacyGpu === '0');
      const gpuManual = (legacyGpu === 'true' || legacyGpu === 'required');
      let gpuPre = '';
      if (!gpuOff) {
        const modeLit = (engineMode === 'cycles' || engineMode === 'cycle') ? 'cycles' : 'eevee';
        gpuPre = '# ---- DSH engine prelude (' + modeLit + ') ----\n' + GPU_PRELUDE
          .replace(/__DSH_ENGINE__/g, "'" + modeLit + "'")
          .replace(/__DSH_GPU_MANUAL__/g, gpuManual ? 'True' : 'False') + '\n';
      }
      const headParts: string[] = [];
      // ---- v0.9.1（93-B4）：env 注入必须在 K 内核之前 —— PATH_HELPERS 里的 K.args/K.run_id 是从 os.environ 读的，
      // 而 WSL→Windows 的 env 跨界不可靠（实测 DSH_* 会被吃掉）→ 直接在脚本里 setdefault 一遍。
      // v0.9.3（D1）：**runId 先定**（客户端可以自带一个 id）—— 客户端等待窗口到点时要能立刻报出
      // "用这个 id 去 collect"，所以 id 必须在发请求前就存在，不能等服务端生成完再回传。
      const runId = /^[A-Za-z0-9_-]{1,64}$/.test(String(opts.runId || '')) ? String(opts.runId)
        : ('run-' + Date.now().toString(36) + Math.random().toString(36).slice(2, 5));
      const envPairs: Record<string, string | null> = {
        DSH_RUN_ID: runId, DSH_OUTDIR: outdirWin, DSH_SESSION: SESSION_NAME,
        DSH_PLUGIN_VERSION: PLUGIN_VERSION,
        DSH_ARGS: JSON.stringify(Array.isArray(opts.args) ? opts.args.map(String) : []),
      };
      const envOpt: Record<string, unknown> | null = (opts.env && typeof opts.env === 'object') ? opts.env as Record<string, unknown> : null;
      if (envOpt) for (const k of Object.keys(envOpt)) envPairs[String(k)] = String(envOpt[k]);
      if (scriptFileInfo) envPairs.DSH_SCRIPT_FILE = scriptFileInfo.resolved;   // 反馈 #4
      headParts.push(envPrelude(envPairs));
      if (opts.bootstrap !== false) headParts.push(KERNEL_BOOTSTRAP);
      headParts.push('_dsh_stage_emit("engine-prelude")');
      // D4.3 基线：记下进入脚本前的 render.filepath（--factory-startup 的默认值本身就是 '/tmp\\' 这类
      // POSIX 串）—— 只有**脚本把它改成了 POSIX 绝对路径**才值得报警，否则全是假阳性。
      headParts.push(['try:', '    _DSH_FP0 = bpy.context.scene.render.filepath', 'except Exception:', '    _DSH_FP0 = None'].join(String.fromCharCode(10)));
      if (gpuPre) headParts.push(gpuPre);
      // v0.8.10（D2）：workdir → 脚本内 chdir + sys.path 首位（Blender 是 Windows 进程，必须过 K.win_path）
      if (opts.workdir) headParts.push(workdirBlock(slipFixPath(String(opts.workdir), null)));
      if (scriptFileInfo) headParts.push(scriptFileBlock(scriptFileInfo.resolved));
      // v0.8.10（B2）：expect 前后哨兵 —— "build 返回空却不抛异常"必须被判失败
      const exp: Record<string, unknown> | null = (opts.expect && typeof opts.expect === 'object') ? opts.expect as Record<string, unknown> : null;
      const expectPre = exp ? ['# ---- DSH expect: before ----', 'import json as _dsh_ejson',
        '_DSH_EXP0 = {"objects": len(bpy.data.objects), "meshes": len(bpy.data.meshes), "materials": len(bpy.data.materials)}'].join(String.fromCharCode(10)) : '';
      const expectPost = exp ? ['# ---- DSH expect: after ----', 'try:',
        '    _DSH_EXP1 = {"objects": len(bpy.data.objects), "meshes": len(bpy.data.meshes), "materials": len(bpy.data.materials)}',
        '    print("DSH_EXPECT " + _dsh_ejson.dumps({"before": _DSH_EXP0, "after": _DSH_EXP1, "delta": {k: _DSH_EXP1[k] - _DSH_EXP0[k] for k in _DSH_EXP1}}))',
        'except Exception as _e:', '    print("DSH_EXPECT " + _dsh_ejson.dumps({"error": str(_e)[:160]}))'].join(String.fromCharCode(10)) : '';
      const NL = String.fromCharCode(10);
      // v0.9.3（F1）：shots 走 qc_render_views —— outdir 用 Windows 形态（Blender 侧），其余键由调用方定
      const shotsPre = shotsWanted
        ? shotsBlock(Object.assign({ engine: 'keep', lock: true, warmup: true, tag: 'shot' }, shotsSpec, { outdir: outdirWin })) + NL
        : '';
      let body: string = (script || pre || gpuPre || shotsPre)
        ? (headParts.length ? headParts.join(NL) + NL : '')
          + (expectPre ? expectPre + NL : '')
          + pre
          + '_dsh_stage_emit("preload-done")' + NL
          + script
          + NL + '_dsh_stage_emit("script-end")' + NL
          + shotsPre
          + PATH_GUARD + NL
          + (expectPost ? expectPost : '')
          + NL + '_dsh_stage_emit("done")' + NL
        : '';
      // F-14：done 之后冲刷 stdout/stderr 再 os._exit(0)（跳过 Blender 退出拆解；hardExit=false 回旧行为）。
      // 空 body 不追加（与 2745 的 `if (body)` 同守卫）：无脚本的运行不该凭空多出一个"只有退出行"的 --python。
      if (body) body += hardExitSyncTail(opts, NL);
      const args: string[] = ['-b'];
      if (fileOpt && !fileLooksPython) args.push(wslToWin(String(fileOpt)));
      if (opts.factoryStartup !== false) args.push('--factory-startup');
      let sp: ReturnType<typeof writeHeadlessScript> | null = null;
      if (body) { sp = writeHeadlessScript(body); args.push('--python', sp.win); }
      args.push('--');
      if (opts.outdir) args.push(outdirWin);
      if (Array.isArray(opts.args)) args.push(...opts.args.map(String));
      const timeoutMs = Math.max(1000, Math.min(1800000, Number(opts.timeoutMs) || 180000));
      metrics.headlessRuns++;
      // ---- v0.9.3（D1/D5）：日志**边跑边写** + 运行台账登记（id 与作业同空间：op=status/collect id=run-…）
      const logDirWsl = outdirWsl || winToWsl(WIN_TMP);
      const stamp = new Date(t0).toISOString().replace(/[:.]/g, '-');
      let logs: HeadlessRunRec["logs"] = null;
      let soStream: fs.WriteStream | null = null;
      let seStream: fs.WriteStream | null = null;
      try {
        fs.mkdirSync(logDirWsl, { recursive: true });
        const logNames = headlessLogNames(stamp, runId);   // F-09（A2）：同毫秒两个 run 不再互写同一个 log
        const soPath = path.join(logDirWsl, logNames.stdout);
        const sePath = path.join(logDirWsl, logNames.stderr);
        soStream = fs.createWriteStream(soPath); seStream = fs.createWriteStream(sePath);
        logs = { stdout: wslToWin(soPath), stderr: wslToWin(sePath), stdoutWsl: soPath, stderrWsl: sePath };
      } catch (e) { logs = null; }
      const runRec: HeadlessRunRec = { id: runId, child: null, pid: null, startedAt: t0, status: 'running',
                       outdir: outdirWin, outdirWsl: outdirWsl, logs: logs, script: sp ? sp.win : null,
                       scriptStaging: sp ? (sp.staging || null) : null,
                       exitCode: null, timedOut: false, artifacts: [], expect: exp, workdir: opts.workdir || null,
                       stage: null, stageAt: null, stageName: null, lastOutputAt: t0, lines: 0,
                       result: null, resultPath: null, resultBytes: 0, resultTruncated: false, pathWarnings: [] };
      if (runRec.scriptStaging && runRec.scriptStaging.substituted) {
        const w = workdirStagingWarning(runRec.scriptStaging);
        if (w) runRec.pathWarnings.push(w);
      }
      runs.set(runId, runRec);
      const stopRunHb = startHeartbeat(runRec, logDirWsl);
      if (runs.size > 20) { const k = runs.keys().next().value; if (k !== undefined && k !== runId) runs.delete(k); }
      ledgerAppend({ id: runId, kind: 'headless', status: 'running', startedAt: t0, script: runRec.script,
                     outdir: runRec.outdir, outdirWsl: outdirWsl, timeoutMs: timeoutMs, stage: null,
                     file: (fileOpt === undefined ? null : String(fileOpt)),   // F-09（A3）：台账记 file，事后可区分"没给档"与"给了没开"
                     stdoutLog: logs ? logs.stdout : null, stderrLog: logs ? logs.stderr : null });
      // v0.8.7（外部反馈 #4 P0-2）/ v0.9.3（D1）：长任务转作业层 —— 客户端超时不再把结果丢掉。
      // 用法：as_job=true 强制转；或给 auto_job_ms（如 120000）表示预计超过它就走作业。
      const autoJobMs = Number(opts.autoJobMs) || 0;
      if (opts.asJob || (autoJobMs > 0 && timeoutMs >= autoJobMs)) {
        runs.delete(runId);                       // 转作业后这次 run 不存在了，别留一个假 running
        const j = jobStart(Object.assign({}, opts, { timeoutMs: Math.max(timeoutMs, 3600000), runId: runId }));
        return { ok: true, mode: 'job', kind: 'job', ms: Date.now() - t0, jobId: j.id, runId: runId, job: j,
                 logs: { stdout: j.stdoutLog, stderr: j.stderrLog },
                 reason: null,
                 hint: '已转作业层（长任务）：用 blender_rt_job(op="status"/"collect"/"wait", id="' + j.id + '") 跟进' };
      }
      // ---- 子进程环境：可选透传用户 Blender 配置（GPU 偏好在里面）
      // v0.9.1（93-B4）：显式 env 入参 + 插件注入的契约变量 —— 参数不必再挤 argv（DSH_ARGS/DSH_OUTDIR/DSH_RUN_ID）
      // v0.9.3（D5）：三处 spawn 统一 baseChildEnv()（PYTHONIOENCODING + **PYTHONUNBUFFERED=1**）
      const childEnv = baseChildEnv({
        DSH_RUN_ID: runId,
        DSH_OUTDIR: outdirWin,
        DSH_ARGS: JSON.stringify(Array.isArray(opts.args) ? opts.args.map(String) : []),
        DSH_SESSION: SESSION_NAME,
        DSH_PLUGIN_VERSION: PLUGIN_VERSION,
      });
      if (envOpt) {
        for (const k of Object.keys(envOpt)) childEnv[String(k)] = String(envOpt[k]);
      }
      // ⚠ 实测坑（v0.9.1）：从 WSL 侧 spawn Windows 的 blender.exe 时，**env 不会自动跨界** ——
      // Node 把 DSH_* 传给了 spawn，但子进程 os.environ 里一个都看不到（要靠 WSLENV 声明）。
      // 这里自动声明；同时脚本里还会再注入一次（双保险，见 DSH_ENV_PRELUDE）。
      withWslEnv(childEnv, envOpt || undefined);
      if (opts.useUserConfig) {
        if (USER_CONFIG_WIN) childEnv.BLENDER_USER_CONFIG = USER_CONFIG_WIN;
        if (USER_SCRIPTS_WIN) childEnv.BLENDER_USER_SCRIPTS = USER_SCRIPTS_WIN;
      }
      const res = await new Promise<SpawnResult>((resolve) => {
        let so = '', se = '';
        let exitCode: number | null = null;
        let signal: NodeJS.Signals | null = null;
        let spawnErr: Error | null = null;
        let timedOut = false;
        const CAP = 262144;
        let child: ChildProcess | null = null;
        try {
          child = spawn(BLENDER_EXE as string, args, { env: childEnv, stdio: ['ignore', 'pipe', 'pipe'], windowsHide: true });
          runRec.child = child; runRec.pid = child.pid;
        } catch (e) {
          return resolve({ so: so, se: se, exitCode: null, signal: null,
                           spawnErr: e instanceof Error ? e : new Error(errorMessage(e)), timedOut: false });
        }
        // v0.9.3（D5）：stage 心跳扫描 + 增量落盘（run 运行期间 op=status.stage / 日志就有内容）
        const scanOut = makeStageScanner(runRec), scanErr = makeStageScanner(runRec);
        const push = (buf: Buffer, which: string): void => {
          const t = buf.toString('utf8');
          if (which === 'o') {
            so += t; if (so.length > CAP) so = so.slice(-CAP);
            scanOut(t);
            try { if (soStream) soStream.write(t); } catch (e) { /* ignore */ }
          } else {
            se += t; if (se.length > CAP) se = se.slice(-CAP);
            scanErr(t);
            try { if (seStream) seStream.write(t); } catch (e) { /* ignore */ }
          }
        };
        const proc = child;
        if (!proc) return resolve({ so: so, se: se, exitCode: null, signal: null, spawnErr: new Error('spawn 未返回进程'), timedOut: false });
        proc.stdout?.on('data', (d: Buffer) => push(d, 'o'));
        proc.stderr?.on('data', (d: Buffer) => push(d, 'e'));
        proc.on('error', (e) => { spawnErr = e; });
        const timer = setTimeout(() => { timedOut = true; try { proc.kill('SIGKILL'); } catch (e) { /* ignore */ } }, timeoutMs);
        proc.on('close', (code, sig) => { clearTimeout(timer); exitCode = code; signal = sig; resolve({ so: so, se: se, exitCode: exitCode, signal: signal, spawnErr: spawnErr, timedOut: timedOut }); });
      });
      try { if (soStream) soStream.end(); if (seStream) seStream.end(); } catch (e) { /* ignore */ }
      const ms = Date.now() - t0;
      metrics.lastHeadlessMs = ms;
      if (res.spawnErr) {
        const msg = '起不来 Blender 进程（' + BLENDER_EXE + '）：' + String((res.spawnErr && res.spawnErr.message) || res.spawnErr);
        const hint = '自动探测没找到 blender 可执行文件。三种配置方式（任选一种）：'
          + '① 环境变量 DSH_BLENDER_EXE=' + (IS_WIN ? 'D:\\...\\blender.exe' : (IS_MAC ? '/Applications/Blender.app/Contents/MacOS/Blender' : '/mnt/d/.../blender.exe'))
          + ' ② 包根写 dsh-blender.config.json: {"blenderExe":"..."}'
          + ' ③ ~/.dsh/dsh-blender.config.json。当前探测结果见 blender_viewport op=doctor 的 config 字段。';
        throw Object.assign(new Error(msg), { code: 'blender-exe-missing', hint: hint });
      }
      const stdout = String(res.so || '');
      const stderr = String(res.se || '');
      // ---- 结果契约：HEADLESS <单行 JSON>（取最后一条）
      // v0.9.3（D3）：末行不是合法 JSON 时**不再覆盖 raw**（旧版回 {_parse_error, raw} 顶掉真实输出，
      // 调用方只能看到 "_parse_error: Unexpected token 'O'"）。现在 parsed 保持 null，解析失败单独记在
      // resultParseError 里，stdout 原文（stdoutTail / logs）原样保留。
      let parsed: unknown = null;
      let parseError: { message: string; line: string } | null = null;
      const lines = stdout.split('\n').filter((l) => l.indexOf('HEADLESS ') === 0);
      if (lines.length) {
        const lastHead = lines[lines.length - 1].slice('HEADLESS '.length).trim();
        try { parsed = JSON.parse(lastHead); }
        catch (e) { parseError = { message: errorMessage(e).slice(0, 200), line: lastHead.slice(0, 400) }; }
      }
      // ---- v0.9.3（F1）：shots 回执（DSH_SHOTS <单行 JSON>）
      let shotsRes: Record<string, unknown> | null = null;
      const shotsLines = stdout.split('\n').filter((l) => l.indexOf('DSH_SHOTS ') === 0);
      if (shotsLines.length) {
        try { shotsRes = JSON.parse(shotsLines[shotsLines.length - 1].slice('DSH_SHOTS '.length).trim()); }
        catch (e) { shotsRes = { ok: false, parse_error: errorMessage(e).slice(0, 200) }; }
      }
      // ---- v0.9.3（D4.3）：路径静默改写体检
      const pathWarnings: unknown[] = pathAudit(stdout, stderr);
      // ---- v0.9.6（D3）：workDir 不共享 → 脚本改落别处，这件事必须出现在回执里（不许静默）
      if (sp && sp.staging && sp.staging.substituted
          && !pathWarnings.some((w) => typeof w === 'object' && w !== null && (w as { code?: unknown }).code === 'WORKDIR_NOT_SHARED')) {
        const w = workdirStagingWarning(sp.staging);
        if (w) pathWarnings.push(w);
      }
      // ---- GPU 回执（前导打印 DSH_GPU）
      let gpu: Record<string, unknown> | null = null;
      const gl = stdout.split('\n').filter((l) => l.indexOf('DSH_GPU ') === 0);
      if (gl.length) { try { gpu = JSON.parse(gl[gl.length - 1].slice('DSH_GPU '.length).trim()); } catch (e) { gpu = { _parse_error: errorMessage(e) }; } }
      // ---- 日志兜底：正常路径已在 push() 里边跑边写（D5）；流没建起来时这里补一次全量
      if (!soStream) {
        try {
          fs.mkdirSync(logDirWsl, { recursive: true });
          const logNames = headlessLogNames(stamp, runId);   // F-09（A2）：与主路径同一套档名规则（含 runId）
          const soPath = path.join(logDirWsl, logNames.stdout);
          const sePath = path.join(logDirWsl, logNames.stderr);
          fs.writeFileSync(soPath, stdout, 'utf8');
          fs.writeFileSync(sePath, stderr, 'utf8');
          logs = { stdout: wslToWin(soPath), stderr: wslToWin(sePath), stdoutWsl: soPath, stderrWsl: sePath };
        } catch (e) { logs = null; }
      }
      runRec.logs = logs;
      // ---- 失败结构化：traceback 段 + 最后一条异常行 + 转义坑提示
      let traceback: string | null = null;
      let lastException: string | null = null;
      let hint: string | null = null;
      const tbIdx = stderr.lastIndexOf('Traceback (most recent call last)');
      if (tbIdx >= 0) traceback = stderr.slice(tbIdx, tbIdx + 3000);
      const em = stderr.match(/^[\w.]*(?:Error|Exception|Warning)\b.*$/gm);
      if (em && em.length) lastException = enrichError(em[em.length - 1].slice(0, 300), traceback);
      if (lastException && /line continuation character|invalid syntax/i.test(lastException) && /\\n/.test(script)) {
        hint = '脚本里出现字面量 \\n（两个字符）而不是真正换行 —— 在 JSON/TS 里请写成 \\n，或用 String.fromCharCode(10) 拼。';
      }
      // ---- 产物清单：默认过滤噪音
      const isNoise = (n: string): boolean => n.indexOf('__pycache__') === 0 || /\.pyc$/.test(n) || /\.blend[12]$/.test(n) || n.indexOf('tmp') === 0 || n === '.DS_Store';
      let artifacts: Array<{ name: string; bytes: number; mtimeMs: number; fresh: boolean }> = [];
      let noiseFiltered = 0;
      if (outdirWsl) {
        try {
          const all: Array<{ name: string; bytes: number; mtimeMs: number; fresh: boolean; noise: boolean }> = fs.readdirSync(outdirWsl)
            .map((n) => { const p = path.join(outdirWsl, n); let st: fs.Stats | null = null; try { st = fs.statSync(p); } catch (e) { st = null; } return st && st.isFile() ? { name: n, bytes: st.size, mtimeMs: st.mtimeMs, fresh: st.mtimeMs >= t0 - 2000, noise: isNoise(n) } : null; })
            .filter((x): x is { name: string; bytes: number; mtimeMs: number; fresh: boolean; noise: boolean } => x !== null);
          noiseFiltered = all.filter((a) => a.noise).length;
          artifacts = (opts.includeNoise ? all : all.filter((a) => !a.noise))
            .sort((a, b) => b.mtimeMs - a.mtimeMs).slice(0, 40)
            .map((a) => ({ name: a.name, bytes: a.bytes, mtimeMs: a.mtimeMs, fresh: a.fresh }));
        } catch (e) { artifacts = []; }
      }
      const gpuFailed = gpuManual && gpu && gpu.ok === false;
      // ---- v0.8.10（B2）：expect 判定（"0 对象却 ok=true"这类静默空产出必须失败）
      let expectEval: { spec: Record<string, unknown>; observed: Record<string, unknown> | null; ok: boolean; warnings: string[] } | null = null;
      if (exp) {
        let observed: Record<string, unknown> | null = null;
        const el = stdout.split('\n').filter((l) => l.indexOf('DSH_EXPECT ') === 0);
        if (el.length) { try { observed = JSON.parse(el[el.length - 1].slice('DSH_EXPECT '.length).trim()) as Record<string, unknown>; } catch (e) { observed = null; } }
        expectEval = { spec: exp, observed: observed, ok: true, warnings: [] };
        const minNew = Number(exp.minNewObjects !== undefined ? exp.minNewObjects : exp.min_new_objects);
        if (!observed || observed.error) {
          expectEval.ok = false;
          expectEval.warnings.push('expect 观测失败：' + ((observed && observed.error) || '没有 DSH_EXPECT 行'));
        } else if (Number.isFinite(minNew) && Number(deltaObjects(observed)) < minNew) {
          expectEval.ok = false;
          expectEval.warnings.push('对象数未达预期：before=' + String(objField(observed, 'before', 'objects')) + ' → after=' + String(objField(observed, 'after', 'objects')) +
                                   '（要求新增 ≥' + minNew + '）—— 典型的 build 空跑');
        }
      }
      const runOk = !res.timedOut && res.exitCode === 0 && !gpuFailed && (!expectEval || expectEval.ok);
      // ---- v0.9.1（93-A4）：失败语义分档 —— "客户端超时"不等于"任务失败"，且要能按 runId 回收
      const failureClass = res.spawnErr ? 'blender_exe_missing'
        : res.timedOut ? 'timeout'
          : gpuFailed ? 'gpu_required_missing'
            : (res.exitCode !== 0 ? 'blender_error' : (lastException ? 'script_error' : 'finished'));
      const FAIL_HINT = {
        finished: null,
        script_error: '脚本抛异常了（退出码可能仍是 0）—— 看 traceback/lastException 指的那一行；结构化结果可能不存在',
        blender_error: 'Blender 进程退出码非 0（崩溃/参数错）—— 看 stderr 尾巴与日志路径',
        timeout: '超时被 SIGKILL：脚本没跑完。改小批量、或 asJob=true 转作业层（不占客户端连接）',
        gpu_required_missing: '要求 GPU 但设备不可用（gpu="true"/engine="cycles" 时）—— 见 gpu 段回执',
        blender_exe_missing: '起不来 Blender 进程：先配 blenderExe / DSH_BLENDER_EXE',
      }[failureClass] || null;
      // ---- v0.9.3（F1/F2）：shots 行 + 取景自诊断（主体占画面 <5% → warning）
      let shotsSummary: Record<string, unknown> | null = null;
      if (shotsRes) {
        const rowsIn: unknown[] = Array.isArray(shotsRes.views) ? shotsRes.views as unknown[] : [];
        const rows: Array<Record<string, unknown>> = rowsIn.map((x) => {
          const e: Record<string, unknown> = (x && typeof x === 'object') ? x as Record<string, unknown> : {};
          const frame = (e.frame && typeof e.frame === 'object') ? e.frame as Record<string, unknown> : null;
          const cov = (frame && typeof frame.coverage === 'number') ? frame.coverage : null;
          const row: Record<string, unknown> = { name: e.view || null, path: e.path || null, ms: e.ms || null,
                        bytes: e.bytes || null, md5: e.hash || null, res: e.res || null,
                        device: e.device || null, coverage_estimate: cov };
          if (cov !== null && cov < 0.05) {
            row.warning = { code: 'subject_too_small', coverage_estimate: cov,
              hint: '主体只占画面 ' + (cov * 100).toFixed(2) + '% —— 这个机位看不清细节：from 拉近 / 换 look_at / '
                  + '给这一项加 targets=[名字] 或 focus="x,y,z,r" 缩小取景范围（rt_see 同类判据）' };
          }
          return row;
        });
        const covs = rows.map((r) => r.coverage_estimate).filter((x): x is number => typeof x === 'number');
        shotsSummary = { ok: !!shotsRes.ok, count: rows.length, rows: rows,
                         coverage_min: covs.length ? Math.min.apply(null, covs) : null,
                         outdir: shotsRes.outdir || null, jsonl: shotsRes.jsonl || null,
                         total_ms: shotsRes.total_ms || null, render_ms: shotsRes.render_ms || null,
                         engine: shotsRes.engine || null, samples: shotsRes.samples || null,
                         device_line: shotsRes.device_line || null,
                         unknown_views: shotsRes.unknown_views || null,
                         scope_warnings: shotsRes.scope_warnings || null,
                         error: shotsRes.error || (shotsRes.ok === false ? (shotsRes.hint || 'shots 失败（见 error/traceback）') : null) };
        if (parsed === null) {
          parsed = { ok: !!shotsRes.ok, shots: rows, outdir: shotsSummary.outdir, jsonl: shotsSummary.jsonl,
                     count: rows.length, coverage_min: shotsSummary.coverage_min, error: shotsSummary.error };
        }
      }
      // ---- v0.9.1（93-A3）：结构化结果落盘（>4KB 自动落，或 outJson= 指定路径）—— 不必再从 stdout 里 indexOf 切片
      let outJsonInfo: { path: string | null; bytes: number; wsl: string | null } = { path: null, bytes: 0, wsl: null };
      try {
        if (parsed !== null && parsed !== undefined) {
          const s = JSON.stringify(parsed);
          outJsonInfo.bytes = s.length;
          if (opts.outJson) {
            const t = readScriptPath(String(opts.outJson));
            fs.mkdirSync(path.dirname(t.wsl), { recursive: true });
            fs.writeFileSync(t.wsl, s, 'utf8');
            outJsonInfo.path = t.win; outJsonInfo.wsl = t.wsl;      // D4.2：Windows 形态 + WSL 可见形态各一份
          } else if (s.length > 4000) {
            const d = dumpResult(parsed, runId);
            outJsonInfo.path = d.path; outJsonInfo.bytes = d.bytes;
            outJsonInfo.wsl = outJsonInfo.path ? winToWsl(outJsonInfo.path) : null;   // D4.2：两种形态都给
          }
        }
      } catch (e) { /* 落盘失败不影响主结果 */ }
      // ---- v0.8.10（A2/D4）：台账终态
      // v0.9.3（D2）：结构化回执的"真值"落在 runRec 上 —— op=collect/status 与同步回执同一份数据
      const resultTruncated = !!(parsed !== null && outJsonInfo.path && outJsonInfo.bytes > 4000);
      const stdoutTail = stdout.length > 4000 ? stdout.slice(-4000) : stdout;
      const stderrTail = stderr.length > 2000 ? stderr.slice(-2000) : stderr;
      try {
        runRec.status = res.timedOut ? 'killed' : (res.exitCode === 0 ? 'done' : 'failed');
        runRec.failureClass = failureClass;
        runRec.exitCode = res.exitCode; runRec.signal = res.signal; runRec.timedOut = !!res.timedOut; runRec.finishedAt = Date.now();
        runRec.ms = Number(runRec.finishedAt) - runRec.startedAt;
        runRec.artifacts = artifacts.slice(0, 20).map((a) => a.name);
        runRec.logs = logs; runRec.expect = expectEval;
        runRec.result = parsed; runRec.parseError = parseError;
        runRec.resultPath = outJsonInfo.path; runRec.resultPathWsl = outJsonInfo.wsl || null;
        runRec.resultBytes = outJsonInfo.bytes; runRec.resultTruncated = resultTruncated;
        runRec.pathWarnings = pathWarnings; runRec.shots = shotsSummary;
        runRec.inputFile = inputFileInfo;
        ledgerAppend({ id: runId, kind: 'headless', status: runRec.status, exitCode: res.exitCode,
                       ms: runRec.ms, outdir: runRec.outdir, script: runRec.script, pid: runRec.pid,
                       file: (fileOpt === undefined ? null : String(fileOpt)), inputFile: inputFileInfo,   // F-09（A3）
                       stage: runRec.stageName || null, resultPath: outJsonInfo.path,
                       stageMs: typeof runRec.stageAt === 'number' ? (runRec.stageAt - runRec.startedAt) : null,
                       artifacts: runRec.artifacts.slice(0, 8), expectOk: (expectEval ? expectEval.ok : null) });
      } catch (e) { /* ignore */ }
      try { if (stopRunHb) stopRunHb(); } catch (e) {}
      const hygieneWarnings = sceneHygieneAudit(stdout, stderr);
      // F-09：「成功但没开档」不再无声 —— 没传 file=（含 .py 语义切换）时给显式 no_input_file 警告
      const noFileWarning = (!fileOpt || fileLooksPython) ? [noInputFileWarning(fileLooksPython)] : [];
      runRec.warnings = (pathWarnings || []).concat(hygieneWarnings).concat(noFileWarning);
      return { ok: runOk, kind: 'headless', runId: runId, pluginVersion: PLUGIN_VERSION, expect: expectEval,
               warnings: runRec.warnings, progress: runRec.progress || null, progressAt: runRec.progressAt || null,
               heartbeat: runRec.heartbeat || null, progressHistory: runRec.progressHistory || [],
               stdoutTruncated: stdout.length > 8000,
        status: failureClass, resumable: true, session: SESSION_NAME,
        failure_hint: FAIL_HINT,
        how_to_recover: '客户端超时/断连不代表失败：这是独立子进程。用 blender_rt_job(op="status"|"collect"|"wait", id="' + runId +
                        '") 按 runId 回收结果与产物（台账：' + wslToWin(LEDGER) + '）',
        scriptFile: scriptFileInfo,
        inputFile: inputFileInfo,                                  // v0.9.3（F6）
        outJson: outJsonInfo.path, outJsonWsl: outJsonInfo.wsl || null,
        resultBytes: outJsonInfo.bytes,
        resultPath: outJsonInfo.path, resultPathWsl: outJsonInfo.wsl || null,
        resultTruncated: resultTruncated,
        resultJson: parsed, resultParseError: parseError,          // v0.9.3（D2/D3）
        stdoutTail: stdoutTail, stderrTail: stderrTail,
        stage: runRec.stage, stageName: runRec.stageName, stageAt: runRec.stageAt,
        lastOutputAt: runRec.lastOutputAt, lines: runRec.lines,
        shots: shotsSummary, pathWarnings: pathWarnings,           // v0.9.3（F1/F2/D4.3）
        outdir: { win: outdirWin, wsl: outdirWsl },
        childEnvKeys: Object.keys(childEnv).filter((k) => k.indexOf('DSH_') === 0),
        exitCode: res.exitCode, signal: res.signal, timedOut: !!res.timedOut,
        ms: ms, timeoutMs: timeoutMs, blender: BLENDER_EXE, script: sp ? sp.win : null, args: args,
        scriptStaging: sp ? (sp.staging || null) : null,              // v0.9.6（D3）：脚本落在哪 + 是否替换了 workDir
        preload: mods.length ? mods : undefined, gpu: gpu, engine: gpuAfterEngine(gpu),
        engineMode: (gpu && gpu.mode) || null, useUserConfig: !!opts.useUserConfig,
        logs: logs, lastException: lastException, traceback: traceback, hint: hint,
        noiseFiltered: noiseFiltered,
        reason: res.timedOut ? ('killed after timeout ' + timeoutMs + 'ms')
          : (gpuFailed ? 'gpu required but unavailable' : (res.exitCode === 0 ? null : 'exit code ' + String(res.exitCode))),
        result: parsed, stdout: clipMiddle(stdout, 4000, 4000), stderr: clipMiddle(stderr, 2000, 2000), artifacts: artifacts };
    },
  };
}
