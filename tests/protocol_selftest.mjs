/**
 * 协议适配自检 —— 不需要 Blender，用两个 mock addon 把两套协议都跑一遍。
 *
 *   node tests/protocol_selftest.mjs
 *
 * 覆盖：
 *   1. detectProtocol() 能分辨两种 addon（扁平 / category-action）
 *   2. resolveProtocol() 的 auto 与显式配置两条路径
 *   3. AddonClient 走两种协议时的请求封套与回包解包（含 execute_code 的 {executed,result} 形状）
 *   4. get_viewport_screenshot 的 python 载荷在 category-action 侧的后处理
 *   5. 错误传播（两种协议各一次）
 *   6. category-action 侧缺失命令的明确报错（不假装成功）
 */
import net from 'node:net';
import { AddonClient } from '../runtime/engine.mjs';
import { detectProtocol, resolveProtocol, resetProtocolCache } from '../runtime/addon-protocol.mjs';

let pass = 0;
const fails = [];
function ok(label, cond, extra) {
  if (cond) { pass++; console.log('  ok   ' + label); }
  else { fails.push(label); console.log('  FAIL ' + label + (extra ? '  → ' + extra : '')); }
}
function eq(label, got, want) {
  ok(label, JSON.stringify(got) === JSON.stringify(want), 'got=' + JSON.stringify(got) + ' want=' + JSON.stringify(want));
}

/* ───────── mock 1：ahujasid 扁平协议（无分帧，{status,result}） ───────── */
function mockFlat() {
  const srv = net.createServer((sock) => {
    let buf = '';
    sock.on('data', (d) => {
      buf += d.toString('utf8');
      let req = null;
      try { req = JSON.parse(buf); } catch (e) { return; }
      buf = '';
      let reply;
      if (req.type === 'ping') reply = { status: 'success', result: { pong: true } };
      else if (req.type === 'get_scene_info') reply = { status: 'success', result: { name: 'Scene-flat' } };
      else if (req.type === 'get_viewport_screenshot') reply = { status: 'success', result: { output_path: req.params.filepath, bytes: 1024 } };
      else if (req.type === 'execute_code') {
        reply = String(req.params.code).includes('#FAIL')
          ? { status: 'error', message: 'flat boom' }
          : { status: 'success', result: { executed: true, result: 'flat-out:' + req.params.code } };
      } else reply = { status: 'error', message: 'unknown command type: ' + req.type };
      sock.write(JSON.stringify(reply));
    });
    sock.on('error', () => {});
  });
  return srv;
}

/* ───────── mock 2：category/action 协议（换行分帧，{success,data|error}） ───────── */
function mockCategoryAction() {
  const srv = net.createServer((sock) => {
    let buf = Buffer.from('');
    sock.on('data', (d) => {
      buf = Buffer.concat([buf, d]);
      for (;;) {
        const i = buf.indexOf(0x0a);
        if (i < 0) break;
        const line = buf.subarray(0, i).toString('utf8');
        buf = buf.subarray(i + 1);
        if (!line.trim()) continue;
        let req = null;
        try { req = JSON.parse(line); } catch (e) { continue; }
        const head = { id: req.id };
        let out;
        if (req.category === 'system' && req.action === 'get_info') out = { success: true, data: { version_string: '5.2.1 LTS-mock', scene: 'Scene' } };
        else if (req.category === 'scene' && req.action === 'get_info') out = { success: true, data: { name: 'Scene-ca' } };
        else if (req.category === 'object' && req.action === 'get_info') out = { success: true, data: { name: req.params.name } };
        else if (req.category === 'utility' && req.action === 'execute_python') {
          const code = String(req.params.code);
          if (code.includes('#FAIL')) out = { success: false, error: { code: 'PYTHON_ERROR', message: 'ca boom' } };
          else if (code.includes('VIEW_OK')) {
            const m = code.match(/_p = "((?:[^"\\]|\\.)*)"/);
            const p = m ? JSON.parse('"' + m[1] + '"') : 'C:\\mock\\frame.png';
            out = { success: true, data: { output: 'noise\nVIEW_OK ' + p + ' 2048\n' } };
          } else if (code.includes('SNAP')) out = { success: true, data: { output: 'SNAP {"name":"Scene-ca","object_count":3}\n' } };
          else out = { success: true, data: { output: 'ca-out:' + code } };
        } else out = { success: false, error: { code: 'UNKNOWN_ACTION', message: 'no such command: ' + req.category + '.' + req.action } };
        sock.write(JSON.stringify(Object.assign(head, out)) + '\n');
      }
    });
    sock.on('error', () => {});
  });
  return srv;
}

const listen = (srv) => new Promise((r) => srv.listen(0, '127.0.0.1', () => r(srv.address().port)));

/* ───────── mock 3：扁平 addon，但只在收到换行分帧后才回（模拟"第一次探测时 addon 正忙"） ─────────
   用来验证探测的纠错分支：第一轮扁平探测超时 → 第二轮 category/action 探测发出后，
   它回的其实是**扁平封套**（{status,result}）→ 应当据此纠正回 ahujasid，而不是误判成 category-action。 */
function mockFlatLateReply() {
  const srv = net.createServer((sock) => {
    let buf = Buffer.from('');
    sock.on('data', (d) => {
      buf = Buffer.concat([buf, d]);
      for (;;) {
        const i = buf.indexOf(0x0a);
        if (i < 0) break;
        buf = buf.subarray(i + 1);
        sock.write(JSON.stringify({ status: 'error', message: 'Unknown command type: command' }));
      }
    });
    sock.on('error', () => {});
  });
  return srv;
}

const flatSrv = mockFlat();
const caSrv = mockCategoryAction();
const lateSrv = mockFlatLateReply();
const flatPort = await listen(flatSrv);
const caPort = await listen(caSrv);
const latePort = await listen(lateSrv);
console.log('\n=== addon 协议适配自检 ===\n  mock flat=:' + flatPort + '  category-action=:' + caPort + '  flat-late=:' + latePort + '\n');

/* 1. 探测 */
eq('detectProtocol(flat) → ahujasid', (await detectProtocol('127.0.0.1', flatPort, 800))?.id, 'ahujasid');
eq('detectProtocol(category-action) → category-action', (await detectProtocol('127.0.0.1', caPort, 800))?.id, 'category-action');
eq('detectProtocol(死端口) → null', await detectProtocol('127.0.0.1', flatPort + 4242, 300), null);
eq('detectProtocol(扁平 addon 第一轮没回) → 按封套形状纠正回 ahujasid', (await detectProtocol('127.0.0.1', latePort, 500))?.id, 'ahujasid');

/* 2. 解析（auto / 显式） */
resetProtocolCache();
const rFlat = await resolveProtocol({ host: '127.0.0.1', port: flatPort, prefer: 'auto', timeoutMs: 800 });
eq('resolveProtocol auto(flat)', { id: rFlat.proto.id, from: rFlat.from }, { id: 'ahujasid', from: 'auto' });
resetProtocolCache();
const rCa = await resolveProtocol({ host: '127.0.0.1', port: caPort, prefer: 'auto', timeoutMs: 800 });
eq('resolveProtocol auto(category-action)', { id: rCa.proto.id, from: rCa.from }, { id: 'category-action', from: 'auto' });
resetProtocolCache();
const t0 = Date.now();
const rForced = await resolveProtocol({ host: '127.0.0.1', port: caPort, prefer: 'ahujasid' });
eq('显式配置不探测（from=config）', { id: rForced.proto.id, from: rForced.from }, { id: 'ahujasid', from: 'config' });
ok('显式配置立即返回（<50ms）', Date.now() - t0 < 50, String(Date.now() - t0) + 'ms');
await resolveProtocol({ host: '127.0.0.1', port: caPort, prefer: 'nope' }).catch((e) => ok('未知协议名报错', /未知 addonProtocol/.test(String(e.message))));

/* 3 + 5. 两种协议下的实际调用 */
for (const [name, port] of [['ahujasid', flatPort], ['category-action', caPort]]) {
  resetProtocolCache();
  const c = new AddonClient({ host: '127.0.0.1', port: port });
  const info = await c.protocol();
  eq('[' + name + '] 客户端协议', info.proto.id, name);

  const p = await c.send('ping', {}, 3000);
  ok('[' + name + '] ping 有回包', !!(p && (p.pong === true || p.blender)), JSON.stringify(p));
  const sc = await c.send('get_scene_info', {}, 3000);
  ok('[' + name + '] get_scene_info 回场景名', /^Scene/.test(String(sc.name)), JSON.stringify(sc));

  const ex = await c.send('execute_code', { code: 'print(1)' }, 3000);
  eq('[' + name + '] execute_code 形状 = {executed,result}', { executed: ex.executed, hasResult: typeof ex.result === 'string' }, { executed: true, hasResult: true });

  const err = await c.send('execute_code', { code: '#FAIL' }, 3000).then(() => null, (e) => e);
  ok('[' + name + '] 错误能传到调用方', !!err && /boom/.test(String(err.message)), err && err.message);

  const shot = await c.send('get_viewport_screenshot', { max_size: 320, filepath: 'C:\\mock\\frame.png', format: 'png' }, 3000);
  ok('[' + name + '] 视口帧调用成功', !!shot && typeof shot === 'object', JSON.stringify(shot));
  c.close();
}
// category-action 侧视口帧是真的走了自建 python（mock 回 VIEW_OK），确认 bytes 被解析出来
{
  resetProtocolCache();
  const c = new AddonClient({ host: '127.0.0.1', port: caPort });
  const shot = await c.send('get_viewport_screenshot', { max_size: 320, filepath: 'C:\\mock\\frame.png' }, 3000);
  eq('[category-action] VIEW_OK 被解析成 bytes', shot.bytes, 2048);
  const snap = await c.send('get_world_state_snapshot', {}, 3000);
  eq('[category-action] 快照 JSON 被解析', snap.name, 'Scene-ca');
  c.close();
}

/* 6. 缺失命令要明确报错 */
{
  resetProtocolCache();
  const c = new AddonClient({ host: '127.0.0.1', port: caPort });
  const e1 = await c.send('no_such_cmd', {}, 3000).then(() => null, (e) => e);
  ok('[category-action] 未知命令明确报错', !!e1 && /不支持命令/.test(String(e1.message)), e1 && e1.message);
  const e2 = await c.send('search_polyhaven_assets', {}, 3000).then(() => null, (e) => e);
  ok('[category-action] 缺集成通道明确报错', !!e2 && /集成通道/.test(String(e2.message)), e2 && e2.message);
  const hel = await c.send('get_telemetry_consent', {}, 3000);
  eq('[category-action] 本地桩不进 socket', hel, { consent: false });
  c.close();
}

/* 7. ahujasid 模式下的透传不受映射层干扰（未知命令原样发给 addon） */
{
  resetProtocolCache();
  const c = new AddonClient({ host: '127.0.0.1', port: flatPort });
  const e = await c.send('some_vendor_cmd', {}, 3000).then(() => null, (err) => err);
  ok('[ahujasid] 未知命令透传给 addon 并回传其错误', !!e && /unknown command type/.test(String(e.message)), e && e.message);
  c.close();
}

/* 8. mock 的“无分帧就不回”行为要成立，否则上面的探测结论不成立 */
{
  const raw = await new Promise((resolve) => {
    const s = net.connect({ host: '127.0.0.1', port: caPort });
    let got = '';
    const t = setTimeout(() => { s.destroy(); resolve(got); }, 500);
    s.on('data', (d) => { got += d.toString(); });
    s.once('connect', () => s.write(JSON.stringify({ type: 'ping', params: {} })));   // 故意不发 \n
    s.on('close', () => { clearTimeout(t); resolve(got); });
  });
  eq('mock 侧：无换行的扁平报文确实不回（探测判据的前提）', raw, '');
}

flatSrv.close(); caSrv.close();

console.log('\n通过 ' + pass + ' 项' + (fails.length ? '，失败 ' + fails.length + ' 项：\n  - ' + fails.join('\n  - ') : '，全部通过'));
process.exit(fails.length ? 1 : 0);
