// Blender 实时交互后端 —— 进程入口（T7）。
// 行为冻结：29 条路由、写租约、CORS、生命周期与拆分前一致；实现位于 http/。
import http from "node:http";
import { createEngine, engineProvenance } from "./engine.mjs";
import { createSelectionStore } from "./selectionstore.mjs";
import { describeConfig } from "./config.mjs";
import { PORT, HOST, stats, pushRtt, rttView, reqCounts, bump } from "./http/config.mjs";
import { json, streamJson, readBody, hostJson, hostErrText } from "./http/response.mjs";
import { LEASE, leaseView, releaseStaleLease, leaseAcquire, leaseGate, isReadOnly } from "./http/lease.mjs";
import { trimJob, clampIntParam } from "./http/jobs.mjs";
import { createRouter } from "./http/router.mjs";
import { createRoutes, CONTRACT_ROUTES } from "./http/routes/index.mjs";
import { startServer } from "./http/lifecycle.mjs";

const engine = createEngine();
// v2.3：Blender 原生选中事件（N 面板「发送选中到 DSH」→ 这里 → 客户端插入草稿）
const selectionStore = createSelectionStore();

const E = {
  engine, selectionStore, PORT, HOST, stats, json, streamJson, readBody, pushRtt, rttView, reqCounts, bump,
  LEASE, leaseView, releaseStaleLease, leaseAcquire, leaseGate, isReadOnly,
  trimJob, clampIntParam, describeConfig, engineProvenance,
  hostJson, hostErrText,
};
E.routes = createRoutes(E);
const server = http.createServer(createRouter(E));

// Blender 可能还没起/没启用 addon —— 不能因此让整个后端退出：启动失败只记一条日志，按调用报错。
try {
  await engine.start();
} catch (e) {
  console.error('[blender-rt] 启动时未连上 addon（Blender 未运行或 addon 未监听 9876）：' + String((e && e.message) || e));
}

startServer({ server, engine, port: PORT, host: HOST, routes: CONTRACT_ROUTES });
