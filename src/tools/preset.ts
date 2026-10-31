import { vTool } from '../contracts/tool.js'
import { ANY_SCHEMA } from './schema-diagnostic.js'
import { T } from '../constants.js'
import { ensureBackend } from '../host/backend.js'
import { backendPost, leasedText } from '../host/http.js'
import { renderOne } from '../ui/cards.js'
import { redactSecrets, toolError } from './errors.js'
import { asArray, asRecord, text } from '../contracts/dynamic.js'
import type { HostContext } from '../host/context.js'

interface PresetRow {
  name: string
  kind: string
  keys: number
}

function splitList(v: unknown): string[] {
  return String(v)
    .split(',')
    .map((x) => x.trim())
    .filter(Boolean)
}

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
            if (!(await ensureBackend(port)))
              return toolError(
                'backend_unreachable',
                '后端不可用（127.0.0.1:' + String(port) + '）',
                '先 blender_viewport op=start 拉起后端，再重试',
              )
            const op = text(args.op) || 'list'
            const a: Record<string, unknown> = {}
            if (text(args.name)) a.name = text(args.name)
            if (text(args.kind)) a.kind = text(args.kind)
            if (text(args.note)) a.note = text(args.note)
            if (text(args.path)) a.path = text(args.path)
            if (text(args.tags)) a.tags = splitList(args.tags)
            if (text(args.names)) a.names = splitList(args.names)
            if (text(args.targets)) a.targets = splitList(args.targets)
            if (args.data) {
              try {
                a.data = JSON.parse(String(args.data))
              } catch (e: unknown) {
                return toolError(
                  'invalid_params',
                  'data 不是合法 JSON: ' +
                    (e instanceof Error ? e.message.slice(0, 120) : 'unknown'),
                  '传 JSON 对象字符串，例如 data={"k":1}',
                )
              }
            }
            if (args.overwrite === false) a.overwrite = false
            const r = await backendPost(port, '/preset', { op: op, args: a }, 300000)
            const lt = leasedText(r)
            if (lt) return { text: lt }
            if (!r || r.ok !== true)
              return toolError(
                'backend_error',
                'PRESET ' + op + ' 失败 · ' + redactSecrets(text(r.error || r.raw || 'unknown')),
                '核对 preset 数据与 op',
              )
            const res = asRecord(r.result)
            const parts: string[] = []
            parts.push('PRESET ' + op + ' ok')
            if (op === 'list') {
              const presets: PresetRow[] = asArray(res.presets).map((row) => {
                const p = asRecord(row)
                return {
                  name: text(p.name),
                  kind: text(p.kind),
                  keys: Number(p.keys ?? 0),
                }
              })
              parts.push(
                '共 ' +
                  String(res.count ?? presets.length) +
                  ' 个：' +
                  presets
                    .map((p) => p.name + '(' + p.kind + ',' + String(p.keys) + '键)')
                    .join(' · '),
              )
              parts.push('目录：' + text(res.dir))
            } else if (op === 'apply') {
              const rr = asRecord(res.report)
              const applied = asArray(rr.applied)
              const skipped = asArray(rr.skipped)
              const errors = asArray(rr.errors)
              parts.push(
                '配方 ' +
                  text(res.preset) +
                  ' · 目标 ' +
                  JSON.stringify(res.targets) +
                  ' · 写入 ' +
                  String(applied.length) +
                  ' 项',
              )
              if (skipped.length) parts.push('跳过：' + JSON.stringify(skipped).slice(0, 300))
              if (errors.length) parts.push('错误：' + JSON.stringify(errors).slice(0, 300))
            } else parts.push(JSON.stringify(res).slice(0, 900))
            return { text: parts.join(String.fromCharCode(10)) }
          },
        }),
      ),
    '@dsh-external/dsh-blender-plugin: rt-preset',
  )
}
