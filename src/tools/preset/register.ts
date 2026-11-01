import { vTool } from '../../contracts/tool.js'
import { ANY_SCHEMA } from '../schema-diagnostic.js'
import { T } from '../../constants.js'
import { renderOne } from '../../ui/cards.js'
import type { HostContext } from '../../host/context.js'
import { executePreset } from './execute.js'

export function registerPreset(ctx: HostContext, port: number): void {
  ctx.effect(
    () =>
      ctx.tools.register(
        vTool({
          name: 'blender_rt_preset',
          description:
            '【配方库】把「参数组合」变成可保存、可套用、可分发的资产：save / list / get / apply / delete / export / import / help。' +
            'data 用点路径表达：材质节点 {"inputs.Base Color": [1,0,0,1]}、对象属性 {"location": [0,0,1]}、场景设置 {"render.resolution_x": 640}。' +
            'apply 的 targets 用 MAT:材质名 / OBJ:对象名 / SCENE（逗号分隔）；不给 targets 只预览（dry_run），给了就实际写入并回报 applied/skipped/errors。' +
            'export/import 走单个 JSON bundle，便于把配方分享到别的机器或会话。',
          parameters: {
            op: {
              type: 'string',
              required: true,
              description: 'save | list | get | apply | delete | export | import | help',
            },
            name: { type: 'string', description: '配方名（save / get / apply / delete）' },
            kind: {
              type: 'string',
              description: '分类（list 可按 kind 过滤），如 material / object / scene',
            },
            tags: { type: 'string', description: '标签（逗号分隔；list 可按 tag 过滤）' },
            note: { type: 'string', description: '备注（save）' },
            data: {
              type: 'string',
              description: '点路径 JSON 字符串（save），如 {"inputs.Roughness": 0.4}',
            },
            targets: {
              type: 'string',
              description: 'apply 目标（逗号分隔）：MAT:材质名 / OBJ:对象名 / SCENE',
            },
            path: { type: 'string', description: 'export 输出路径 / import 输入路径' },
            names: { type: 'string', description: 'export 选定的配方名（逗号分隔；省略=全部）' },
            overwrite: { type: 'boolean', description: 'save/import 是否覆盖同名（默认 true）' },
          },
          output: { schema: ANY_SCHEMA, render: renderOne },
          isConcurrencySafe: () => false,
          timeoutMs: T.preset,
          async execute(args: Record<string, unknown>) {
            return executePreset(port, args)
          },
        }),
      ),
    '@dsh-external/dsh-blender-plugin: rt-preset',
  )
}
