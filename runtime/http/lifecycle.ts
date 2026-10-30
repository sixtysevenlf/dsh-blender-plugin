// HTTP 生命周期（T7）：端口占用自愈、优雅退出、启动日志。
// 与拆分前一致：EADDRINUSE 先探活（能应答就让位），死进程 SIGTERM→600ms→SIGKILL→300ms 重试，最多 2 次。
import type { Server } from "node:http";
import fs from "node:fs";
import path from "node:path";
import { CFG } from "../config.js";
import { errorMessage } from "../core/json.js";

// 永远排不上队、看护每 15 s 重生一个实例却撞 EADDRINUSE 退出 ⇒ 无限循环，只能人工 kill -9。
// 两层兜底：① 信号处理不再可能被"吞"（强制退出定时器）；② EADDRINUSE 时先判定占用者是死是活，
// 死的直接 SIGKILL 夺回端口并重试 listen（不依赖人工、不依赖看护）。
const sleepMs = (ms: number): Promise<void> => new Promise((r) => setTimeout(r, ms));

/** 按 /proc 扫出"占着本端口的同族后端"（供 SIGKILL 用；无 /proc 的平台返回 []）。
 *  ★ 必须按 **argv token** 精确匹配，绝不能拿整条 cmdline 做子串匹配：
 *    实测踩过（2026-09-29）—— 调用方的 `bash -c "... node runtime/server.mjs --port 9899 ..."`
 *    命令行里同样含这两个串，子串匹配会把调用方自己的 shell 一起 SIGKILL 掉（自伤）。 */
function samePortHolders(port: unknown): number[] {
  const out: number[] = [];
  const wantPort = String(port);
  try {
    for (const d of fs.readdirSync('/proc')) {
      if (!/^[0-9]+$/.test(d) || Number(d) === process.pid) continue;
      try {
        const toks: string[] = fs.readFileSync('/proc/' + d + '/cmdline', 'utf8').split('\u0000').filter(Boolean);
        let isServer = false, portOk = false, isShell = false;
        for (let i = 0; i < toks.length; i++) {
          if (i === 0 && /(^|\/)(bash|sh|zsh|dash|fish|bwrap)$/.test(toks[i])) isShell = true;
          if (i >= 1 && /(^|\/)server\.mjs$/.test(toks[i])) isServer = true;   // 必须是独立 argv 且以 server.mjs 结尾
          if (toks[i] === '--port' && toks[i + 1] === wantPort) portOk = true;
        }
        if (isServer && portOk && !isShell) out.push(Number(d));
      } catch (e) { /* 进程刚好退出 */ }
    }
  } catch (e) { /* 无 /proc（macOS/Windows）→ 不夺，交给看护报错 */ }
  return out;
}

/** 占用者到底"死没死"：还能答 /health 就是活的（只是忙），那就绝不夺。 */
async function holderResponsive(port: unknown, ms = 1200): Promise<boolean> {
  const ac = new AbortController();
  const t = setTimeout(() => ac.abort(), ms);
  try { return (await fetch('http://127.0.0.1:' + String(port) + '/health', { signal: ac.signal })).ok; }
  catch (e) { return false; }
  finally { clearTimeout(t); }
}

function killHolders(port: unknown, signal: NodeJS.Signals | number): number[] {
  const killed: number[] = [];
  for (const pid of samePortHolders(port)) {
    try { process.kill(pid, signal); killed.push(pid); } catch (e) { /* 已退出 */ }
  }
  return killed;
}


export interface StartServerOpts {
  server: Server;
  engine: { stop: () => unknown };
  port: number;
  host: string;
  routes: string[];
}

export function startServer({ server, engine, port, host, routes }: StartServerOpts): void {
  const onListening = () => console.log('[blender-rt] http://' + host + ':' + String(port) + '/  (routes: ' + routes.join(' ') + ')');
  let reclaimTries = 0;
  server.on('error', async (e: NodeJS.ErrnoException) => {
    const code = e && e.code;
    if (code === 'EADDRINUSE' && reclaimTries < 2) {
      reclaimTries += 1;
      if (await holderResponsive(port)) {
        console.error('[dsh-blender-backend] 端口 ' + String(port) + ' 被**能应答**的后端占着 —— 不夺，本次退出（交给看护/op=start）。');
        process.exit(0);
      }
      const k1 = killHolders(port, 'SIGTERM');
      await sleepMs(600);
      const k2 = killHolders(port, 'SIGKILL');
      console.error('[dsh-blender-backend] 僵死后端占着 ' + String(port) + '：SIGTERM=[' + k1.join(',') + '] SIGKILL=[' + k2.join(',') + '] → 重试 listen。');
      await sleepMs(300);
      try { server.listen(port, host, onListening); return; } catch (e2) { /* 落到下面的通用报错 */ }
    }
    const msg = code === 'EADDRINUSE'
      ? ('端口 ' + port + ' 已被占用（EADDRINUSE）且自动夺回失败：多半是上一次的后端僵死。'
         + '处置：blender_viewport op=restart，或 kill -9 掉 /proc 里 cmdline 含 "server.mjs --port ' + String(port) + '" 的进程。')
      : ('监听失败：' + errorMessage(e));
    console.error('[dsh-blender-backend] ' + msg);
    try { fs.writeFileSync(path.join(CFG.workDirWsl, 'backend-listen-error.txt'), msg, 'utf8'); } catch (e2) { /* workDir 都写不了就算了 */ }
    process.exit(1);
  });
  server.listen(port, host, onListening);
  const shutdown = () => {
    try { engine.stop(); } catch (e) { /* 忽略 */ }
    try { server.close(); } catch (e) { /* 忽略 */ }
    setTimeout(() => process.exit(0), 400);
  };
  process.on('SIGINT', shutdown);
  process.on('SIGTERM', shutdown);
}
