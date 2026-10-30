// 事务 API 调用（T6）
// @imports
import { extractLoop } from "../core/output.mjs";
import { KERNEL_BOOTSTRAP } from "../core/kernel.mjs";


export function createTxnApi(E) {
  const { addon, ensureTxn } = E;
  /** 事务：snapshot / restore / list / prune / mark / revert / marks / drop / help */
  async function txnCall(op, payload) {
    await ensureTxn();
    const body = 'print("LOOP " + K.dsh_txn_api["dispatch"](' + JSON.stringify(String(op || 'list')) + ', _json.dumps(_json.loads('
      + JSON.stringify(JSON.stringify(payload || {})) + '))))';
    return extractLoop(await addon.send('execute_code', { code: KERNEL_BOOTSTRAP + '\nimport json as _json\n' + body }, 300000));
  }
  return { txnCall };
}
