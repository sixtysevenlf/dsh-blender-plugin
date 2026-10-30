// 作业层：start/status/collect/wait/kill/list（T6）
// @imports
import { winToWsl, wslToWin } from "../config.mjs";
import { BLENDER_EXE, GPU_PRELUDE, WIN_TMP } from "../paths.mjs";
import path from "node:path";
import fs from "node:fs";
import { PLUGIN_VERSION, pathAudit, pidAlive, sceneHygieneAudit } from "../core/evidence.mjs";
import { slipFixPath } from "../core/output.mjs";
import { describeMissingInput, noInputFileWarning, validateFileOpt } from "../file_guard.mjs";
import { PATH_GUARD, envPrelude, fileFingerprint, scriptFileBlock, shotsBlock, workdirBlock } from "../core/scripts.mjs";
import { KERNEL_BOOTSTRAP } from "../core/kernel.mjs";
import { hardExitJobParts } from "../hard_exit.mjs";
import { writeHeadlessScript } from "../staging.mjs";
import { baseChildEnv, withWslEnv, workdirStagingWarning } from "../proc_env.mjs";
import { spawn } from "node:child_process";
import { makeStageScanner, startHeartbeat } from "../core/progress.mjs";

import { createLedger } from "./ledger.mjs";

export function createJobsApi(E) {
  const { HERE, SESSION_NAME, dumpResult, preloadChunk, readScriptPath } = E;
  const ledger = E.ledger || createLedger(E);
  const { runs, LEDGER, ledgerAppend, ledgerList, reconcileLedger, runSnapshot } = ledger;
  // ---------------------------------------------------------------- 作业层（v0.8.5）
  // 长任务后台化：start 立刻返回 job id（不占客户端连接）；status/collect/kill 轮询；日志与产物落在 outdir/jobs/<id>/。
  const jobs = new Map();
  function jobDir(id, outdir) {
    const base = outdir ? winToWsl(String(outdir)) : winToWsl(WIN_TMP);
    const d = path.join(base, 'jobs', id);
    fs.mkdirSync(d, { recursive: true });
    return d;
  }
  function jobArtifacts(j) {
    const dir = j.outdir ? winToWsl(String(j.outdir)) : null;
    if (!dir) return [];
    try {
      const isNoise = (n) => n.indexOf('__pycache__') === 0 || /\.pyc$/.test(n) || /\.blend[12]$/.test(n) || n.indexOf('tmp') === 0;
      return fs.readdirSync(dir).map((n) => { const p = path.join(dir, n); let st = null; try { st = fs.statSync(p); } catch (e) {} return st && st.isFile() && !isNoise(n) ? { name: n, bytes: st.size, mtimeMs: st.mtimeMs } : null; })
        .filter(Boolean).sort((a, b) => b.mtimeMs - a.mtimeMs).slice(0, 40);
    } catch (e) { return []; }
  }
  function jobSnapshot(j) {
    // v0.8.9：作业结束后 ms 必须冻结（旧版用 Date.now()，done 的作业放一会儿再看会显示成分钟级）
    const now = Date.now();
    const running = j.status === 'running';
    const outWsl = j.outdirWsl || (j.outdir ? winToWsl(String(j.outdir)) : winToWsl(WIN_TMP));
    let logBytes = j.logBytes || 0;
    if (running) { try { logBytes = fs.statSync(path.join(j.dir, 'stdout.log')).size; } catch (e) { /* 还没建 */ } }
    return { kind: 'job', id: j.id, runId: j.runId || null, status: j.status, pid: j.pid,
             pidAlive: running ? pidAlive(j.pid) : false,
             ms: (j.finishedAt || now) - j.startedAt, timeoutMs: j.timeoutMs,
             outdir: j.outdir || WIN_TMP, outdirWsl: outWsl,
             logDir: wslToWin(j.dir), logDirWsl: j.dir,
             stdoutLog: wslToWin(path.join(j.dir, 'stdout.log')), stderrLog: wslToWin(path.join(j.dir, 'stderr.log')),
             stdoutLogWsl: path.join(j.dir, 'stdout.log'), stderrLogWsl: path.join(j.dir, 'stderr.log'),
             exitCode: j.exitCode, signal: j.signal, engine: j.engineMode,
             // ---- v0.9.3（D5）：运行中就能看到进度（不再"6 分钟纯黑盒"）
             stage: j.stage || null, stageName: j.stageName || null, stageAt: j.stageAt || null,
             stageAgoMs: j.stageAt ? (now - j.stageAt) : null,
             lastOutputAt: j.lastOutputAt || null,
             idleMs: j.lastOutputAt ? (now - j.lastOutputAt) : (now - j.startedAt),
             lines: j.lines || 0, logBytes: logBytes,
             // ---- v0.9.3（D2/D3）：结构化结果（不必再手写正则从 stdout 里抠）
             resultJson: j.parsed || null, result: j.parsed || null, resultParseError: j.parseError || null,
             resultPath: j.resultPath || null, resultPathWsl: j.resultPathWsl || null,
             resultBytes: j.resultBytes || 0, resultTruncated: !!j.resultTruncated,
             stdoutTail: j.stdoutTail || null, stderrTail: j.stderrTail || null,
             pathWarnings: j.pathWarnings || [], warnings: j.warnings || j.pathWarnings || [], shots: j.shots || null,
             progress: j.progress || null, progressAt: j.progressAt || null,
             progressAgeMs: j.progressAt ? (now - j.progressAt) : null,
             heartbeat: j.heartbeat || null, progressHistory: j.progressHistory || [],
             scriptStaging: j.scriptStaging || null,
             inputFile: j.inputFile || null, scriptFile: j.scriptFile || null,
             lastException: j.lastException || null,
             artifacts: running ? [] : jobArtifacts(j) };
  }
  /**
   * 起一个后台作业。
   * v0.9.3（D6.1）：与 headless 同形参 —— script_file / args / env / factory_startup / bootstrap /
   *   workdir / include_noise 都收（旧版只认 script，靠 DSH_ARGS 取参的 preview.py 无法复用）。
   * v0.9.3（D5）：env 前导 + PYTHONUNBUFFERED + stage 心跳扫描（stdout.log 运行期就有增量）。
   */
  function jobStart(opts = {}) {
    const id = 'job-' + Date.now().toString(36) + Math.random().toString(36).slice(2, 5);
    const outdirArg = opts.outdir ? slipFixPath(String(opts.outdir), null) : null;
    const outdirWsl = outdirArg ? winToWsl(outdirArg) : winToWsl(WIN_TMP);
    const outdirWin = outdirArg ? wslToWin(outdirArg) : WIN_TMP;
    const dir = jobDir(id, outdirWsl);
    const outPath = path.join(dir, 'stdout.log'), errPath = path.join(dir, 'stderr.log');
    const engineMode = String(opts.engine === undefined || opts.engine === null ? 'eevee' : opts.engine).toLowerCase();
    const runId = /^[A-Za-z0-9_-]{1,64}$/.test(String(opts.runId || '')) ? String(opts.runId)
      : ('run-' + Date.now().toString(36) + Math.random().toString(36).slice(2, 5));
    // ---- script_file（与 headless 同一套语义：显式收 .py；file= 传 .py 自动改当脚本）
    let scriptFileInfo = null;
    let script = opts.script ? String(opts.script) : '';
    const fileOpt = validateFileOpt(opts.file);   // F-09：空串/类型错显式报错，不再静默当"没传"
    const fileLooksPython = !!(fileOpt && /\.py$/i.test(String(fileOpt)));
    if (opts.scriptFile || fileLooksPython) {
      const given = String(opts.scriptFile || fileOpt);
      try {
        const rp = readScriptPath(given);
        script = fs.readFileSync(rp.wsl, 'utf8');
        scriptFileInfo = { given: given, resolved: rp.win, bytes: script.length,
                           auto_from_file: !opts.scriptFile && fileLooksPython };
      } catch (e) {
        throw Object.assign(new Error('读不到脚本文件：' + given + ' —— ' + String((e && e.message) || e).slice(0, 160)),
                            { hint: 'script_file 用 .py 路径（Windows D:\\… 或 WSL /home/… 都行）；file= 是 .blend' });
      }
    }
    const inputFileInfo = (fileOpt && !fileLooksPython)
      ? fileFingerprint(wslToWin(String(fileOpt)), winToWsl(String(fileOpt)))
      : describeMissingInput(fileOpt, fileLooksPython);   // F-09：null → {given,opened,reason}（形态冻结：truthy 分支一字未动）
    const args = ['-b'];
    if (fileOpt && !fileLooksPython) args.push(wslToWin(String(fileOpt)));
    if (opts.factoryStartup !== false) args.push('--factory-startup');
    const envPairs = { DSH_RUN_ID: runId, DSH_OUTDIR: outdirWin, DSH_SESSION: SESSION_NAME,
                       DSH_PLUGIN_VERSION: PLUGIN_VERSION,
                       DSH_ARGS: JSON.stringify(Array.isArray(opts.args) ? opts.args.map(String) : []) };
    if (opts.env && typeof opts.env === 'object') for (const k of Object.keys(opts.env)) envPairs[String(k)] = String(opts.env[k]);
    if (scriptFileInfo) envPairs.DSH_SCRIPT_FILE = scriptFileInfo.resolved;   // 反馈 #4
    const parts = [envPrelude(envPairs)];
    if (opts.bootstrap !== false) parts.push(KERNEL_BOOTSTRAP);
    parts.push('_dsh_stage_emit("engine-prelude")');
    parts.push(['try:', '    _DSH_FP0 = bpy.context.scene.render.filepath', 'except Exception:', '    _DSH_FP0 = None'].join(String.fromCharCode(10)));
    if (engineMode !== 'keep') parts.push(GPU_PRELUDE.replace(/__DSH_ENGINE__/g, "'" + (engineMode === 'cycles' ? 'cycles' : 'eevee') + "'").replace(/__DSH_GPU_MANUAL__/g, 'False'));
    // v0.8.9：作业层也支持 preload（与 headless 同一套：把 runtime/<name>.py 源码拼到脚本开头）
    const jobMods = Array.isArray(opts.preload) ? opts.preload : (opts.preload ? String(opts.preload).split(',') : []);
    // v0.9.3（F1）：作业层同样支持 shots（as_job=true 时 headless 就是转到这里跑的）
    const jobShotsSpec = Array.isArray(opts.shots) ? { views: opts.shots }
      : (opts.shots && typeof opts.shots === 'object' ? Object.assign({}, opts.shots) : null);
    const jobShotsWanted = !!(jobShotsSpec && Array.isArray(jobShotsSpec.views) && jobShotsSpec.views.length);
    if (jobShotsWanted && !jobMods.some((m) => String(m).trim().replace(/\.py$/, '') === 'qc_render')) jobMods.push('qc_render');
    for (const m of jobMods) {
      const name = String(m).trim().replace(/\.py$/, '');
      if (!name) continue;
      const f = path.join(HERE, name + '.py');
      if (!fs.existsSync(f)) {
        // S9（④）：报错直接给可用模块名 —— 现场「要 preload=vehicle」只能靠试。
        let avail = [];
        try { avail = fs.readdirSync(HERE).filter((x) => x.endsWith('.py')).map((x) => x.slice(0, -3)); } catch (e2) { /* ignore */ }
        throw new Error('preload 找不到模块：' + f + '（可用模块名：' + avail.join(', ') + '）—— preload 取 runtime/<name>.py 的文件名，例如 preload="vehicle,audit"；离线（后端挂了）见 runtime/offline_bootstrap.py');
      }
      parts.push(preloadChunk(name));
    }
    parts.push('_dsh_stage_emit("preload-done")');
    if (opts.workdir) parts.push(workdirBlock(slipFixPath(String(opts.workdir), null)));
    if (scriptFileInfo) parts.push(scriptFileBlock(scriptFileInfo.resolved));
    parts.push(script);
    parts.push('_dsh_stage_emit("script-end")');
    if (jobShotsWanted) parts.push(shotsBlock(Object.assign({ engine: 'keep', lock: true, warmup: true, tag: 'shot' }, jobShotsSpec, { outdir: outdirWin })));
    parts.push(PATH_GUARD);
    parts.push('_dsh_stage_emit("done")');
    // F-14：done 之后冲刷 stdout/stderr 再 os._exit(0)（跳过 Blender 退出拆解；hardExit=false 回旧行为）
    for (const hx of hardExitJobParts(opts)) parts.push(hx);
    const sp = writeHeadlessScript(parts.join('\n'));
    args.push('--python', sp.win, '--');
    if (opts.outdir) args.push(outdirWin);
    if (Array.isArray(opts.args)) args.push.apply(args, opts.args.map(String));
    const timeoutMs = Math.max(1000, Math.min(86400000, Number(opts.timeoutMs) || 3600000));
    const so = fs.createWriteStream(outPath), se = fs.createWriteStream(errPath);
    const j = { kind: 'job', id: id, runId: runId, child: null, pid: null, startedAt: Date.now(), status: 'running',
                exitCode: null, signal: null, timeoutMs: timeoutMs, outdir: outdirWin, outdirWsl: outdirWsl,
                dir: dir, engineMode: engineMode, parsed: null, parseError: null, lastException: null,
                script: sp.win, scriptStaging: sp.staging || null, scriptFile: scriptFileInfo, inputFile: inputFileInfo, args: args,
                stage: null, stageAt: null, stageName: null, lastOutputAt: Date.now(), lines: 0, logBytes: 0,
                resultPath: null, resultBytes: 0, resultTruncated: false, pathWarnings: [], shots: null };
    if (j.scriptStaging && j.scriptStaging.substituted) {
      const w = workdirStagingWarning(j.scriptStaging);
      if (w) j.pathWarnings.push(w);
    }
    const child = spawn(BLENDER_EXE, args, { env: withWslEnv(baseChildEnv(Object.assign({}, envPairs)), opts.env),
                                             stdio: ['ignore', 'pipe', 'pipe'], detached: false, windowsHide: true });
    j.child = child; j.pid = child.pid;
    // v0.9.3（D5）：stage 扫描 + 行数/字节计数（stdout 仍然 pipe 到日志文件）
    const scanOut = makeStageScanner(j), scanErr = makeStageScanner(j);
    child.stdout.on('data', (d) => { try { j.logBytes += d.length; scanOut(d.toString('utf8')); } catch (e) { /* ignore */ } });
    child.stderr.on('data', (d) => { try { scanErr(d.toString('utf8')); } catch (e) { /* ignore */ } });
    child.stdout.pipe(so); child.stderr.pipe(se);
    jobs.set(id, j); if (jobs.size > 20) { const k = jobs.keys().next().value; if (k !== id) jobs.delete(k); }
    ledgerAppend({ id: id, kind: 'job', status: 'running', startedAt: j.startedAt, pid: j.pid, script: sp.win,
                   runId: runId, outdir: outdirWin, engine: engineMode, timeoutMs: timeoutMs, stage: null,
                   file: (fileOpt === undefined ? null : String(fileOpt)),   // F-09（A3）：台账记 file，事后可区分"没给档"与"给了没开"
                   dir: wslToWin(dir), stdoutLog: wslToWin(outPath), stderrLog: wslToWin(errPath) });
    j.timer = setTimeout(() => { j.status = 'killed'; try { child.kill('SIGKILL'); } catch (e) {} }, timeoutMs);
    const stopHb = startHeartbeat(j, dir);
    child.on('close', (code, sig) => {
      clearTimeout(j.timer); stopHb(); j.exitCode = code; j.signal = sig; j.finishedAt = Date.now();
      if (j.status !== 'killed') j.status = (code === 0) ? 'done' : 'failed';
      try {
        const txt = fs.readFileSync(outPath, 'utf8');
        // v0.9.3（D3）：解析失败不再覆盖 raw —— 单独记 resultParseError，stdoutTail 保留原文
        const lines = txt.split('\n').filter((l) => l.indexOf('HEADLESS ') === 0);
        if (lines.length) {
          const lastHead = lines[lines.length - 1].slice('HEADLESS '.length).trim();
          try { j.parsed = JSON.parse(lastHead); }
          catch (e) { j.parseError = { message: String((e && e.message) || e).slice(0, 200), line: lastHead.slice(0, 400) }; }
        }
        const sl = txt.split('\n').filter((l) => l.indexOf('DSH_SHOTS ') === 0);
        if (sl.length) { try { j.shots = JSON.parse(sl[sl.length - 1].slice('DSH_SHOTS '.length).trim()); } catch (e) { /* ignore */ } }
        j.stdoutTail = txt.length > 4000 ? txt.slice(-4000) : txt;
        j.pathWarnings = pathAudit(txt, '');
        // F-09：作业路径同样不许"成功但没开档"无声（与 sync 路径同一份守卫语义）
        j.warnings = (j.pathWarnings || []).concat(sceneHygieneAudit(txt, '')).concat((!fileOpt || fileLooksPython) ? [noInputFileWarning(fileLooksPython)] : []);
      } catch (e) { /* ignore */ }
      try {
        const er = fs.readFileSync(errPath, 'utf8');
        j.stderrTail = er.length > 2000 ? er.slice(-2000) : er;
        const m = er.match(/^[\w.]*(?:Error|Exception|Warning)\b.*$/gm);
        if (m && m.length) j.lastException = m[m.length - 1].slice(0, 300);
        if (j.pathWarnings.length === 0) j.pathWarnings = pathAudit('', er);
      } catch (e) { /* ignore */ }
      // v0.9.6（D3）：pathAudit 会整体覆盖 —— 把"workDir 不共享、脚本改落别处"这条找回来（追加在末尾）
      if (j.scriptStaging && j.scriptStaging.substituted
          && !(j.pathWarnings || []).some((w) => w && w.code === 'WORKDIR_NOT_SHARED')) {
        const w = workdirStagingWarning(j.scriptStaging);
        if (w) j.pathWarnings = (j.pathWarnings || []).concat([w]);
      }
      // v0.9.3（D2）：作业结果也走 out_json 落盘（>4KB 自动落），与 headless 同一套
      try {
        if (j.parsed !== null && j.parsed !== undefined) {
          const s = JSON.stringify(j.parsed);
          j.resultBytes = s.length;
          if (opts.outJson) {
            const t = readScriptPath(String(opts.outJson));
            fs.mkdirSync(path.dirname(t.wsl), { recursive: true });
            fs.writeFileSync(t.wsl, s, 'utf8');
            j.resultPath = t.win; j.resultPathWsl = t.wsl;
          } else if (s.length > 4000) {
            const d = dumpResult(j.parsed, id);
            j.resultPath = d.path; j.resultPathWsl = d.path ? winToWsl(d.path) : null;
          }
          j.resultTruncated = !!(j.resultPath && s.length > 4000);
        }
      } catch (e) { /* ignore */ }
      // v0.8.10（D4）：终态摘要落盘 + 台账追加（后端重启后仍可查）
      try {
        const arts = jobArtifacts(j).slice(0, 20);
        const summary = { id: id, kind: 'job', status: j.status, exitCode: j.exitCode, signal: j.signal,
                          startedAt: j.startedAt, finishedAt: Date.now(), ms: Date.now() - j.startedAt,
                          outdir: outdirWin, script: sp.win, engine: engineMode, runId: runId,
                          file: (fileOpt === undefined ? null : String(fileOpt)), inputFile: j.inputFile,   // F-09（A3）
                          stage: j.stageName || null,
                          artifacts: arts.map((a) => a.name + '(' + a.bytes + 'B)'),
                          resultSummary: j.parsed ? JSON.stringify(j.parsed).slice(0, 400) : null,
                          lastException: j.lastException || null, pluginVersion: PLUGIN_VERSION };
        try { fs.writeFileSync(path.join(j.dir, 'summary.json'), JSON.stringify(summary, null, 1), 'utf8'); } catch (e) { /* ignore */ }
        ledgerAppend(Object.assign({}, summary, { artifacts: arts.slice(0, 8).map((a) => a.name) }));
      } catch (e) { /* ignore */ }
    });
    child.on('error', (e) => { j.status = 'failed'; j.lastException = String((e && e.message) || e); });
    return jobSnapshot(j);
  }

  function jobStatus(id) {
    const key = String(id);
    const j = jobs.get(key);
    if (j) { const s = jobSnapshot(j); s.stale = false; return s; }
    const r = runs.get(key);
    if (r) { const s = runSnapshot(r); s.stale = false; return s; }
    const led = ledgerList(200).find((x) => x.id === key);
    return led ? Object.assign({ kind: led.kind || 'ledger', fromLedger: true }, reconcileLedger(led))
               : { id: key, status: 'unknown', ok: false, hint: '没有这个 id 的记录（内存台账 + 磁盘台账都查过了）' };
  }
  /** 从日志文件里取尾部（run 与 job 通用） */
  function tailFile(p, tail) {
    try { return fs.readFileSync(p, 'utf8').slice(-tail); } catch (e) { return null; }
  }
  function jobCollect(id, tail = 4000) {
    const key = String(id);
    const j = jobs.get(key);
    if (j) {
      const snap = jobSnapshot(j);
      const so = tailFile(path.join(j.dir, 'stdout.log'), tail);
      const se = tailFile(path.join(j.dir, 'stderr.log'), tail);
      if (so !== null) snap.stdoutTail = so;
      if (se !== null) snap.stderrTail = se;
      return snap;
    }
    const r = runs.get(key);
    if (r) {
      const snap = runSnapshot(r);
      const soP = r.logs && r.logs.stdoutWsl ? r.logs.stdoutWsl : null;
      const seP = r.logs && r.logs.stderrWsl ? r.logs.stderrWsl : null;
      // v0.9.3（D5）：日志是边跑边写的 → 运行中也能 tail（旧版 collect 对 run 直接回 unknown）
      if (soP) { const t = tailFile(soP, tail); if (t !== null) snap.stdoutTail = t; }
      if (seP) { const t = tailFile(seP, tail); if (t !== null) snap.stderrTail = t; }
      snap.hint = 'run 与 job 同一 id 空间；本条目来自 blender_rt_headless 的同步执行';
      return snap;
    }
    const led = ledgerList(200).find((x) => x.id === key);
    if (!led) return { id: key, status: 'unknown', ok: false, hint: '没有这个 id 的记录；op=list 看全部' };
    const rec = reconcileLedger(led);
    if (rec.status === 'running' || rec.status === 'stale') {
      rec.stdoutTail = rec.stdoutLog ? tailFile(winToWsl(rec.stdoutLog), tail) : null;
      rec.stderrTail = rec.stderrLog ? tailFile(winToWsl(rec.stderrLog), tail) : null;
    }
    return Object.assign({ kind: led.kind || 'ledger', fromLedger: true }, rec);
  }
  /**
   * v0.9.3（D6.2）：阻塞等待 —— 旧版只能循环 op=status，会撞外层 harness 的
   * "Repeated tool call detected (consecutive_calls: 5/8)"；op=collect 又不等。
   * 语义：等到终态或 timeout_ms 到点（默认 120s，上限 600s/次，可反复调）。
   */
  async function jobWait(id, timeoutMs = 120000) {
    const key = String(id);
    const budget = Math.max(1000, Math.min(600000, Number(timeoutMs) || 120000));
    const t0 = Date.now();
    let waited = 0;
    for (;;) {
      const st = jobStatus(key);
      if (st.status !== 'running') return Object.assign(jobCollect(key), { waitedMs: Date.now() - t0, waitTimedOut: false });
      if (Date.now() - t0 >= budget) {
        return Object.assign(jobCollect(key), { waitedMs: Date.now() - t0, waitTimedOut: true,
          hint: '等待窗口到点，任务**仍在跑**（不是失败）：再调一次 op=wait，或 op=collect 看 stage/idleMs' });
      }
      await new Promise((r2) => setTimeout(r2, 500));
      waited++;
      if (waited > 2400) break;                      // 安全上限（>20min 的极端轮次）
    }
    return Object.assign(jobCollect(key), { waitedMs: Date.now() - t0, waitTimedOut: true });
  }
  function jobKill(id) {
    const key = String(id);
    const j = jobs.get(key);
    if (j) {
      if (j.status === 'running') { j.status = 'killed'; try { j.child.kill('SIGKILL'); } catch (e) { /* 可能已退出 */ } }
      return jobSnapshot(j);
    }
    const r = runs.get(key);
    if (r) {
      if (r.status === 'running') {
        r.status = 'killed'; r.finishedAt = Date.now();
        try { if (r.child) r.child.kill('SIGKILL'); } catch (e) { /* 可能已退出 */ }
        ledgerAppend({ id: key, kind: 'headless', status: 'killed', killedBy: 'op=kill', ms: Date.now() - r.startedAt });
      }
      return runSnapshot(r);
    }
    const led = ledgerList(200).find((x) => x.id === key);
    const rec = led ? reconcileLedger(led) : null;
    // v0.9.3（D6.4）：未知 id **不抛错** —— 旧版回 unknown 让调用方流程中断
    return { id: key, ok: true, status: (rec && rec.status) || 'unknown', alreadyFinished: !(rec && rec.status === 'running'),
             fromLedger: !!rec,
             note: rec ? '这条来自磁盘台账，内存里没有在跑的句柄（已结束或后端重启过）；无需 kill'
                       : '没有这个 id 的记录（可能已结束/被清理）—— 不需要 kill，op=list 看当前作业' };
  }
  function jobList() {
    const mem = Array.from(jobs.values()).map(jobSnapshot).concat(Array.from(runs.values()).map(runSnapshot));
    const ids = new Set(mem.map((x) => x.id));
    const disk = ledgerList(60).filter((x) => !ids.has(x.id)).map((x) => Object.assign({ kind: x.kind || 'ledger', fromLedger: true }, reconcileLedger(x)));
    return mem.concat(disk);
  }
  return { jobs, jobStart, jobSnapshot, jobStatus, jobCollect, jobWait, jobKill, jobList, jobDir, jobArtifacts, tailFile, ledger };
}
