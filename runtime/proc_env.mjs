/**
 * S8 · 子进程环境与安全 JSON 读取（从 engine.mjs 外移）—— launch.mjs 需要它们（S3-c 搬移时漏了依赖）。
 */
import fs from 'node:fs';
import { IS_MAC, IS_WIN } from './config.mjs';

/**
 * v0.9.3（D5 / 外部反馈 2026-09-24）：子进程基础环境 —— 三处 spawn 统一走它。
 *
 * 旧版只注入 PYTHONIOENCODING。Blender 的 Python stdout 被重定向（pipe）时是**块缓冲**，
 * 于是 job 的 stdout.log / stderr.log 在任务运行期间**全程 0 字节**，只有进程退出才落盘
 * （Lead 实测：20 分钟渲染期间两个日志一直 0 字节；engine：「399 s 那次中间没有任何阶段信息，6 分钟纯黑盒」）。
 * PYTHONUNBUFFERED=1 让 print 立即写出 → stage 心跳与增量日志才有意义。
 */
function baseChildEnv(extra) {
  return Object.assign({}, process.env, { PYTHONIOENCODING: 'utf-8', PYTHONUNBUFFERED: '1' }, extra || {});
}


/** WSLENV 声明（v0.9.1 实测坑：WSL 侧 spawn Windows 进程时 env 不会自动跨界）—— headless 与 job 共用
 *  macOS / Windows 上父子进程同 OS，env 天然继承，不需要这一步。 */
function withWslEnv(childEnv, extraEnv) {
  if (IS_MAC || IS_WIN) return childEnv;   // 同 OS，env 直接继承；WSLENV 无意义且会污染子进程环境
  try {
    const pass = Object.keys(childEnv).filter((k) => k.indexOf('DSH_') === 0
      || (extraEnv && Object.prototype.hasOwnProperty.call(extraEnv, k)));
    const spec = pass.map((k) => k + '/w').join(':');
    childEnv.WSLENV = childEnv.WSLENV ? (childEnv.WSLENV + ':' + spec) : spec;
  } catch (e) { /* WSLENV 拼不出来就算了，脚本内注入是主路径 */ }
  return childEnv;
}


/** 把"workDir 不共享、脚本改落别处"变成一条**追加在末尾**的 pathWarning（形状与 pathAudit 一致：带 hint）。
 *  为什么追加而不是插到最前：pathAudit 的既有断言（如 acceptance_v093）看的是 pathWarnings[0]，别抢它的位置。 */
function workdirStagingWarning(staging) {
  if (!staging || !staging.substituted) return null;
  return { code: 'WORKDIR_NOT_SHARED', value: staging.requestedWhy, evidence: staging.note,
           hint: 'workDir（' + staging.requested + '）在 Windows 侧不保证可见 → 无头脚本会落在 blender.exe 打不开的位置。'
                 + '把 DSH_BLENDER_WORKDIR / 配置里的 workDir 指到 Windows 可见目录（D:\\… 或发行版根文件系统 ~/.dsh/…）' };
}

/** 读 JSON，坏文件返回 null（engine 与 launch 共用） */
function readJsonSafe(p) {
  try { return JSON.parse(fs.readFileSync(p, 'utf8')); } catch (e) { return null; }
}

export { baseChildEnv, withWslEnv, readJsonSafe, workdirStagingWarning };
