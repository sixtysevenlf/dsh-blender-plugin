// Gateway 命令面探针（3.1）：只连 Gateway HTTP，不直连 addon 端口。
// 用法：node lib/runtime/_probe_tools.js
import { CFG } from './config.js'
import { GATE_FLAGS } from './core/flags.js'
import { errorMessage } from './core/json.js'

const base = 'http://127.0.0.1:' + String(CFG.gatewayPort)
const out: Record<string, { ok: boolean; ms: number; r?: string; err?: string }> = {}

async function probe(
  name: string,
  path: string,
  init?: RequestInit,
): Promise<Record<string, unknown> | null> {
  const t0 = Date.now()
  try {
    const response = await fetch(base + path, init)
    const text = await response.text()
    let parsed: Record<string, unknown> = {}
    try {
      parsed = JSON.parse(text) as Record<string, unknown>
    } catch {
      parsed = { text: text.slice(0, 300) }
    }
    out[name] = {
      ok: response.ok && parsed.ok !== false,
      ms: Date.now() - t0,
      r: JSON.stringify(parsed).slice(0, 300),
    }
    return parsed
  } catch (e) {
    out[name] = { ok: false, ms: Date.now() - t0, err: errorMessage(e).slice(0, 160) }
    return null
  }
}

await probe('health', '/health')
const status = await probe('status', '/status')
await probe('doctor', '/doctor')
const commands = await probe('commands', '/commands')
const code = [
  'import bpy, json',
  's = bpy.context.scene',
  'names = ' + JSON.stringify(GATE_FLAGS),
  'out = {}',
  'for n in names:',
  '    out[n] = bool(getattr(s, n, None))',
  'print(json.dumps(out))',
].join('\n')
await probe('act', '/act', {
  method: 'POST',
  headers: { 'content-type': 'application/json' },
  body: JSON.stringify({ code }),
})

const readiness = (status?.readiness || {}) as Record<string, unknown>
const addon = (readiness.addon || {}) as Record<string, unknown>
const commandView = (commands || {}) as Record<string, unknown>
console.log(
  JSON.stringify(
    {
      gateway: base,
      ready_instances: addon.ready_instances ?? null,
      commands: {
        total: commandView.total ?? null,
        available: Array.isArray(commandView.available) ? commandView.available.length : null,
        disabled: Array.isArray(commandView.disabled) ? commandView.disabled.length : null,
      },
      probes: out,
    },
    null,
    1,
  ),
)
