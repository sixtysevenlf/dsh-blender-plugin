// HTTP 响应原语（T7）：JSON / ndjson 流式 / 请求体读取 / 宿主 CORS。
// 行为冻结：状态码、content-type、cache-control、心跳间隔（15 s）、body 上限（4 MiB）全部不变。
function json(res, code, obj) {
  const body = JSON.stringify(obj);
  res.writeHead(code, { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store' });
  res.end(body);
}
/**
 * 流式响应（ndjson）—— 长任务必需：客户端 fetch 的响应头超时默认 300 s（实测 UND_ERR_HEADERS_TIMEOUT），
 * 而服务端要等子进程跑完才回包 → 长任务必然踩超时。这里立刻发响应头 + 周期心跳，最后一行才是结果。
 */
async function streamJson(res, producer, hbMs = 15000) {
  const t0 = Date.now();
  res.writeHead(200, { 'content-type': 'application/x-ndjson; charset=utf-8', 'cache-control': 'no-store', 'x-dsh-stream': '1' });
  let done = false;
  const hb = setInterval(() => {
    if (done) return;
    try { res.write(JSON.stringify({ heartbeat: true, elapsedMs: Date.now() - t0 }) + '\n'); } catch (e) { /* 客户端断了就算了 */ }
  }, hbMs);
  let obj;
  try { obj = await producer(); }
  catch (e) { obj = { ok: false, error: String((e && e.message) || e), diagnosis: (e && e.diagnosis) || null }; }
  done = true; clearInterval(hb);
  try { res.write(JSON.stringify(obj) + '\n'); } catch (e) { /* ignore */ }
  res.end();
  return obj;
}

function readBody(req, limit = 1 << 22) {
  return new Promise((resolve, reject) => {
    let n = 0; const chunks = [];
    req.on('data', (c) => { n += c.length; if (n > limit) { reject(new Error('body too large')); req.destroy(); return; } chunks.push(c); });
    req.on('end', () => resolve(Buffer.concat(chunks).toString('utf8')));
    req.on('error', reject);
  });
}
// CORS 控制浏览器对响应的读取，不是身份验证，也不阻止非浏览器客户端伪造 Origin。
const HOST_ORIGIN_RE = /^(http:\/\/(127\.0\.0\.1|localhost)(:\d+)?|dsh-app:\/\/app)$/;
function hostCorsHeaders(origin) {
  const h = {
    'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
    'Access-Control-Allow-Headers': 'content-type',
    'Access-Control-Max-Age': '600',
    'Vary': 'Origin',
  };
  if (origin && HOST_ORIGIN_RE.test(String(origin))) h['Access-Control-Allow-Origin'] = String(origin);
  return h;
}
function hostJson(res, code, obj, origin) {
  res.writeHead(code, Object.assign({ 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store' },
                                    hostCorsHeaders(origin)));
  res.end(JSON.stringify(obj));
}
function hostErrText(e) { return String((e && e.message) || e); }
/** `@` 提及的 limit：夹到 [1,1000]，默认 200（旧 viewstore 口径，路由还在用）。 */
function clampItemsLimit(v, dflt = 200) {
  const n = Number(v);
  if (!Number.isFinite(n)) return dflt;
  return Math.max(1, Math.min(1000, Math.round(n)));
}
function hostNormEmpty(v) { return (v === '' || v == null) ? null : v; }

export { json, streamJson, readBody, hostCorsHeaders, hostJson, hostErrText, clampItemsLimit, hostNormEmpty };
