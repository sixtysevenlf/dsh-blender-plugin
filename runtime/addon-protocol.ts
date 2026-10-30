/**
 * addon 协议 —— 只支持本仓 `DSH Blender Bridge` 的**扁平协议**（v2.3 起）。
 *
 * 历史（已删除）：v2.0 之前还支持上游 `MCP for Blender`（模块 `blender_mcp`）与
 * harveyxiacn `blender_mcp_addon`（category-action 协议）。v2.3 起选中/集合/activity
 * 都依赖本仓 addon v1.8，继续兼容旧 addon 只会让用户拿到"模型工具能用、新功能永远不可用"
 * 的半残状态，因此**旧 addon 支持与双名回退全部删除**；用户必须使用本仓 addon。
 *
 * 协议形状（扁平 / 无分帧）：
 *   请求  {"type":"get_scene_info","params":{}}
 *   应答  {"status":"success","result":{…}} / {"status":"error","message":"…"}
 */

import net from 'node:net';

/** addon 请求的线上形态（扁平协议）。 */
export interface AddonWire {
  type: string;
  params: Record<string, unknown>;
}
/** plan() 的产物：wire 必填；local/post 是协议可选钩子（扁平协议不用）。 */
export interface ProtocolPlan {
  wire: AddonWire;
  local?: () => unknown;
  post?: (data: unknown) => unknown;
}
export type ProtocolDecode =
  | { ok: true; data: unknown }
  | { ok: false; message: string };
export interface AddonProtocol {
  id: string;
  label: string;
  framing: string;
  panelHint: string;
  plan(type: string, params?: Record<string, unknown>): ProtocolPlan;
  encode(wire: AddonWire): string;
  decode(raw: string): ProtocolDecode;
}
/** 本仓 addon 的协议描述符（唯一一种）。 */
export const PROTOCOLS: Record<string, AddonProtocol> = {
  flat: {
    id: 'flat',
    label: '直连 addon · 扁平协议（无分帧；实测 v1.7/v1.8 都通 —— 活版本看 rt_cmd get_addon_info）',
    framing: 'none',
    panelHint: '「DSH Blender Bridge」面板 → Connect',
    plan(type: string, params?: Record<string, unknown>): ProtocolPlan { return { wire: { type: type, params: params || {} } }; },
    encode(wire: AddonWire): string { return JSON.stringify(wire); },
    decode(raw: string): ProtocolDecode {
      const o = JSON.parse(raw) as Record<string, unknown>;
      if (o.status === 'error') return { ok: false, message: String(o.message || 'addon error') };
      return { ok: true, data: o.result === undefined ? {} : o.result };
    },
  },
};

export const PROTOCOL_IDS = Object.keys(PROTOCOLS);

/**
 * 探一次：发扁平 ping，有 JSON 回包即证明对面是本仓 addon。
 * @returns {Promise<{id:string, ms:number}|null>} null = addon 不在 / 端口不通
 */
export async function detectProtocol(host: string, port: number, timeoutMs = 1000): Promise<{ id: string; ms: number } | null> {
  const t0 = Date.now();
  const proto = PROTOCOLS.flat;
  return new Promise((resolve) => {
    const s = net.connect({ host: host, port: port });
    let buf = '';
    let settled = false;
    const done = (v: { id: string; ms: number } | null): void => { if (settled) return; settled = true; clearTimeout(t); try { s.destroy(); } catch (e) { /* ignore */ } resolve(v); };
    const t = setTimeout(() => done(null), timeoutMs);
    s.once('connect', () => {
      try { s.write(proto.encode(proto.plan('ping', {}).wire)); } catch (e) { done(null); }
    });
    s.once('error', () => done(null));
    s.on('data', (d) => {
      buf += d.toString('utf8');
      try { JSON.parse(buf.trim()); done({ id: 'flat', ms: Date.now() - t0 }); } catch (e) { /* 继续攒 */ }
    });
  });
}

/** 兼容旧调用点（已无缓存；保留导出避免破坏调用方）。 */
export function resetProtocolCache() { /* no-op */ }

/**
 * 得到实际要用的协议描述符。只支持扁平协议 —— `prefer` 参数仅为兼容旧调用点，直接忽略。
 * @returns {Promise<{proto:object, from:'config'}>}
 */
export interface ProtocolInfo {
  proto: AddonProtocol;
  from: string;
  detected?: unknown;
}
export async function resolveProtocol(_opts?: { host?: string; port?: number; prefer?: string; timeoutMs?: number }): Promise<ProtocolInfo> {
  return { proto: PROTOCOLS.flat, from: 'config' };
}
