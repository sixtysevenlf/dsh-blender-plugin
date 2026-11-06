/**
 * dsh-blender-plugin —— 客户端半边（浏览器 bundle）。
 *
 * 手写、零打包器、零新增依赖（契约 §0 / §2.2）。本文件**就是** bundle 源：
 * `scripts/build-client.mjs` 原样拷成 `lib/client.js`，由 `package.json` 的
 * `dsh.client` + `exports["./client"]` 暴露给 DSH 宿主。
 *
 * 包装格式逐字照本机已在跑的第三方样板
 * `@dsh-external/dsh-coc-dice/lib/client.js`：
 *   window.__ModuleLoader__.load({ id, factory: (require) => { … return module.exports } })
 * 加载器语义（`dsh-client-modules/lib/types/client/manifest.d.ts:9-16`）：执行 bundle 只
 * **登记** factory；所有副作用在 materialization 时才跑。
 *
 * v2.3 仅保留两块功能（DSH 侧视口面板与 blender_rt_pick 已于 v2.3 删除）：
 *   ① `@` 提及源 —— `GET /host/scene-items?q=` 按 对象/材质/集合 分组；
 *   ② Blender 原生选中 → 草稿 —— 无头桥（`conversation.input.dock`）轮询
 *      `GET /host/selection/latest`，把 addon 推来的选中插入当前会话草稿（**不自动发送**）。
 *
 * 纪律：
 *   - 不得 `import`/`export` 语法、不得顶层 `await`、不得引用 Node 内置模块；
 *     可 `require("react")`（baseline 提供，core client-ui 包同样用法）。
 *   - 所有 fetch 失败**静默降级**；`@` 候选失败返回 `[]` **不抛**。
 *   - 选中插入**不自动发送**，只填草稿。
 *   - 后端地址默认 http://127.0.0.1:9877，可被 ctx 配置 / localStorage 覆盖。
 */
window.__ModuleLoader__.load({
  id: 'dsh-blender-plugin',
  factory: (require) => {
    var module = { exports: {} }
    var exports = module.exports
    Object.defineProperty(exports, Symbol.toStringTag, { value: 'Module' })
    var React = require('react')

    //#region 配置
    /** 后端默认地址（契约 §2.4 / AGENTS.md §2：后端 127.0.0.1:9877）。 */
    var DEFAULT_BACKEND = 'http://127.0.0.1:9877'
    /** localStorage 覆盖键（最高优先级的用户侧覆盖）。 */
    var LS_BACKEND_KEY = 'dsh-blender-plugin:backend'
    /** v2.3：Blender 原生选中轮询间隔（会话可见时才轮询）。 */
    var SELECTION_POLL_MS = 100 // v2.6.1 性能实测：1200→100ms，端到端 p50 993→156ms（本机回环，见契约「选中轮询」）
    /** v2.3：草稿文本里最多列多少个对象名（几百个零件时收敛成摘要，别刷屏）。 */
    var SELECTION_DRAFT_NAME_LIMIT = 24
    /** v2.4：子元素（顶点/边/面/骨骼）草稿里最多列多少个索引/名字。 */
    var SUB_DRAFT_INDEX_LIMIT = 12

    /** apply() 期间由 ctx 配置解析出的地址；backend() 在它之上叠 localStorage。 */
    var configuredBackend = DEFAULT_BACKEND
    /** 幂等守卫：cordis 同一 fiber 只 apply 一次；卸载时复位（见 apply 里的 effect）。 */
    var started = false

    /** 去掉尾部斜杠（不用正则字面量，避免粗筛误伤）。 */
    function stripTrailingSlashes(value) {
      var out = String(value === null || value === undefined ? '' : value)
      while (out.length > 0 && out.charAt(out.length - 1) === '/') out = out.slice(0, -1)
      return out
    }

    /**
     * 当前生效的后端地址。每次调用都重读 localStorage，所以用户在控制台里
     * `localStorage.setItem("dsh-blender-plugin:backend", "http://…")` 后立刻生效。
     */
    function backend() {
      try {
        var v = window.localStorage.getItem(LS_BACKEND_KEY)
        if (v) return stripTrailingSlashes(v)
      } catch (e) {
        /* 隐私模式 / 无 localStorage → 退回配置值 */
      }
      return configuredBackend
    }

    /** 优先级：window.__DSH_BLENDER_BACKEND__ > ctx.config.{backend,host,blenderBackend} > 默认。 */
    function resolveConfiguredBackend(ctx) {
      try {
        var g = window.__DSH_BLENDER_BACKEND__
        if (typeof g === 'string' && g) return stripTrailingSlashes(g)
      } catch (e) {
        /* ignore */
      }
      try {
        var c = ctx && ctx.config
        if (c) {
          if (typeof c.backend === 'string' && c.backend) return stripTrailingSlashes(c.backend)
          if (typeof c.host === 'string' && c.host) return stripTrailingSlashes(c.host)
          if (typeof c.blenderBackend === 'string' && c.blenderBackend)
            return stripTrailingSlashes(c.blenderBackend)
        }
      } catch (e) {
        /* ignore */
      }
      return DEFAULT_BACKEND
    }
    //#endregion

    //#region 通用小工具
    /** 任何值 → 可读错误文本（绝不抛）。 */
    function errText(e) {
      if (e === null || e === undefined) return '未知错误'
      if (typeof e === 'string') return e
      if (e.message) return String(e.message)
      return String(e)
    }

    /** 夹到 [0,1]（契约 §1.5：坐标超出范围夹住）。 */
    function clamp01(n) {
      if (!(typeof n === 'number') || n !== n) return 0
      if (n < 0) return 0
      if (n > 1) return 1
      return n
    }

    /** 取路径最后一段（面板里显示 .blend 文件名）。 */
    function shortPath(p) {
      var s = String(p === null || p === undefined ? '' : p)
      var i = Math.max(s.lastIndexOf('/'), s.lastIndexOf('\\'))
      return i >= 0 ? s.slice(i + 1) : s
    }

    /** Unix 秒 → 本地 HH:MM:SS。 */
    function timeLabel(unixSec) {
      try {
        var d = new Date(unixSec * 1000)
        function two(n) {
          return n < 10 ? '0' + n : String(n)
        }
        return two(d.getHours()) + ':' + two(d.getMinutes()) + ':' + two(d.getSeconds())
      } catch (e) {
        return ''
      }
    }

    /** source 字段 → 中文（契约 §1.2）。 */
    function sourceLabel(src) {
      if (src === 'user') return '用户'
      if (src === 'auto') return '自动'
      if (src === 'model') return '模型'
      return '无'
    }

    /**
     * 从 ctx 上安全取一个服务：`ctx.get(name)` 是 cordis 的安全访问器
     * （`dsh-client-ui-conversation/lib/client.js:22945` 同款用法）；
     * 服务未挂载时返回 undefined 而不是抛。
     */
    function serviceOf(ctx, name) {
      try {
        if (ctx && typeof ctx.get === 'function') return ctx.get(name)
        if (ctx) return ctx[name]
      } catch (e) {
        /* ignore */
      }
      return undefined
    }
    //#endregion

    //#region HTTP（全部永不抛，失败折叠成 {ok:false}）
    /**
     * 永不抛的 JSON 取回。失败一律折叠成 `{ ok:false, status, body:null, error }`，
     * 由调用方静默降级 —— 契约要求面板「fetch 失败必须静默降级」。
     * @returns {Promise<{ok:boolean,status:number,body:any,error:(string|null)}>}
     */
    async function fetchJSON(url, init) {
      var res
      try {
        res = await window.fetch(url, init)
      } catch (e) {
        return { ok: false, status: 0, body: null, error: errText(e) }
      }
      var status = res && typeof res.status === 'number' ? res.status : 0
      var httpOk = !!(res && res.ok)
      var body = null
      try {
        body = await res.json()
      } catch (e) {
        body = null
      }
      return { ok: httpOk, status: status, body: body, error: null }
    }

    /** POST 一个小 JSON 体（自动带 content-type）。 */
    function postJSON(url, payload) {
      return fetchJSON(url, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(payload),
      })
    }

    /**
     * `GET /host/ready` → 响应体或 null（契约 §7.1）。
     * **门控的唯一依据**；取不到/未就绪一律当作"不可用"（静默降级）。
     */
    //#endregion

    //#region 点击 → 草稿（契约 §2.4；**不自动发送**）
    /**
     * 读会话输入框当前草稿文本（备用通道用：绝不覆盖用户已写的内容）。
     * `SessionInput.state: SnapshotStore<InputState>`，`InputState.draft: string`
     * （`dsh-client-ui-conversation/lib/types/client/contract/input.d.ts:254-260`）。
     * @returns {{readable:boolean, draft:string}}
     */
    function readDraftInfo(sessionInput) {
      try {
        var st = sessionInput && sessionInput.state
        if (st && typeof st.getSnapshot === 'function') {
          var snap = st.getSnapshot()
          if (snap && typeof snap.draft === 'string') return { readable: true, draft: snap.draft }
        }
      } catch (e) {
        /* ignore */
      }
      return { readable: false, draft: '' }
    }

    /**
     * 失败提示：用 error 级，DSH 会渲染成自动消失的 Toast（约 4 秒，无关闭按钮）；不可用则静默。
     * **不要用 info**：DSH 把 info 渲染成输入栏里的常驻 banner，没有清除接口，
     * 发送消息后也不会消失（v2.3 的“已接收 Blender 选中”因此变成永久横幅）。
     * 成功路径也不提示：草稿里出现文本本身就是反馈。
     */
    function notifyError(deps, text) {
      try {
        var f = deps && deps.fallbackInput
        if (f && typeof f.notify === 'function') f.notify('error', text)
      } catch (e) {
        /* 静默 */
      }
    }

    //#endregion

    //#region v2.3 · Blender 原生选中 → DSH 草稿（契约 v2.3 增补）
    /**
     * 把 addon 推来的 selection payload 折成一条草稿文本。
     * 空选中（names 为空）返回 null —— 调用方据此跳过插入。
     *
     * v2.3 两种 kind：
     *   · objects    —— 用户选中的对象；名单超过 SELECTION_DRAFT_NAME_LIMIT 时收敛成"前 N 件 + 共 M 件"；
     *   · collection —— 用户直接发的整个集合（大装配体）；给集合名 + 件数 + 前几个成员。
     */
    /**
     * v2.4：编辑模式 / 姿态模式的子元素选中 → 草稿文本。
     * 网格给 点/边/面 计数 + 索引样例；骨架给选中的骨骼名；曲线/晶格给控制点计数。
     * 完整数组在 payload 里（get_selection 也能查），这里只列前 SUB_DRAFT_INDEX_LIMIT 个。
     */
    function subSelectionText(sel) {
      var sub = sel && sel.sub
      if (!sub || typeof sub !== 'object') return null
      var type = String(sub.type || '')
      var obj = String(sub.object || '')
      var counts = sub.counts && typeof sub.counts === 'object' ? sub.counts : {}
      var more = sub.truncated === true
      var prefix = 'Blender 子元素选中'
      if (type === 'MESH') {
        var v = Number(counts.vertices || 0),
          e = Number(counts.edges || 0),
          f = Number(counts.faces || 0)
        var text =
          'Blender 编辑模式（网格）「' +
          obj +
          '」：已选 ' +
          v +
          ' 顶点 / ' +
          e +
          ' 边 / ' +
          f +
          ' 面。'
        var idx = sub.indices && typeof sub.indices === 'object' ? sub.indices : {}
        var labels = { vertices: '顶点索引', edges: '边索引', faces: '面索引' }
        var parts = []
        for (var k in labels) {
          if (!Object.prototype.hasOwnProperty.call(labels, k)) continue
          var arr = Array.isArray(idx[k]) ? idx[k] : []
          if (!arr.length) continue
          var shown = arr.slice(0, SUB_DRAFT_INDEX_LIMIT).join(',')
          if (arr.length > SUB_DRAFT_INDEX_LIMIT || more) shown += '…'
          parts.push(labels[k] + '：' + shown)
        }
        if (parts.length) text += parts.join('；') + '。'
        return text + '用 bpy / BMesh 精确修改这些元素；用户说"这些点/线/面"时指它们。'
      }
      if (type === 'ARMATURE') {
        var bones = Array.isArray(sub.bones) ? sub.bones : []
        var boneCount = Number(counts.bones || bones.length)
        var head =
          'Blender 骨骼（骨架）「' +
          obj +
          '」' +
          (String(sub.mode || '') === 'POSE' ? '姿势模式' : '编辑模式') +
          '：已选 ' +
          boneCount +
          ' 根'
        if (bones.length) {
          head += '：' + bones.slice(0, SUB_DRAFT_INDEX_LIMIT).join('、')
          if (bones.length > SUB_DRAFT_INDEX_LIMIT || more) head += '…'
        }
        return (
          head +
          "。用 bpy.data.objects['" +
          obj +
          '\'].pose.bones[...] 精确定位；用户说"这些骨骼"时指它们。'
        )
      }
      if (type === 'CURVE' || type === 'LATTICE') {
        var pts = Array.isArray(sub.points) ? sub.points : []
        var pcount = Number(counts.points || pts.length)
        var label = type === 'CURVE' ? '曲线/曲面控制点' : '晶格控制点'
        var txt =
          'Blender ' +
          label +
          '「' +
          obj +
          '」：' +
          String(sub.mode || '') +
          '，已选 ' +
          pcount +
          ' 个'
        if (pts.length) {
          var sample = pts.slice(0, SUB_DRAFT_INDEX_LIMIT).map(function (p) {
            return Array.isArray(p) ? String(p[0]) + ':' + String(p[1]) : String(p)
          })
          txt += '：' + sample.join('、')
          if (pts.length > SUB_DRAFT_INDEX_LIMIT || more) txt += '…'
        }
        return txt + '。元素用 (spline_index, point_index) 定位；用户说"这些点"时指它们。'
      }
      if (type === 'OTHER') {
        return (
          'Blender ' +
          String(sub.mode || '编辑模式') +
          '（对象「' +
          obj +
          '」）：' +
          String(sub.note || '子元素暂未展开，请用 Python 读当前选择集') +
          '。'
        )
      }
      return null
    }

    function selectionMessageText(payload) {
      var sel = payload && payload.selection ? payload.selection : null
      if (!sel) return null
      var kind = sel.kind === 'collection' ? 'collection' : 'objects'
      var names = Array.isArray(sel.names)
        ? sel.names.filter(function (n) {
            return typeof n === 'string' && n.length > 0
          })
        : []
      var count = typeof sel.count === 'number' ? sel.count : names.length

      if (kind === 'collection') {
        var coll =
          typeof sel.collection === 'string' && sel.collection.length > 0 ? sel.collection : null
        if (!coll) return null
        var shown = names.slice(0, SELECTION_DRAFT_NAME_LIMIT)
        var head = 'Blender 集合：' + coll + '（' + count + ' 件'
        if (shown.length) {
          head += '，前 ' + shown.length + ' 件：' + shown.join('、')
          if (sel.truncated || count > shown.length || names.length > shown.length) head += '…'
        }
        head +=
          "）。集合成员用 bpy.data.collections['" +
          coll +
          '\'].objects 取；用户说"这个集合/这批"时指它。'
        return head
      }

      var subText = subSelectionText(sel)
      if (subText) return subText
      if (!names.length) return null
      var drawn = names.slice(0, SELECTION_DRAFT_NAME_LIMIT)
      var text = 'Blender 选中：' + drawn.join('、')
      if (sel.truncated || count > drawn.length || names.length > drawn.length) {
        text += '（共 ' + count + ' 件，只列前 ' + drawn.length + ' 件）'
      }
      text += '。'
      if (sel.active) text += '活动对象：' + sel.active + '。'
      if (sel.mode) text += '模式：' + sel.mode + '。'
      if (Array.isArray(sel.collections) && sel.collections.length && count > 1) {
        var summary = sel.collections.slice(0, 4).map(function (c) {
          return String(c.name) + '(' + Number(c.count || 0) + ')'
        })
        text += '涉及集合：' + summary.join('、') + '。'
      }
      return text + '用户说"这个/这些"时指它们；对象名在 bpy.data.objects 里可直接用。'
    }

    /**
     * 把选中写进当前会话草稿（**不自动发送**）。
     * 优先 inputActions.insertText（可追加），否则退回 SessionInput.setDraft（仅当草稿为空）。
     * 成功不弹提示（草稿文本可见）；失败由调用方用 notifyError 报 error 级 Toast。
     * @returns {Promise<{ok:boolean, how?:string, reason?:string, text?:string}>}
     */
    async function applySelectionToDraft(deps, payload) {
      var text = selectionMessageText(payload)
      if (!text) return { ok: false, reason: '没有对象名可插入（空选中）' }
      var sessionId = deps && deps.sessionId
      var actions = deps && deps.inputActions
      var fallback = deps && deps.fallbackInput
      if (!sessionId) return { ok: false, reason: '未取得 sessionId —— 不能写入草稿', text: text }
      if (!actions && !fallback)
        return { ok: false, reason: '未取得 inputActions —— 无法写入草稿', text: text }
      if (actions) {
        var wrote = false
        for (var attempt = 0; attempt < 2 && !wrote; attempt++) {
          try {
            wrote = actions.insertText(text, actions.captureInsertion()) === true
          } catch (e) {
            wrote = false
          }
        }
        if (!wrote) return { ok: false, reason: '文本插入被拒（草稿正在变或已锁定）', text: text }
        return { ok: true, how: 'actions', text: text }
      }
      // 备用通道只有 setDraft（整体替换）→ 只在"读得到且为空"时才写，绝不覆盖用户内容
      var info = readDraftInfo(fallback)
      if (!info.readable) return { ok: false, reason: '备用通道读不到当前草稿', text: text }
      if (info.draft.length > 0)
        return { ok: false, reason: '草稿已有内容，备用通道不覆盖', text: text }
      try {
        fallback.setDraft(text)
      } catch (e) {
        return { ok: false, reason: '文本写入失败：' + errText(e), text: text }
      }
      return { ok: true, how: 'fallback', text: text }
    }

    /**
     * 造「Blender 选中 → 草稿」的无头桥组件。
     * 注册在 `conversation.input.dock`（session 作用域、常驻挂载、渲染 null）：
     * 面板收起来也照常接收 Blender 端按钮推来的选中。
     */
    function makeSelectionBridge() {
      function BlenderSelectionBridge(props) {
        var p = props || {}
        var sessionId =
          p.sessionId !== undefined && p.sessionId !== null ? p.sessionId : p.blenderSessionId
        var inputActions = p.inputActions || null
        var fallbackInput = p.blenderInput || null
        var lastSeqRef = React.useRef(0)
        var busyRef = React.useRef(false)
        var failCountRef = React.useRef(0)
        var sessionRef = React.useRef(sessionId)
        var actionsRef = React.useRef(inputActions)
        var fallbackRef = React.useRef(fallbackInput)
        sessionRef.current = sessionId
        actionsRef.current = inputActions
        fallbackRef.current = fallbackInput

        React.useEffect(function () {
          var alive = true
          var timer = 0
          function schedule() {
            if (!alive) return
            timer = window.setTimeout(tick, SELECTION_POLL_MS)
          }
          async function tick() {
            try {
              // 不按页面可见性跳过：用户可能在 Blender 里点了按钮、DSH 标签页在后台；
              // 选中仍应进草稿（否则回到 DSH 时发现什么都没收到）。轮询本身很便宜（零 Blender 往返）。
              if (busyRef.current || !sessionRef.current) {
                schedule()
                return
              }
              var res = await fetchJSON(backend() + '/host/selection/latest', { cache: 'no-store' })
              var body = res && res.body
              if (alive && body && body.ok === true && body.unconsumed === true && body.selection) {
                var seq = Number(body.seq) || 0
                if (seq > lastSeqRef.current) {
                  lastSeqRef.current = seq
                  busyRef.current = true
                  var out = { ok: false, reason: 'bridge_error' }
                  try {
                    out = await applySelectionToDraft(
                      {
                        sessionId: sessionRef.current,
                        inputActions: actionsRef.current,
                        fallbackInput: fallbackRef.current,
                      },
                      { selection: body.selection },
                    )
                  } catch (e) {
                    out = { ok: false, reason: 'bridge_exception:' + errText(e) }
                  }
                  if (out.ok === true) {
                    failCountRef.current = 0
                    try {
                      var consumedOk = await postJSON(backend() + '/host/selection/consume', {
                        seq: seq,
                        ok: true,
                        how: out.how || 'actions',
                      })
                      void consumedOk
                    } catch (e) {
                      /* 本地 lastSeq 已挡住重复插入，下次再消费 */
                    }
                    busyRef.current = false
                  } else {
                    // 插入失败：**不消费**，下一 tick 重试（会话/草稿锁常在几百 ms 内恢复）。
                    // 连续失败 3 次才消费掉并在回执里记下原因，避免无限重试。
                    failCountRef.current += 1
                    if (failCountRef.current === 1) {
                      notifyError(
                        { fallbackInput: fallbackRef.current },
                        'Blender 选中未写入草稿：' + String(out.reason || 'unknown'),
                      )
                    }
                    if (failCountRef.current >= 3) {
                      try {
                        var consumedFail = await postJSON(backend() + '/host/selection/consume', {
                          seq: seq,
                          ok: false,
                          reason: String(out.reason || 'unknown'),
                        })
                        void consumedFail
                      } catch (e) {
                        /* ignore */
                      }
                      failCountRef.current = 0
                    } else {
                      lastSeqRef.current = seq - 1 // 允许下一 tick 重试同一 seq
                    }
                    busyRef.current = false
                  }
                }
              }
            } catch (e) {
              /* 静默：桥不能影响会话 */
            }
            schedule()
          }
          tick()
          return function () {
            alive = false
            if (timer) window.clearTimeout(timer)
          }
        }, [])
        return null
      }
      return BlenderSelectionBridge
    }
    //#endregion

    //#region @ 提及源（契约 §2.5）
    /** ref 前缀 → 允许的种类（`object:<name>` / `material:<name>` / `collection:<name>`）。 */
    var REF_KINDS = { object: true, material: true, collection: true }

    /**
     * 拆 ref：`object:Cube` → `{kind:"object", name:"Cube"}`；裸 `Cube` → `{kind:"object", name:"Cube"}`。
     * 裸值按 object 处理 ⇒ 契约 §2.5 里那两行 codec 写法（`'@'+ref`、`'<blender-object>'+ref+'</blender-object>'`）
     * 对裸 ref **逐字成立**，对带前缀的 ref 则给出更准确的模型文本。
     */
    function splitRef(ref) {
      var s = String(ref === null || ref === undefined ? '' : ref)
      var i = s.indexOf(':')
      if (i > 0) {
        var kind = s.slice(0, i)
        if (REF_KINDS[kind] === true) return { kind: kind, name: s.slice(i + 1) }
      }
      return { kind: 'object', name: s }
    }

    /**
     * `@` 候选数据源：`GET /host/scene-items?q=<query>`。
     * 按 对象 / 材质 / 集合 用 `section` 分组（候选**没有**缩略图字段，契约 §2.5）。
     * **失败必须返回 `[]` 且不抛** —— 抛了会污染输入框。
     */
    async function sceneItemCandidates(req) {
      try {
        var q = req && typeof req.query === 'string' ? req.query : ''
        var r = await fetchJSON(backend() + '/host/scene-items?q=' + encodeURIComponent(q))
        if (!r.ok || !r.body || r.body.ok !== true) return []
        var out = []
        // [响应字段, section 名, ref 前缀, icon]
        var groups = [
          ['objects', '对象', 'object', 'file'],
          ['materials', '材质', 'material', 'file'],
          ['collections', '集合', 'collection', 'folder'],
        ]
        for (var g = 0; g < groups.length; g++) {
          var list = r.body[groups[g][0]]
          if (!list || typeof list.length !== 'number') continue
          for (var i = 0; i < list.length; i++) {
            var it = list[i]
            if (!it || !it.name) continue
            var name = String(it.name)
            out.push({
              name: name,
              label: name,
              description: groups[g][0] === 'objects' ? String(it.type || 'OBJECT') : groups[g][1],
              icon: groups[g][3],
              section: groups[g][1],
              value: groups[g][2] + ':' + name,
            })
          }
        }
        return out
      } catch (e) {
        return []
      }
    }

    /** 选中一个候选 → ReferenceInsert（契约 §2.5）。 */
    function onPickItem(pick) {
      var c = pick && pick.candidate ? pick.candidate : {}
      var ref = typeof c.value === 'string' && c.value ? c.value : String(c.name || '')
      var parts = splitRef(ref)
      return {
        insert: {
          source: 'blender',
          ref: ref,
          label: parts.name,
          appearance: 'file',
          clipboardText: '@' + parts.name,
        },
      }
    }

    /** ref ↔ 文本/剪贴板投影（契约 §2.5；`serialize` 是"@提及 → 模型文本"的正式通道）。 */
    var blenderCodec = {
      clipboardText: function (ref) {
        return '@' + splitRef(ref).name
      },
      serialize: function (ref) {
        var parts = splitRef(ref)
        return Promise.resolve(
          '<blender-' + parts.kind + '>' + parts.name + '</blender-' + parts.kind + '>',
        )
      },
    }
    //#endregion

    //#region 插件入口
    /**
     * 备用通道：由框架解析出的 `sessionId` 反查会话作用域 ctx，
     * 再取该会话的 `SessionInput`（有 `setDraft`/`addAttachments`/`notify`）。
     *
     * 出处（本机运行时）：`dsh-client-ui-conversation/lib/client.js:20515-20522`
     * 的 QueueDock 同款写法 —— `const actx = ctx.sessions.scope(sessionId)` →
     * `actx.get("conversation")` → `conversation.input.for(actx)`。
     * `ctx.sessions.scope(id)` 签名见
     * `dsh-api-session-controller/lib/types/client/sessions/service.d.ts:212`。
     */
    function sessionFallback(ctx, sessionId) {
      var out = { blenderSessionId: sessionId, blenderInput: null }
      try {
        var sessions = serviceOf(ctx, 'sessions')
        var actx =
          sessions && typeof sessions.scope === 'function' ? sessions.scope(sessionId) : undefined
        if (actx && typeof actx.get === 'function') {
          var conversation = actx.get('conversation')
          if (conversation && conversation.input && typeof conversation.input.for === 'function') {
            out.blenderInput = conversation.input.for(actx)
          }
        }
      } catch (e) {
        out.blenderInput = null
      }
      return out
    }

    /**
     * cordis 插件入口。只 inject `slots`（面板必需，且是本机 coc-dice 验证过的写法）；
     * `inputTriggers` 用 `ctx.inject([...])` **懒等**，避免"输入触发器没挂载 ⇒ 面板也一起消失"。
     */
    /**
     * cordis 插件入口。只 inject `slots`；`inputTriggers` 用 `ctx.inject([...])` 懒等，
     * 避免"输入触发器没挂载 ⇒ 连原生选中桥也一起消失"。
     * v2.3：DSH 侧视口面板与 blender_rt_pick 已删除；这里只留 @ 提及源 + 原生选中桥。
     */
    function apply(ctx) {
      if (started) return
      started = true
      configuredBackend = resolveConfiguredBackend(ctx)

      var SelectionBridge = makeSelectionBridge()

      // ① @ 提及源（契约 §2.5）。inputTriggers 服务名见
      //    dsh-client-ui-input-trigger/lib/client.js:1526
      ctx.inject(['inputTriggers'], function (scope) {
        var triggers = typeof scope.get === 'function' ? scope.get('inputTriggers') : undefined
        if (!triggers || typeof triggers.registerSource !== 'function') return
        scope.effect(function () {
          return triggers.registerSource({
            trigger: '@',
            name: 'blender',
            candidates: function (session, req) {
              return sceneItemCandidates(req)
            },
            onPick: onPickItem,
            codec: blenderCodec,
          })
        }, 'dsh-blender-plugin: @ blender scene items')
      })

      // ② v2.3：Blender 原生选中 → 当前会话草稿（无头 dock；不渲染任何 UI）
      ctx.slots.inject('conversation.input.dock', function () {
        return ctx.slots.register(
          {
            name: 'conversation.input.dock',
            id: 'blender-selection-bridge',
            order: 90,
            inject: function (sessionId) {
              return sessionFallback(ctx, sessionId)
            },
          },
          SelectionBridge,
        )
      })

      // ③ 卸载时复位守卫（便于热重载 / 重新 apply）
      ctx.effect(function () {
        return function () {
          started = false
        }
      }, 'dsh-blender-plugin: reset apply guard')
    }

    var inject = ['slots']

    exports.apply = apply
    exports.inject = inject
    // 仅供离线自检/探针断言纯函数（DSH 只读 apply/inject，未知导出无副作用）
    exports.__selftest = {
      DEFAULT_BACKEND: DEFAULT_BACKEND,
      LS_BACKEND_KEY: LS_BACKEND_KEY,
      splitRef: splitRef,
      onPickItem: onPickItem,
      blenderCodec: blenderCodec,
      sceneItemCandidates: sceneItemCandidates,
      stripTrailingSlashes: stripTrailingSlashes,
      // v2.3（Blender 原生选中 → DSH）
      SELECTION_POLL_MS: SELECTION_POLL_MS,
      SELECTION_DRAFT_NAME_LIMIT: SELECTION_DRAFT_NAME_LIMIT,
      SUB_DRAFT_INDEX_LIMIT: SUB_DRAFT_INDEX_LIMIT,
      selectionMessageText: selectionMessageText,
      subSelectionText: subSelectionText,
      applySelectionToDraft: applySelectionToDraft,
      notifyError: notifyError,
      makeSelectionBridge: makeSelectionBridge,
    }
    return module.exports
  },
})
