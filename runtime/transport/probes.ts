// addon 传输层：TCP 探活、现场诊断、协议信息与 AddonClient（T6）
// @imports
import { ADDRESS } from "../paths.js";
import net from "node:net";
import { resolveProtocol } from "../addon-protocol.js";
import { CFG } from "../config.js";
import { errorMessage } from "../core/json.js";
import type { AddonProtocol, ProtocolDecode, ProtocolInfo } from "../addon-protocol.js";


export async function tcpProbe(host: string = ADDRESS.host, port: number = ADDRESS.port, timeoutMs = 2000): Promise<boolean> {
  return new Promise<boolean>((resolve) => {
    const s = net.connect({ host, port });
    let settled = false;
    const done = (ok: boolean): void => { if (settled) return; settled = true; try { s.destroy(); } catch (e) {} resolve(ok); };
    const t = setTimeout(() => done(false), timeoutMs);
    s.once("connect", () => { clearTimeout(t); done(true); });
    s.once("error", () => { clearTimeout(t); done(false); });
  });
}

export async function freshPing(host: string = ADDRESS.host, port: number = ADDRESS.port, timeoutMs = 2500): Promise<number> {
  const t0 = Date.now();
  const { proto } = await resolveProtocol({ host, port, prefer: 'flat', timeoutMs: Math.min(timeoutMs, 1500) });
  const plan = proto.plan("ping", {});
  return new Promise<number>((resolve, reject) => {
    const s = net.connect({ host, port });
    let buf = "";
    const t = setTimeout(() => { try { s.destroy(); } catch (e) {} reject(new Error("ping timeout")); }, timeoutMs);
    s.once("error", (e) => { clearTimeout(t); try { s.destroy(); } catch (x) {} reject(e); });
    s.on("data", (d: Buffer) => {
      buf += d.toString("utf8");
      let r: ProtocolDecode | null = null;
      try { r = proto.decode(buf.trim()); } catch (e) { return; }   // 没攒够一整条 → 继续
      clearTimeout(t);
      try { s.destroy(); } catch (e) {}
      if (!r.ok) reject(new Error((r as { message?: string }).message || "ping error"));
      else resolve(Date.now() - t0);
    });
    s.once("connect", () => s.write(proto.encode(plan.wire)));
  });
}

export interface Diagnosis {
  kind: string;
  tcp: boolean;
  ping: boolean | null;
  ping_ms?: number;
  summary: string;
  fix: string;
  hint: string;
  error?: string;
}
export async function diagnose(): Promise<Diagnosis> {
  const proto = (await resolveProtocol({ host: ADDRESS.host, port: ADDRESS.port, prefer: 'flat' }).catch(() => null));
  const panel = (proto && proto.proto.panelHint) || "「DSH Blender Bridge」面板 → Connect";
  const tcp = await tcpProbe();
  if (!tcp) {
    return { kind: "blender-unreachable", tcp: false, ping: null,
      summary: "9876 端口没人监听 —— Blender 没在跑，或 addon 没连上",
      fix: "**agent 走这条路：blender_viewport(op=\"launch\") 一键拉起 GUI Blender 并自动 Connect addon**（v0.9.4 起，不必有人点面板）；"
        + "人工流程是：启动 Blender → 3D 视图按 N → " + panel + "（服务监听 127.0.0.1:" + String(CFG.addonPort) + "）",
      hint: "TCP " + CFG.addonHost + ":" + String(CFG.addonPort) + " 不通：Blender 没在运行，或 addon 面板没连上。"
        + "一键修：blender_viewport(op=\"launch\")（GUI + 自动 Connect，幂等）；人工修：N → " + panel };
  }
  try {
    const ms = await freshPing();
    return { kind: "main-thread-busy", tcp: true, ping: true, ping_ms: ms,
      summary: "addon 活着（ping 通），但 bpy 命令被主线程挡住 —— 正在渲染 / 模态操作 / 跑重活",
      fix: "等它空下来再调用；长渲染改走 blender_rt_headless（独立进程，不占 GUI 通道）",
      hint: "addon 活着、ping 通，但 bpy 命令超时 → Blender 主线程被占（渲染 / 模态操作 / 重活）。等它空下来，或把长渲染改用 blender_rt_headless（独立进程，不占 GUI 通道）" };
  } catch (e) {
    // TCP 通但没有扁平 JSON 回包：addon 线程卡住，或对面根本不是本仓 addon。
    return { kind: "addon-thread-stuck", tcp: true, ping: false, error: errorMessage(e),
      summary: "端口通但 ping 无响应 —— addon 客户端线程卡住，或当前 addon 不是本仓「DSH Blender Bridge」",
      fix: "不要连发；等它结束（agent 模式下可 blender_rt_loop op=stop 急停），仍无响应再重启 Blender。"
        + "若 Blender 里装的是旧单文件 addon 或旧「MCP for Blender」，均已不支持：请换成 addon/dsh_blender_bridge.zip（v1.11）",
      hint: "TCP 通但 ping 无响应 → addon 线程卡住，或装的不是本仓 addon；只支持 DSH Blender Bridge v1.11+（zip 包）" };
  }
}

/** 通道运行时实际使用的协议（给 /doctor、/who、工具输出用） */
export interface ProtocolInfoView {
  id: string | null;
  label?: string;
  from?: string;
  detected?: unknown;
  configured: string;
  error?: string;
}
export async function protocolInfo(): Promise<ProtocolInfoView> {
  try {
    const r = await resolveProtocol({ host: ADDRESS.host, port: ADDRESS.port, prefer: 'flat' });
    return { id: r.proto.id, label: r.proto.label, from: r.from, detected: r.detected || null, configured: 'flat' };
  } catch (e) {
    return { id: null, error: errorMessage(e), configured: 'flat' };
  }
}

interface PendingRequest {
  resolve: (v: unknown) => void;
  reject: (e: Error) => void;
  timer: NodeJS.Timeout;
  post?: (data: unknown) => unknown;
  proto: AddonProtocol;
}
export class AddonClient {
  addr: { host: string; port: number };
  sock: net.Socket | null;
  queue: Promise<unknown>;
  buf: string;
  pending: PendingRequest | null;
  proto: AddonProtocol | null;          // 首次使用时解析（显式配置 → 直取；auto → 探测一次并缓存）
  protocolInfo: ProtocolInfo | null;
  _seq: number;
  constructor(addr: { host: string; port: number } = ADDRESS) {
    this.addr = addr;
    this.sock = null;
    this.queue = Promise.resolve();
    this.buf = '';
    this.pending = null;
    this.proto = null;
    this.protocolInfo = null;
    this._seq = 0;
  }
  async protocol(): Promise<ProtocolInfo> {
    if (!this.proto) {
      const info = await resolveProtocol({ host: this.addr.host, port: this.addr.port, prefer: 'flat' });
      this.protocolInfo = info;
      this.proto = info.proto;
      return info;
    }
    return this.protocolInfo as ProtocolInfo;
  }
  connect(timeoutMs = 4000): Promise<boolean> {
    return new Promise<boolean>((resolve, reject) => {
      const s = net.connect({ host: this.addr.host, port: this.addr.port });
      const t = setTimeout(() => { s.destroy(); reject(new Error('connect timeout')); }, timeoutMs);
      s.once('connect', () => { clearTimeout(t); this.sock = s; resolve(true); });
      s.once('error', (e) => { clearTimeout(t); this.sock = null; reject(e); });
      s.on('data', (d: Buffer) => this._onData(d));
      s.on('close', () => { this.sock = null; if (this.pending) { this.pending.reject(new Error('socket closed')); this.pending = null; } });
    });
  }
  _onData(d: Buffer): void {
    this.buf += d.toString('utf8');
    if (!this.pending) return;
    const proto = this.pending.proto;
    let r: ProtocolDecode | null = null;
    try { r = proto.decode(this.buf.trim()); } catch (e) { return; }   // 未收全 → 继续攒
    this.buf = '';
    const p = this.pending; this.pending = null;
    clearTimeout(p.timer);
    if (!r.ok) { p.reject(new Error((r as { message?: string }).message)); return; }
    try { p.resolve(p.post ? p.post(r.data) : r.data); }
    catch (err) { p.reject(err instanceof Error ? err : new Error(errorMessage(err))); }
  }
  async ensure(): Promise<void> { if (!this.sock) await this.connect(); }
  send(type: string, params: Record<string, unknown> = {}, timeoutMs = 120000, _opts?: { noMark?: boolean }): Promise<unknown> {
    const run = async () => {
      const info = await this.protocol();
      const proto = info.proto;
      const plan = proto.plan(type, params);          // 可能抛：未知命令 / 缺参数
      if (plan.local) return plan.local();            // 本地桩（遥测 / 集成 status）不占 socket
      await this.ensure();
      return new Promise<unknown>((resolve, reject) => {
        const timer = setTimeout(() => { this.pending = null; this.buf = ''; reject(new Error('addon timeout: ' + type)); }, timeoutMs);
        this.pending = { resolve: resolve, reject: reject, timer: timer, post: plan.post, proto: proto };
        this.sock?.write(proto.encode(plan.wire));
      });
    };
    const p = this.queue.then(run, run);
    this.queue = p.catch(() => {});
    return p;
  }
  close(): void { if (this.sock) { this.sock.end(); this.sock = null; } }
}

