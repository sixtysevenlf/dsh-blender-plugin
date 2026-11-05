// runtime/engine.ts —— 公开门面：后端入口与 lib/runtime/engine.js 的稳定导出面。
// 本文件只做 re-export；实现位于 core/、execution/、transport/。
//
// 收敛原则：只保留仓库内**经构建产物路径 lib/runtime/engine.js** 消费的名字 ——
// check-build / check-parity / install-smoke / catalog/logic.ts 的对拍探针、以及少数
// 直接 import 门面的测试。内部实现代码一律直接 import 具体模块，不穿过本文件。
// 新增/删除导出必须先改登记表 config/facades.json（check-no-shims 会拒绝未登记导出）。
export { PLUGIN_VERSION, engineProvenance } from './core/evidence.js'
export {
  COMMAND_CATALOG,
  PLAN_CATALOG,
  TOOL_DETAIL,
  PLAN_TOOL_MAP,
  planOpNames,
  planOpSuggestion,
  planOpCrossChannelHint,
  catalogFingerprint,
  PLAN_READ_ONLY_OPS,
  PLAN_READ_ONLY,
} from './catalog/index.js'
export {
  MODELING_CLASS_GUIDE,
  renderClassText,
  classGuideStats,
  renderHandoff,
  MODEL_FACING_TOOLS,
} from './classes.js'
export { catalogPayload } from './core/output/index.js'
export { GATE_FLAGS, FLAGS_CODE } from './core/flags.js'
export { PATH_GUARD } from './core/scripts.js'
export { createEngine } from './core/engine/index.js'
