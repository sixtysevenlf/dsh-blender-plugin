/** blender_session：GUI 会话生命周期、所有权、写权限 scope、pause 与事务快照。 */
import { vTool } from '../../contracts/tool.js'
import type { HostContext } from '../../host/context.js'
import { ANY_SCHEMA } from '../schema-diagnostic.js'
import { T } from '../../constants.js'
import { renderOne } from '../../ui/cards.js'
import { executeSession } from './execute.js'

const ACTIONS = [
  'list',
  'status',
  'doctor',
  'attach',
  'claim',
  'detach',
  'release',
  'grant',
  'revoke',
  'grants',
  'pause',
  'resume',
  'mark_read',
  'presence',
  'heartbeat',
  'start',
  'stop',
  'txn',
  'help',
]

function buildSessionTool(ctx: HostContext, port: number) {
  return vTool({
    name: 'blender_session',
    description:
      '【会话 / 归属 / 权限】管理 GUI 会话：list/status/doctor 看状态；attach 附着到用户已打开的 Blender（多实例必须给 instance_id）；grant/revoke 把写 scope（exclusive | collection:名 | prefix:名 | object:名）授予子 agent；pause/resume 只有人类能操作（agent 调用返回 human_action_required）；mark_read 清掉 human_modified 冲突。' +
      'start/stop 只作用 virtual-gui：start 第一次返回 consent_required，等用户授权后由宿主带一次性 token 重试；user-gui 永远由用户自己打开 Blender 并 Connect。' +
      'txn 子面：snapshot/restore/list/prune/mark/revert/marks/drop（restore/mark/revert 属写操作，需要 exclusive grant + 人类未暂停）。' +
      'Gateway 进程与 launch 运维不在模型面：Gateway 由宿主管理。旧 blender_viewport 的会话/健康动作迁移到这里；旧 blender_rt_txn 迁移到 action=txn。',
    parameters: {
      action: { type: 'string', required: true, description: ACTIONS.join(' | ') },
      session_id: { type: 'string', description: '会话 id（大多数会话级动作必填）' },
      instance_id: {
        type: 'string',
        description: 'attach/claim 时的 addon 实例 id（多实例必填；status 里能看到 instanceId）',
      },
      kind: { type: 'string', description: 'start 的会话类型，只接受 virtual-gui' },
      scopes: {
        type: 'string',
        description:
          'grant / mark_read 的 scope 列表：exclusive、collection:COL、prefix:OBJ_、object:Cube；多个用逗号分隔',
      },
      target_agent_id: {
        type: 'string',
        description: 'grant 的目标 agent id（同 DSH session 内）',
      },
      target_dsh_session_id: { type: 'string', description: 'grant 的目标 DSH session id' },
      target_host_id: { type: 'string', description: 'grant 的目标 host id' },
      grant_id: { type: 'string', description: 'revoke 的 grant id' },
      ttl_ms: { type: 'integer', description: 'grant 有效期毫秒（默认由 Gateway 决定）' },
      op: {
        type: 'string',
        description:
          'txn 子动作：snapshot | restore | list | prune | mark | revert | marks | drop | help（默认 list）',
      },
      label: { type: 'string', description: 'txn：快照 / mark 的名字' },
      note: { type: 'string', description: 'txn：snapshot 备注' },
      objects: { type: 'string', description: 'txn：mark 限定的对象（逗号分隔）' },
      keep: { type: 'integer', description: 'txn prune：保留最近 N 个快照' },
      into: { type: 'string', description: 'txn restore：auto | snapshot | original' },
      backup: { type: 'boolean', description: 'txn restore：是否先留 pre_restore 备份' },
      force: { type: 'boolean', description: '保留参数：由 Gateway 决定是否接受强抢语义' },
    },
    output: { schema: ANY_SCHEMA, render: renderOne },
    isConcurrencySafe: () => true,
    timeoutMs: T.txn,
    async execute(args: Record<string, unknown>) {
      return executeSession(port, args)
    },
  })
}

export function registerSession(ctx: HostContext, port: number): void {
  ctx.effect(() => ctx.tools.register(buildSessionTool(ctx, port)), 'dsh-blender-plugin: session')
}
