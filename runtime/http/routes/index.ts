// 路由注册表（T7）—— 也是 29 条对外路由契约的唯一真源（onListening 日志与契约测试都读它）。
// 顺序 = 拆分前 server.mjs if 链顺序（/host 前缀最先，其次 health/gone，再按原顺序）。
import { createHealthRoutes } from "./health.js";
import { createSessionRoutes } from "./session.js";
import { createFrameRoutes } from "./frame.js";
import { createActRoutes } from "./act.js";
import { createCommandsRoutes } from "./commands.js";
import { createLoopRoutes } from "./loop.js";
import { createPerfRoutes } from "./perf.js";
import { createOptRoutes } from "./opt.js";
import { createViewRoutes } from "./view.js";
import { createHeadlessRoutes } from "./headless.js";
import { createPlanRoutes } from "./plan.js";
import { createWorkerRoutes } from "./worker.js";
import { createTxnRoutes } from "./txn.js";
import { createPresetRoutes } from "./preset.js";
import { createJobRoutes } from "./job.js";
import { createHostRoutes } from "../host.js";
import type { RouteDef } from "../router.js";
import type { HttpRouteDeps } from "./deps.js";

/** 对外路由契约（稳定集合，顺序与拆分前 onListening 日志一致）。 */
export const CONTRACT_ROUTES = [
  "/health", "/status", "/doctor", "/launch", "/who", "/lease", "/release",
  "/frame", "/frame.png", "/act", "/commands", "/cmd", "/loop", "/perf", "/opt",
  "/view", "/headless", "/plan", "/worker", "/txn", "/preset", "/job",
  "/host/scene-items", "/host/jobs", "/host/jobs/log", "/host/state",
  "/host/selection", "/host/selection/latest", "/host/selection/consume",
];

export function createRoutes(E: HttpRouteDeps): RouteDef[] {
  const host = createHostRoutes(E);
  const health = createHealthRoutes(E);
  const session = createSessionRoutes(E);
  const frame = createFrameRoutes(E);
  const act = createActRoutes(E);
  const commands = createCommandsRoutes(E);
  const loop = createLoopRoutes(E);
  const perf = createPerfRoutes(E);
  const opt = createOptRoutes(E);
  const view = createViewRoutes(E);
  const headless = createHeadlessRoutes(E);
  const plan = createPlanRoutes(E);
  const worker = createWorkerRoutes(E);
  const txn = createTxnRoutes(E);
  const preset = createPresetRoutes(E);
  const job = createJobRoutes(E);
  return [
    { method: null, exact: "/host", prefix: "/host/", fn: host.host },
    { method: "GET", paths: ["/health"], fn: health.health },
    { method: null, paths: ["/", "/index.html", "/stream.mjpg", "/input", "/frame"], fn: health.gone },
    { method: "GET", paths: ["/status"], fn: health.status },
    { method: "GET", paths: ["/doctor"], fn: health.doctor },
    { method: "POST", paths: ["/launch"], fn: session.launch },
    { method: "GET", paths: ["/frame.png"], fn: frame.frame },
    { method: "POST", paths: ["/act"], fn: act.act },
    { method: "GET", paths: ["/commands"], fn: commands.commands },
    { method: "POST", paths: ["/cmd"], fn: commands.cmd },
    { method: "POST", paths: ["/loop"], fn: loop.loop },
    { method: "POST", paths: ["/perf"], fn: perf.perf },
    { method: "POST", paths: ["/opt"], fn: opt.opt },
    { method: "GET", paths: ["/who"], fn: session.who },
    { method: "POST", paths: ["/lease"], fn: session.lease },
    { method: "POST", paths: ["/release"], fn: session.release },
    { method: "POST", paths: ["/view"], fn: view.view },
    { method: "POST", paths: ["/headless"], fn: headless.headless },
    { method: "POST", paths: ["/plan"], fn: plan.plan },
    { method: "POST", paths: ["/worker"], fn: worker.worker },
    { method: "POST", paths: ["/txn"], fn: txn.txn },
    { method: "POST", paths: ["/preset"], fn: preset.preset },
    { method: "POST", paths: ["/job"], fn: job.job },
  ];
}
