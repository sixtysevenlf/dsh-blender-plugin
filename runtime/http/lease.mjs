// 写权限租约（T7）：多 agent / 多会话同时驱动一个 Blender 时，靠它避免互相踩。
// 语义与拆分前逐字一致：不持租约不拦；显式持租约后别人的写操作 409；TTL 过期自动失效。
import { CFG } from "../config.mjs";
import { PLAN_READ_ONLY_OPS } from "../catalog.mjs";
import { stats } from "./config.mjs";

/**
 * 写权限租约：多 agent / 多会话同时驱动一个 Blender 时，靠它避免互相踩。
 * 语义：不持有租约时不拦（单机单 agent 完全无感）；一旦有人显式拿到租约，别人的写操作会被 409 挡住。
 * 过期即自动失效（TTL，默认 10 min，每次写操作自动续期）。
 */
const LEASE = { holder: null, token: null, acquiredAt: null, expiresAt: null, ttlMs: CFG.leaseTtlMs, renewals: 0,
               // v2.6（本轮反馈 #7）：写作用域的**第二维** —— 不只有"谁在写"，还有"写到哪"。
               scope: null };
/**
 * v0.9.4：租约持有者**是否还活着**（实测踩到：DSH 会话崩了/被关掉之后，它的租约在内存里
 * 继续生效到 TTL（默认 1 h），把别的会话的写通道整段挡死 —— 而那个会话早就不存在了）。
 * holder 约定是 `plugin-pid-<pid>`（插件前端 HOLDER，见 src/index.ts），后端与它同机 → 能直接探活。
 * 返回 true/false；**查不出来返回 null**（按"活着"处理，绝不误抢别人的租约）。
 */
/**
 * v2.6（本轮反馈 #7）：把写作用域声明规范化成 [{kind:'collection'|'prefix', value}]。
 *
 * 现场：子代理只有单一集合的写权限，于是灯（LGT-*）被迫塞进资产集合，装配时再逐个人工搬 ——
 * 「写作用域」只按集合一刀切，和「对象名前缀」这个真正的归属信号脱节。
 * 这里允许两种维度同时声明：
 *   collection:COL_Lights   或裸 COL_Lights        → 集合维度
 *   prefix:LGT-             或裸 LGT-* / LGT-      → 对象名前缀维度
 * 语义是**声明式**（advisory）：不拦写入，但会出现在 /who、/lease 与 409 回执里，
 * 让"谁该写哪"在协议层可见；强制归属由装配层的 K.route_collections() 一次归位。
 */
function normalizeScope(scope) {
  const raw = Array.isArray(scope) ? scope : String(scope == null ? '' : scope).split(',');
  const out = [];
  for (const item of raw) {
    const s0 = String(item || '').trim();
    if (!s0) continue;
    if (/^collection:/i.test(s0)) { out.push({ kind: 'collection', value: s0.slice(11).trim() }); continue; }
    if (/^prefix:/i.test(s0)) { out.push({ kind: 'prefix', value: s0.slice(7).trim() }); continue; }
    if (s0.endsWith('*') || s0.includes('-')) { out.push({ kind: 'prefix', value: s0.replace(/\*$/, '') }); continue; }
    out.push({ kind: 'collection', value: s0 });
  }
  return out.length ? out : null;
}
function holderAlive(holder) {
  const m = /^plugin-pid-(\d+)$/.exec(String(holder || ''));
  if (!m) return null;
  const pid = Number(m[1]);
  if (!Number.isInteger(pid) || pid < 2 || pid > 4194304) return null;
  try { process.kill(pid, 0); return true; }
  catch (e) { return (e && e.code === 'EPERM') ? true : false; }   // EPERM：存在但无权限 → 活着
}
function leaseView() {
  const now = Date.now();
  const active = !!(LEASE.holder && LEASE.expiresAt && LEASE.expiresAt > now);
  return {
    active: active,
    holder: active ? LEASE.holder : null,
    expiresInMs: active ? LEASE.expiresAt - now : 0,
    ttlMs: LEASE.ttlMs,
    since: LEASE.acquiredAt,
    renewals: LEASE.renewals,
    holderAlive: active ? holderAlive(LEASE.holder) : null,   // null = 无法判断 / 非本机约定命名
    scope: active ? LEASE.scope : null,                       // v2.6：写作用域（collection / prefix 两维）
    staleReclaimed: stats.staleLeasesReclaimed || 0,
  };
}
/** 死会话的租约 → 立刻回收（返回被回收的 holder；没得回收返回 null）。不改租约语义，只补一个"持有者不存在"的出口。 */
function releaseStaleLease(why) {
  const v = leaseView();
  if (!v.active || v.holderAlive !== false) return null;
  const dead = v.holder;
  LEASE.holder = null; LEASE.token = null; LEASE.expiresAt = null; LEASE.acquiredAt = null; LEASE.renewals = 0;
  stats.staleLeasesReclaimed = (stats.staleLeasesReclaimed || 0) + 1;
  stats.lastLeaseNote = '已回收死会话的写租约（holder=' + dead + '，' + String(why || '') + '）';
  return dead;
}
function leaseAcquire(holder, ttlMs, force, scope) {
  const now = Date.now();
  const h = String(holder || 'anonymous');
  const v = leaseView();
  if (v.active && v.holder !== h && !force) {
    return { denied: true, holder: v.holder, expiresInMs: v.expiresInMs };
  }
  if (!v.active || v.holder !== h) {
    LEASE.token = 'lk_' + Math.random().toString(16).slice(2, 10);
    LEASE.acquiredAt = now;
    LEASE.renewals = 0;
  } else {
    LEASE.renewals++;
  }
  LEASE.holder = h;
  if (ttlMs) LEASE.ttlMs = Math.max(5000, Math.min(86400000, Number(ttlMs) || LEASE.ttlMs));
  if (scope !== undefined) LEASE.scope = normalizeScope(scope);   // v2.6：声明式写作用域（省略则保留）
  LEASE.expiresAt = now + LEASE.ttlMs;
  return { granted: true, lease: leaseView() };
}
/** 只读 op：即便别人持有租约也放行（否则连"看看现状"都会被挡） */
const READ_ONLY_OPS = { '/perf': ['status', 'help'], '/loop': ['status', 'board', 'help'], '/opt': ['opt_analyze', 'analyze', 'help'],
  '/plan': PLAN_READ_ONLY_OPS,'/worker': ['status', 'list', 'see', 'ping'],
  '/txn': ['list', 'marks', 'help', 'edit_status'],
  '/preset': ['list', 'get', 'help'],
  // v0.9.3（D6）：wait 只是"阻塞读"，kill 只作用于作业自己的子进程（不碰 live 场景）——
  // 它们不该被别的会话的写租约挡住（否则"我的作业我收不回来"）。
  '/job': ['status', 'collect', 'list', 'wait', 'kill'] };
function isReadOnly(path, op) {
  const list = READ_ONLY_OPS[path];
  return !!(list && list.indexOf(String(op)) >= 0);
}

/** 请求带上 holder/force 时的门禁；返回 null = 放行 */
function leaseGate(payload) {
  const h = String((payload && (payload.holder || payload.lease)) || '');
  const force = !!(payload && payload.force);
  // v0.9.4：先回收"持有者进程已经不存在"的租约（否则会话崩了会把写通道挡到 TTL 结束）
  const reclaimed = releaseStaleLease('写请求到达时按 plugin-pid-<pid> 探活发现进程不存在');
  const v = leaseView();
  if (v.active && v.holder !== h && !force) {
    return { ok: false, error: 'leased', holder: v.holder, expiresInMs: v.expiresInMs, holderAlive: v.holderAlive, holderScope: v.scope || null,
      hint: '另一个会话正在驱动这个 Blender。要么等它（剩余 ' + Math.round(v.expiresInMs / 1000) + 's），要么 body 加 force:true 抢占，或 blender_viewport op=lease force=true' };
  }
  // 放行时顺手续期：holder 匹配或无人持有时自动接管
  if (!h) return null;  // 没报 holder 的老客户端：无租约时按旧行为放行
  const r = leaseAcquire(h, null, force);
  return r.denied ? { ok: false, error: 'leased', holder: r.holder, expiresInMs: r.expiresInMs, hint: '租约已被占用' } : null;
}
export { LEASE, normalizeScope, holderAlive, leaseView, releaseStaleLease, leaseAcquire, READ_ONLY_OPS, isReadOnly, leaseGate };
