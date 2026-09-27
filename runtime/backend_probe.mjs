/**
 * 后端探活与启动决策（S7）—— start 不再信陈旧句柄/pidfile：**先真探端口**。
 *
 * 现场 bug：`op=start` 只看子进程句柄（childAlive），句柄还在但端口根本没监听时，
 * 它会回"已在跑（already-spawned）"，于是用户拿到一个**打不通的后端**。
 * 这里把"能不能连通"变成唯一事实来源；句柄只用来判断"要不要先清理"。
 */
import net from "node:net";

/** TCP 真探：连上即"在监听"。比 HTTP 探活更轻，不受宿主 fetch 超时影响。 */
export function probeTcp(host, port, timeoutMs = 500) {
  return new Promise((resolve) => {
    const sock = new net.Socket();
    let done = false;
    const fin = (v) => {
      if (done) return;
      done = true;
      try { sock.destroy(); } catch (e) { /* ignore */ }
      resolve(v);
    };
    sock.setTimeout(timeoutMs);
    sock.once("connect", () => fin(true));
    sock.once("timeout", () => fin(false));
    sock.once("error", () => fin(false));
    try { sock.connect(port, host); } catch (e) { fin(false); }
  });
}

/** HTTP 探活：能拿到 2xx 才算"后端真的在服务"（端口通但后端没起来 ⇒ false）。 */
export async function probeHttp(url, timeoutMs = 1200) {
  const ac = new AbortController();
  const timer = setTimeout(() => ac.abort(), timeoutMs);
  try {
    const r = await fetch(url, { signal: ac.signal });
    return !!r && r.ok;
  } catch (e) {
    return false;
  } finally {
    clearTimeout(timer);
  }
}

/**
 * 启动决策表（纯函数，便于单测）：
 *   portUp                → "already-running"（别的会话拉起的也算在跑，绝不重复拉）
 *   !portUp && childAlive → "stale-handle"（句柄陈了：端口没监听 ⇒ 必须先清再拉）
 *   !portUp && !childAlive→ "spawn"
 */
export function decideStart({ portUp, childAlive }) {
  if (portUp) return "already-running";
  if (childAlive) return "stale-handle";
  return "spawn";
}

/** 轮询等到能服务为止（op=start 用它给真结论，而不是"睡固定时间再探一次"）。 */
export async function waitHttp(url, timeoutMs = 10000, intervalMs = 300) {
  const t0 = Date.now();
  for (;;) {
    if (await probeHttp(url, Math.min(1500, Math.max(300, timeoutMs)))) return true;
    if (Date.now() - t0 >= timeoutMs) return false;
    await new Promise((r) => setTimeout(r, intervalMs));
  }
}
