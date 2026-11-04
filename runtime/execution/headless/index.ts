import { wslToWin } from '../../config.js'
import { isJobMode } from './job-mode.js'
import {
  buildRunReceipt,
  collectHeadlessOutcome,
  finishRunRecord,
  headlessJobReceipt,
  prepareRun,
  recordCompletedRun,
} from './run-stages.js'
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
      const run = prepareRun(opts, E)
      // v0.8.7（外部反馈 #4）/ v0.9.3：长任务转作业层 —— 客户端超时不再把结果丢掉。
      // 用法：as_job=true 强制转；或给 auto_job_ms（如 120000）表示预计超过它就走作业。
      // 同步检查放在 await 之前：非作业模式下必须像拆分前一样同步进入 spawn。
      if (isJobMode(opts, run.p)) {
        const job = await headlessJobReceipt({
          opts,
          p: run.p,
          t0: run.t0,
          logStreams: run.logStreams,
          runs,
          jobStart,
        })
        if (job) return job
      }
      const outcome = await collectHeadlessOutcome({
        p: run.p,
        built: run.built,
        opts,
        runRec: run.runRec,
        logStreams: run.logStreams,
        logDirWsl: run.logDirWsl,
        t0: run.t0,
        sessionName: SESSION_NAME,
        metrics,
        dumpResult,
        readScriptPath,
      })
      // ---- v0.8.10：台账终态（结构化回执的"真值"落在 runRec 上）
      recordCompletedRun({
        p: run.p,
        runRec: run.runRec,
        res: outcome.res,
        failureClass: outcome.failureClass,
        artifacts: outcome.artifacts,
        logs: outcome.logs,
        expectEval: outcome.expectEval,
        parsed: outcome.parsed,
        parsedOut: outcome.parsedOut,
        outJsonInfo: outcome.outJsonInfo,
        resultTruncated: outcome.resultTruncated,
        pathWarnings: outcome.pathWarnings,
        shotsSummary: outcome.shotsSummary,
        ledgerAppend,
      })
      finishRunRecord({
        runRec: run.runRec,
        stopRunHb: run.stopRunHb,
        pathWarnings: outcome.pathWarnings,
        stdout: outcome.stdout,
        stderr: outcome.stderr,
        fileOpt: run.p.fileOpt,
        fileLooksPython: run.p.fileLooksPython,
      })
      const receipt = buildRunReceipt({
        outcome,
        p: run.p,
        opts,
        runRec: run.runRec,
        sessionName: SESSION_NAME,
        ledgerWin: wslToWin(LEDGER),
      })
      await outcome.logsClosed
      return receipt
    },
  }
}
