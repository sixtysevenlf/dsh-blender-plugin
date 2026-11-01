import { winToWsl, wslToWin } from '../../config.js'
import { BLENDER_EXE, WIN_TMP } from '../../paths.js'
import { PLUGIN_VERSION, pathAudit, sceneHygieneAudit } from '../../core/evidence.js'
import { prepareHeadlessOptions } from './options/index.js'
import { buildHeadlessScript } from './script.js'
import { createHeadlessLogStreams } from './process.js'
import {
  applyShotsResult,
  classifyFailure,
  collectArtifacts,
  evaluateExpect,
  extractFailureContext,
  finishRunWarnings,
  failureHint,
  gpuAfterEngine,
  parseHeadlessStdout,
  summarizeShots,
  withWorkdirWarning,
  writeResultJson,
} from './result.js'
import { buildHeadlessReceipt } from './receipt.js'
import { createRunState } from './run-state.js'
import { buildChildEnv } from './child-env.js'
import { closeHeadlessLogStreams, executeHeadless } from './execute.js'
import { isJobMode, jobModeReceipt } from './job-mode.js'
import { writeFallbackLogs } from './logs.js'
import type { HeadlessDeps, HeadlessOpts } from './types.js'

export function createHeadlessRun(E: HeadlessDeps) {
  const {
    LEDGER,
    SESSION_NAME,
    dumpResult,
    jobStart,
    ledgerAppend,
    metrics,
    readScriptPath,
    runs,
  } = E
  return {
    async headless(opts: HeadlessOpts = {}): Promise<Record<string, unknown>> {
      const t0 = Date.now()
      const p = prepareHeadlessOptions(opts, E)
      const built = buildHeadlessScript(p)
      metrics.headlessRuns++
      // ---- v0.9.3（D1/D5）：日志**边跑边写** + 运行台账登记（id 与作业同空间：op=status/collect id=run-…）
      const logDirWsl = p.outdirWsl || winToWsl(WIN_TMP)
      const logStreams = createHeadlessLogStreams(logDirWsl, t0, p.runId)
      let logs = logStreams.logs
      const { runRec, stopRunHb } = createRunState({
        p,
        built,
        t0,
        logDirWsl,
        logs,
        runs,
        ledgerAppend,
      })
      // v0.8.7（外部反馈 #4 P0-2）/ v0.9.3（D1）：长任务转作业层 —— 客户端超时不再把结果丢掉。
      // 用法：as_job=true 强制转；或给 auto_job_ms（如 120000）表示预计超过它就走作业。
      if (isJobMode(opts, p)) {
        runs.delete(p.runId) // 转作业后这次 run 不存在了，别留一个假 running
        try {
          const j = jobStart(
            Object.assign({}, opts, { timeoutMs: Math.max(p.timeoutMs, 3600000), runId: p.runId }),
          )
          return jobModeReceipt({ opts, p, t0, job: j })
        } finally {
          await closeHeadlessLogStreams(logStreams)
        }
      }
      // ---- 子进程环境：可选透传用户 Blender 配置（GPU 偏好在里面）
      const childEnv = buildChildEnv(p, opts, SESSION_NAME)
      const { res, stdout, stderr, ms, logsClosed } = await executeHeadless({
        p,
        built,
        childEnv,
        runRec,
        logStreams,
        t0,
      })
      metrics.lastHeadlessMs = ms
      const parsedOut = parseHeadlessStdout(stdout)
      let parsed = parsedOut.parsed
      // ---- v0.9.3（D4.3）：路径静默改写体检
      const pathWarnings: unknown[] = pathAudit(stdout, stderr)
      // ---- v0.9.6（D3）：workDir 不共享 → 脚本改落别处，这件事必须出现在回执里（不许静默）
      withWorkdirWarning(pathWarnings, built.sp ? built.sp.staging : null)
      // ---- 日志兜底：正常路径已在 push() 里边跑边写（D5）；流没建起来时这里补一次全量
      if (!logStreams.soStream) {
        logs = writeFallbackLogs(logDirWsl, logStreams.stamp, p.runId, stdout, stderr)
      }
      runRec.logs = logs
      const failure = extractFailureContext(stderr, p.script)
      const artifacts = collectArtifacts(p.outdirWsl, t0, opts.includeNoise)
      const gpuFailed = p.gpuManual && parsedOut.gpu && parsedOut.gpu.ok === false
      const expectEval = evaluateExpect(p.exp, stdout)
      const runOk =
        !res.timedOut && res.exitCode === 0 && !gpuFailed && (!expectEval || expectEval.ok)
      const failureClass = classifyFailure(res, !!gpuFailed, failure.lastException)
      const FAIL_HINT = failureHint(failureClass)
      // ---- v0.9.3（F1/F2）：shots 行 + 取景自诊断（主体占画面 <5% → warning）
      const shotsSummary = parsedOut.shotsRes ? summarizeShots(parsedOut.shotsRes) : null
      parsed = applyShotsResult(parsed, shotsSummary)
      const outJsonInfo = writeResultJson(parsed, opts, { dumpResult, readScriptPath }, p.runId)
      // ---- v0.8.10（A2/D4）：台账终态
      // v0.9.3（D2）：结构化回执的"真值"落在 runRec 上 —— op=collect/status 与同步回执同一份数据
      const resultTruncated = !!(parsed !== null && outJsonInfo.path && outJsonInfo.bytes > 4000)
      try {
        runRec.status = res.timedOut ? 'killed' : res.exitCode === 0 ? 'done' : 'failed'
        runRec.failureClass = failureClass
        runRec.exitCode = res.exitCode
        runRec.signal = res.signal
        runRec.timedOut = !!res.timedOut
        runRec.finishedAt = Date.now()
        runRec.ms = Number(runRec.finishedAt) - runRec.startedAt
        runRec.artifacts = artifacts.artifacts.slice(0, 20).map((a) => a.name)
        runRec.logs = logs
        runRec.expect = expectEval
        runRec.result = parsed
        runRec.parseError = parsedOut.parseError
        runRec.resultPath = outJsonInfo.path
        runRec.resultPathWsl = outJsonInfo.wsl || null
        runRec.resultBytes = outJsonInfo.bytes
        runRec.resultTruncated = resultTruncated
        runRec.pathWarnings = pathWarnings
        runRec.shots = shotsSummary
        runRec.inputFile = p.inputFileInfo
        ledgerAppend({
          id: p.runId,
          kind: 'headless',
          status: runRec.status,
          exitCode: res.exitCode,
          ms: runRec.ms,
          outdir: runRec.outdir,
          script: runRec.script,
          pid: runRec.pid,
          file: p.fileOpt === undefined ? null : String(p.fileOpt),
          inputFile: p.inputFileInfo, // F-09（A3）
          stage: runRec.stageName || null,
          resultPath: outJsonInfo.path,
          stageMs: typeof runRec.stageAt === 'number' ? runRec.stageAt - runRec.startedAt : null,
          artifacts: runRec.artifacts.slice(0, 8),
          expectOk: expectEval ? expectEval.ok : null,
        })
      } catch (e) {
        /* ignore */
      }
      try {
        if (stopRunHb) stopRunHb()
      } catch (e) {}
      runRec.warnings = finishRunWarnings(
        pathWarnings,
        sceneHygieneAudit(stdout, stderr),
        p.fileOpt,
        p.fileLooksPython,
      )
      const receipt = buildHeadlessReceipt({
        ok: runOk,
        runId: p.runId,
        session: SESSION_NAME,
        pluginVersion: PLUGIN_VERSION,
        expect: expectEval,
        runRec: runRec,
        stdout: stdout,
        stderr: stderr,
        status: failureClass,
        failureHint: FAIL_HINT,
        ledgerWin: wslToWin(LEDGER),
        scriptFile: p.scriptFileInfo,
        inputFile: p.inputFileInfo,
        outJson: outJsonInfo,
        result: parsed,
        parseError: parsedOut.parseError,
        shots: shotsSummary,
        pathWarnings: pathWarnings,
        ms: ms,
        timeoutMs: p.timeoutMs,
        outdirWin: p.outdirWin,
        outdirWsl: p.outdirWsl,
        childEnvKeys: Object.keys(childEnv).filter((k) => k.indexOf('DSH_') === 0),
        res: res,
        logs: logs,
        failure: failure,
        artifacts: artifacts,
        noiseFiltered: artifacts.noiseFiltered,
        blenderExe: BLENDER_EXE,
        built: built,
        preload: p.mods.length ? p.mods : undefined,
        gpu: parsedOut.gpu,
        engine: gpuAfterEngine(parsedOut.gpu),
        engineMode: (parsedOut.gpu && parsedOut.gpu.mode) || null,
        useUserConfig: !!opts.useUserConfig,
        resultTruncated: resultTruncated,
      })
      await logsClosed
      return receipt
    },
  }
}
