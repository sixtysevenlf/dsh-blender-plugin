// runtime/engine.ts —— 公开门面（T6）。
// 行为冻结：本文件只做 re-export + createEngine 装配入口；实现位于 core/、execution/、transport/。
export { launchBlender } from './launch.js';
export { PLUGIN_VERSION, engineProvenance, sceneHygieneAudit } from './core/evidence.js';
export { winToWsl, wslToWin, describeConfig, PKG_ROOT } from './config.js';
export { COMMAND_CATALOG, PLAN_CATALOG, TOOL_DETAIL, PLAN_TOOL_MAP, planOpNames, planOpSuggestion,
         planOpCrossChannelHint, catalogFingerprint, PLAN_READ_ONLY_OPS, PLAN_READ_ONLY } from './catalog.js';
export { blenderJoin, PIPELINE_PATH, ADDRESS, WIN_TMP, WSL_TMP, LIVE_PNG_WIN, LIVE_PNG_WSL,
         KIT_PATH, RUNNER_PATH, PERF_PATH, VIEW_PATH, CONTRACT_PATH, PLANNER_PATH, WORKER_PATH, TXN_PATH,
         QC_PATH, QC_RENDER_PATH, PRESET_PATH, AUDIT_PATH, MONTAGE_PATH, MOTION_PATH, DELIVER_PATH,
         GENERATOR_PATH, SCULPT_PATH, FIX_PATH, UV_PATH, PRINT_PATH, SWEEP_PATH, MATERIAL_PATH,
         RENDER_GUARD_PATH, GATE_PATH, IMG_PATH, CALIB_PATH, FACE_PATH, HUMAN_PATH, CLEARANCE_PATH,
         VEHICLE_PATH, SHAPE_PATH, USER_CONFIG_WIN, USER_SCRIPTS_WIN, GPU_PRELUDE, VIEW_PNG_WIN,
         VIEW_PNG_WSL, BLENDER_EXE, PATH_HELPERS, ACT_WRAPPER, ASSEMBLY_PATH, BLOCKOUT_PATH,
         VISUALREPORT_PATH, SHAPESOLVE_PATH, EVALHARNESS_PATH, SURFACE_PATH } from './paths.js';
export { KERNEL_BOOTSTRAP } from './core/kernel.js';
export { writeHeadlessScript } from './staging.js';
export { MODELING_CLASS_GUIDE, renderClassText, classGuideStats, renderHandoff, MODEL_FACING_TOOLS } from './classes.js';
export { catalogPayload } from './core/output.js';
export { GATE_FLAGS, FLAGS_CODE } from './core/flags.js';
export { PATH_GUARD } from './core/scripts.js';
export { tcpProbe, freshPing, diagnose, protocolInfo, AddonClient } from './transport/probes.js';
export { createEngine } from './core/engine.js';
