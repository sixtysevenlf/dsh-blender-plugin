
// Compare the frozen export surface and createEngine() surface of the base
// runtime/engine.mjs (taken from git) with the split runtime/engine.mjs.
import fs from 'node:fs';
const repo = new URL('../../../../', import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1');
const basePath = process.argv[2];
if (!basePath || !fs.existsSync(basePath)) { console.error('usage: node parity-engine.mjs <base engine path>'); process.exit(2); }
const tmp = repo.replace(/\\/g,'/') + '/runtime/engine.base.tmp.mjs';
fs.copyFileSync(basePath, tmp);
try {
  const b = await import('file:///' + tmp.replace(/^\//,''));
  const n = await import('file:///' + repo.replace(/^\//,'') + '/runtime/engine.mjs');
  const bk = Object.keys(b).sort(), nk = Object.keys(n).sort();
  console.log('base exports:', bk.length, ' new exports:', nk.length);
  console.log('missing exports:', JSON.stringify(bk.filter(k=>!nk.includes(k))));
  console.log('extra exports:', JSON.stringify(nk.filter(k=>!bk.includes(k))));
  const be = b.createEngine(), ne = n.createEngine();
  const bkeys = Object.keys(be), nkeys = Object.keys(ne);
  console.log('base surface keys:', bkeys.length, ' new surface keys:', nkeys.length);
  console.log('missing surface keys:', JSON.stringify(bkeys.filter(k=>!nkeys.includes(k))));
  console.log('extra surface keys:', JSON.stringify(nkeys.filter(k=>!bkeys.includes(k))));
  console.log('surface key order equal:', JSON.stringify(bkeys) === JSON.stringify(nkeys));
  console.log('worker key order equal:', JSON.stringify(Object.keys(be.worker)) === JSON.stringify(Object.keys(ne.worker)));
  console.log('job key order equal:', JSON.stringify(Object.keys(be.job)) === JSON.stringify(Object.keys(ne.job)));
  be.stop(); ne.stop();
  const ok = bk.length===nk.length && !bk.some(k=>!nk.includes(k)) && bkeys.length===nkeys.length && JSON.stringify(bkeys)===JSON.stringify(nkeys);
  console.log(ok ? 'ENGINE PARITY OK' : 'ENGINE PARITY FAILED');
  process.exit(ok?0:1);
} finally { fs.rmSync(tmp, { force: true }); }
