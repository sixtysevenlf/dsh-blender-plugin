
// Base-vs-split HTTP parity: run the frozen base server (from git) and the new
// runtime/server.mjs against the same fake addon, then compare status, content
// type and normalized JSON for every route family.
import { startFakeAddon } from '../../../../tests/helpers/fake-addon.mjs';
import { spawn } from 'node:child_process';
import net from 'node:net';
import path from 'node:path';
import fs from 'node:fs';
import { pathToFileURL } from 'node:url';

const repo = process.env.PARITY_REPO;
const basePath = process.argv[2];
if (!repo || !basePath || !fs.existsSync(basePath)) { console.error('usage: PARITY_REPO=<repo> node parity-server.mjs <base server path>'); process.exit(2); }
const fake = await startFakeAddon();
const tmp = path.join(repo, 'runtime/server.base.tmp.mjs');
fs.copyFileSync(basePath, tmp);
const free = () => new Promise(r => { const s = net.createServer(); s.listen(0,'127.0.0.1',()=>{const p=s.address().port;s.close(()=>r(p));}); });
const pa = await free(), pb = await free();
const env = { ...process.env, DSH_BLENDER_ADDON_PORT: String(fake.port), TEMP: 'D:/DSH/tmp', TMP: 'D:/DSH/tmp', DSH_TRAJ: '0' };
function start(entry, port) { return spawn(process.execPath, [entry, '--port', String(port)], { cwd: repo, env, stdio: ['ignore','pipe','pipe'] }); }
async function waitUp(port) { for (let i=0;i<100;i++) { try { const r = await fetch('http://127.0.0.1:'+port+'/health'); if (r.ok) return; } catch {} await new Promise(r=>setTimeout(r,100)); } throw new Error('server not up on '+port); }
const procs = [start('runtime/server.base.tmp.mjs', pa), start('runtime/server.mjs', pb)];
let fails = 0;
try {
  await Promise.all([waitUp(pa), waitUp(pb)]);
  const VOLATILE = new Set(['pid','uptimeMs','startedAt','lastCmdAgeMs','lastErrorAgeMs','lastFrameMs','lastActMs','lastViewMs','lastPlanMs','lastHeadlessMs','lastSelectionMs','ms','expiresInMs','since','acquiredAt','expiresAt','lastOkAt','lastCmdAt','lastErrorAt','aiPausedAt','at','loadedAt','time','elapsedMs','heartbeat','boundPort','port','rssMB','lastLaunchMs','ping_ms','roundTripMs','mainThreadMs','lastOkAgeMs']);
  function norm(v) {
    if (Array.isArray(v)) return v.map(norm);
    if (v && typeof v === 'object') { const o={}; for (const k of Object.keys(v).sort()) { if (VOLATILE.has(k)) continue; o[k]=norm(v[k]); } return o; }
    if (typeof v === 'string') {
      if (/^plugin-pid-\d+$/.test(v)) return 'plugin-pid-X';
      if (/^lk_[a-z0-9]+$/.test(v)) return 'lk_X';
      if (v.includes('.dsh_write_probe_')) return v.replace(/\.dsh_write_probe_\d+/g,'.dsh_write_probe_PID');
      if (/^127\.0\.0\.1:\d+$/.test(v)) return '127.0.0.1:PORT';
      if (/^\d{4}-\d{2}-\d{2}T/.test(v)) return 'ISO';
    }
    return v;
  }
  const requests = [
    ['GET','/health'],['GET','/who'],['GET','/status'],['GET','/doctor'],['GET','/commands'],
    ['GET','/host/state'],['GET','/host/jobs'],['GET','/host/selection/latest'],['GET','/frame'],['GET','/nope'],
    ['POST','/plan',{op:'catalog'}],['POST','/cmd',{name:'ping'}],['POST','/cmd',{name:'get_addon_info'}],
    ['POST','/lease',{}],['POST','/release',{}],['POST','/loop',{op:'status'}],['POST','/perf',{op:'status'}],
    ['POST','/opt',{op:'opt_analyze'}],['POST','/txn',{op:'list'}],['POST','/preset',{op:'list'}],['POST','/job',{op:'list'}],
    ['POST','/worker',{op:'list'}],['POST','/act',{code:'1+1'}],['POST','/view',{}],['POST','/headless',{}],
  ];
  async function hit(port, [method, p, body]) {
    const r = await fetch('http://127.0.0.1:'+port+p, { method, headers: body?{'content-type':'application/json'}:{}, body: body?JSON.stringify(body):undefined });
    const text = await r.text();
    let parsed; try { parsed = JSON.parse(text); } catch { parsed = text.slice(0,300); }
    return { status: r.status, ct: (r.headers.get('content-type')||'').split(';')[0], body: norm(parsed) };
  }
  function diff(a, b, p='') {
    if (typeof a !== typeof b) return [p+': type '+typeof a+' vs '+typeof b];
    if (a && typeof a === 'object') {
      if (Array.isArray(a) !== Array.isArray(b)) return [p+': array mismatch'];
      const ka=Object.keys(a).sort(), kb=Object.keys(b).sort(), out=[];
      for (const k of ka) if (!kb.includes(k)) out.push(p+'.'+k+': missing in new');
      for (const k of kb) if (!ka.includes(k)) out.push(p+'.'+k+': extra in new ('+JSON.stringify(b[k]).slice(0,120)+')');
      for (const k of ka) if (kb.includes(k)) out.push(...diff(a[k], b[k], p+'.'+k));
      return out;
    }
    return a === b ? [] : [p+': '+JSON.stringify(a)+' vs '+JSON.stringify(b)];
  }
  for (const req of requests) {
    const a = await hit(pa, req), b = await hit(pb, req);
    const d = diff(a,b);
    if (d.length) { fails++; console.log('DIFF', req[0], req[1]); for (const line of d.slice(0,8)) console.log('   ', line.slice(0,300)); }
    else console.log('same', req[0], req[1], a.status);
  }
  console.log(fails ? 'PARITY FAILURES: '+fails : 'PARITY OK '+requests.length+' requests');
} finally {
  procs.forEach(p=>p.kill());
  await fake.close();
  fs.rmSync(tmp, { force: true });
}
process.exit(fails?1:0);
