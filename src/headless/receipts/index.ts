/** headless 回执入口（F1.2 拆分）：对外导出保持 v2.6.2 的公共集合。 */
export {
  HEADLESS_WAIT_MS,
  newRunId,
  startJobThenMaybeWait,
  promotedFromTimeout,
  fetchJobStatus,
} from './envelope.js'
export {
  normalizeShots,
  receiptEnvelope,
  headlessReceipt,
  promotedReceipt,
  type ToolReceipt,
} from './envelope.js'
export { headlessHumanText, type ReceiptContext } from './human-text.js'
