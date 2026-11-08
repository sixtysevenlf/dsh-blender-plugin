#!/usr/bin/env node
/**
 * 3.6 failed-first-call budget sampler.
 *
 * Samples the real Gateway over loopback for the no-exe / half-open /
 * commands-disconnected / timeline failure classes. Each sample is a fresh DSH
 * session (new x-dsh-session-id) so the measurement is a cold first call, not a
 * warm admin call. Steam detection and the full gates are not HTTP failure
 * classes; they are covered by tests/unit/js/blender-detect.test.ts and the PR
 * CI gate commands.
 *
 * Usage (requires `npm run build`):
 *   node tests/e2e/first-call-budget.mjs
 * Writes local-workspace/3.6-first-call-budget.json when the directory exists.
 */
import { spawn } from 'node:child_process'
import fs from 'node:fs'
import net from 'node:net'
import os from 'node:os'
import path from 'node:path'
import { startFakeWsAddon } from '../helpers/fake-ws-addon.mjs'

const SAMPLES = 20
const REPO = path.resolve(import.meta.dirname, '..', '..')
const OUT_DIR = path.join(REPO, 'local-workspace')

function freePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer()
    server.once('error', reject)
    server.listen(0, '127.0.0.1', () => {
      const addr = server.address()
      server.close(() => resolve(addr.port))
    })
  })
}

function p95(values) {
  const sorted = [...values].sort((a, b) => a - b)
  return sorted[Math.min(sorted.length - 1, Math.ceil(sorted.length * 0.95) - 1)]
}

async function main() {
  const port = await freePort()
  const workDir = fs.mkdtempSync(path.join(os.tmpdir(), 'dsh-budget-'))
  const env = {
    ...process.env,
    DSH_BLENDER_WORKDIR: workDir,
    DSH_BLENDER_GATEWAY_PORT: String(port),
    DSH_BLENDER_HTTP_PORT: String(port),
  }
  env.DSH_BLENDER_EXE = path.join(
    os.tmpdir(),
    'dsh-missing-blender-' + String(process.pid) + '.exe',
  )
  const child = spawn(
    process.execPath,
    [path.join(REPO, 'lib/runtime/gateway.js'), '--port', String(port)],
    {
      cwd: REPO,
      env,
      stdio: ['ignore', 'pipe', 'pipe'],
    },
  )
  let log = ''
  child.stdout.on('data', (d) => (log += d))
  child.stderr.on('data', (d) => (log += d))
  const base = 'http://127.0.0.1:' + port
  const deadline = Date.now() + 15000
  let up = false
  while (Date.now() < deadline) {
    try {
      const r = await fetch(base + '/health')
      const j = await r.json()
      if (j.ok === true) {
        up = true
        break
      }
    } catch {}
    await new Promise((r) => setTimeout(r, 100))
  }
  if (!up) throw new Error('gateway did not start: ' + log.slice(-1000))

  const get = async (route, session) => {
    const r = await fetch(base + route, {
      headers: {
        'x-dsh-host-id': 'budget-host',
        'x-dsh-session-id': session,
        'x-dsh-agent-id': 'agent-main',
        'x-dsh-agent-role': 'main',
      },
    })
    return { status: r.status, body: await r.json() }
  }
  const post = async (route, body, session) => {
    const r = await fetch(base + route, {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        'x-dsh-host-id': 'budget-host',
        'x-dsh-session-id': session,
        'x-dsh-agent-id': 'agent-main',
        'x-dsh-agent-role': 'main',
      },
      body: JSON.stringify(body),
    })
    return { status: r.status, body: await r.json() }
  }
  const timed = async (fn) => {
    const t = performance.now()
    const out = await fn()
    return { ms: performance.now() - t, out }
  }

  const result = {}

  // no-exe: no Blender executable -> spawn stage failure, before any observable GUI.
  {
    const ms = []
    let last = null
    for (let i = 0; i < SAMPLES; i++) {
      const { ms: dt, out } = await timed(() =>
        post('/worker', { op: 'start', isolated: true }, 'a1-' + i),
      )
      ms.push(dt)
      last = out
    }
    result.no_exe = {
      samplesMs: ms.map((n) => Math.round(n)),
      p95Ms: Math.round(p95(ms)),
      budgetMs: 1000,
      ok: p95(ms) <= 1000 && JSON.stringify(last).includes('blender_exe_missing'),
      last: JSON.stringify(last).slice(0, 500),
    }
  }

  // half-open: half-open addon -> extension/bpy timeout, no nested retry.
  let addon = await startFakeWsAddon({ gatewayPort: port, behavior: () => ({ action: 'hang' }) })
  await addon.ready
  {
    const ms = []
    let last = null
    for (let i = 0; i < SAMPLES; i++) {
      const { ms: dt, out } = await timed(() => get('/status', 'a3-' + i))
      ms.push(dt)
      last = out
    }
    result.half_open_addon = {
      samplesMs: ms.map((n) => Math.round(n)),
      p95Ms: Math.round(p95(ms)),
      budgetMs: 3000,
      ok: p95(ms) <= 3000 && String(last.body.addonError || '').includes('timeout'),
      last: { status: last.status, addonError: last.body.addonError },
    }
  }
  await addon.close()

  // commands-disconnected: commands with no addon -> transport stage, bounded.
  {
    const ms = []
    let last = null
    for (let i = 0; i < SAMPLES; i++) {
      const { ms: dt, out } = await timed(() => get('/commands', 'a4-' + i))
      ms.push(dt)
      last = out
    }
    result.commands_disconnected = {
      samplesMs: ms.map((n) => Math.round(n)),
      p95Ms: Math.round(p95(ms)),
      budgetMs: 3000,
      ok: p95(ms) <= 3000 && last.status >= 200 && last.status < 600,
      lastStatus: last.status,
      last: JSON.stringify(last.body).slice(0, 400),
    }
  }

  // timeline: five-step timeline with Gateway-only: status + doctor agree, bounded.
  {
    const ms = []
    let statusReady = null
    let doctorKind = null
    for (let i = 0; i < SAMPLES; i++) {
      const { ms: dt, out } = await timed(async () => {
        const s = await get('/status', 'a6-' + i)
        const d = await get('/doctor', 'a6-' + i)
        return { s, d }
      })
      ms.push(dt)
      statusReady = out.s.body.readiness && out.s.body.readiness.ready
      doctorKind = out.d.body.addon && out.d.body.addon.kind
    }
    result.timeline = {
      samplesMs: ms.map((n) => Math.round(n)),
      p95Ms: Math.round(p95(ms)),
      budgetMs: 3000,
      ok: p95(ms) <= 3000 && statusReady === false && doctorKind === 'addon-unreachable',
      statusReady,
      doctorKind,
    }
  }

  // 合并所有失败类的冷样本，按设计的合并样本口径计算总 p95。
  const combined = Object.values(result).flatMap((v) => v.samplesMs)
  const combinedP95Ms = Math.round(p95(combined))
  child.kill('SIGTERM')
  try {
    fs.rmSync(workDir, { recursive: true, force: true })
  } catch {}
  if (fs.existsSync(OUT_DIR)) {
    const stamp = new Date().toISOString()
    fs.writeFileSync(
      path.join(OUT_DIR, '3.6-first-call-budget.json'),
      JSON.stringify({ stamp, samples: SAMPLES, combinedP95Ms, result }, null, 2) + '\n',
    )
  }
  console.log(JSON.stringify({ combinedP95Ms, result }, null, 2))
  const failed = Object.entries(result).filter(([, v]) => !v.ok)
  if (failed.length || combinedP95Ms > 3000) {
    console.error(
      'BUDGET FAILED: ' +
        (failed.length ? failed.map(([k]) => k).join(', ') : 'combined p95 ' + combinedP95Ms),
    )
    process.exit(1)
  }
}

main().catch((e) => {
  console.error(e)
  process.exit(1)
})
