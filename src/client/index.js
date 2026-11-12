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
 * 现保留三块功能（DSH 侧视口面板与 blender_rt_pick 已于 v2.3 删除）：
 *   ① `@` 提及源 —— `GET /host/scene-items?q=` 按 对象/材质/集合 分组；
 *   ② Blender 原生选中 → 草稿 —— 无头桥（`conversation.input.dock`）轮询
 *      `GET /host/selection/latest`，把 addon 推来的选中插入当前会话草稿（**不自动发送**）；
 *   ③ Agent presence —— 注册右侧栏 tab 类型与引导页入口，展示各 agent 最近观察位置。
 *
 * 纪律：
 *   - 不得 `import`/`export` 语法、不得顶层 `await`、不得引用 Node 内置模块；
 *     可 `require("react")`（baseline 提供，core client-ui 包同样用法）。
 *   - 所有 fetch 失败**静默降级**；`@` 候选失败返回 `[]` **不抛**。
 *   - 选中插入**不自动发送**，只填草稿。
 *   - Gateway 地址默认 http://127.0.0.1:39877，可被 ctx 配置 / localStorage 覆盖。
 */
window.__ModuleLoader__.load({
  id: 'dsh-blender-plugin',
  factory: (require) => {
    var module = { exports: {} }
    var exports = module.exports
    Object.defineProperty(exports, Symbol.toStringTag, { value: 'Module' })
    var React = require('react')

    //#region 配置
    /** Gateway 默认地址（契约 §2.4：Gateway 127.0.0.1:39877）。 */
    var DEFAULT_BACKEND = 'http://127.0.0.1:39877'
    /** localStorage 覆盖键（最高优先级的用户侧覆盖）。 */
    var LS_BACKEND_KEY = 'dsh-blender-plugin:backend'
    /** Presence panel identity: also the right-sidebar tab type id and keyed slot key. */
    var PRESENCE_PANEL_ID = 'blender-presence'
    /** Right-sidebar tab kind opened by the guide card and /blender-presence. */
    var PRESENCE_TAB_KIND = 'blender-presence'
    /** Presence panel refresh while it is visible (human list, not live telemetry). */
    var PRESENCE_POLL_MS = 2000
    /** virtual-gui agent rows get one fixed color + tag; user-gui keeps its agent color. */
    var VIRTUAL_GUI_COLOR = '#A855F7'
    /** Plugins page item id for the Blender settings page. */
    var PLUGIN_CONFIG_ID = 'blender'
    var SELECTION_POLL_MS = 100 // v2.6.1 性能实测：1200→100ms，端到端 p50 993→156ms（本机回环，见契约「选中轮询」）
    /** v2.3：草稿文本里最多列多少个对象名（几百个零件时收敛成摘要，别刷屏）。 */
    var SELECTION_DRAFT_NAME_LIMIT = 24
    /** v2.4：子元素（顶点/边/面/骨骼）草稿里最多列多少个索引/名字。 */
    var SUB_DRAFT_INDEX_LIMIT = 12

    /** apply() 期间由 ctx 配置解析出的地址；backend() 在它之上叠 localStorage。 */
    var configuredBackend = DEFAULT_BACKEND
    /** DSH 认证 RPC（仅人类浏览器可达）；未挂载时为 null，面板开关随之禁用。 */
    var presenceRpc = null
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
      var body
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

    //#region Agent presence panel（三维视口外的人员/观察列表）
    function formatPresenceTime(ms) {
      var n = Number(ms || 0)
      if (!n) return '—'
      try {
        return new Date(n).toLocaleTimeString()
      } catch (e) {
        return '—'
      }
    }

    function presenceRowsFromState(body) {
      var presence = body && typeof body === 'object' ? body.presence : null
      var sessions = presence && Array.isArray(presence.sessions) ? presence.sessions : []
      var rows = []
      for (var i = 0; i < sessions.length; i++) {
        var session = sessions[i]
        if (!session || typeof session !== 'object') continue
        var agents = Array.isArray(session.agents) ? session.agents : []
        for (var j = 0; j < agents.length; j++) {
          var agent = agents[j]
          if (!agent || typeof agent !== 'object') continue
          rows.push({
            sessionId: String(session.id || ''),
            sessionKind: String(session.kind || ''),
            isVirtualGui: String(session.kind || '') === 'virtual-gui',
            instanceId: String(session.instanceId || ''),
            shortId: String(agent.shortId || ''),
            id: String(agent.id || ''),
            displayName: String(agent.displayName || 'agent'),
            color: String(agent.color || '#4C8DFF'),
            activity: String(agent.lastActivity || agent.activity || ''),
            lastActivityAt: Number(agent.lastActivityAt || agent.lastSeen || 0),
            viewpoint:
              agent.view && typeof agent.view === 'object'
                ? String(agent.view.viewpoint || '')
                : '',
            observation: agent.view ? 'current' : 'none',
          })
        }
      }
      return rows
    }

    function PresenceDot(props) {
      return React.createElement('span', {
        style: {
          display: 'inline-block',
          width: 8,
          height: 8,
          marginRight: 8,
          borderRadius: 999,
          background: props.color || '#4C8DFF',
          flex: '0 0 auto',
        },
      })
    }

    function PresencePanelIcon(props) {
      var size = props && props.size ? props.size : 18
      return React.createElement(
        'svg',
        { width: size, height: size, viewBox: '0 0 24 24', fill: 'none', 'aria-hidden': 'true' },
        React.createElement('path', {
          d: 'M 4 8.5h3l1.4-2h7.2l1.4 2h3v9H4z',
          stroke: 'currentColor',
          strokeWidth: 1.8,
          strokeLinejoin: 'round',
        }),
        React.createElement('circle', {
          cx: 12,
          cy: 13,
          r: 3,
          stroke: 'currentColor',
          strokeWidth: 1.8,
        }),
      )
    }

    /** Read the GUI counts from /host/state; older Gateways only send guiConnections. */
    function virtualGuiCounts(body) {
      var vg = body && body.virtualGui && typeof body.virtualGui === 'object' ? body.virtualGui : {}
      var userGui = Number(vg.userGui || 0)
      var virtualGui = Number(vg.virtualGui || 0)
      var total = Number(vg.total || vg.guiConnections || 0)
      if (!total) total = userGui + virtualGui
      // Older Gateways only counted user-gui connections under guiConnections.
      if (total && !userGui && !virtualGui) userGui = total
      return {
        total: total,
        userGui: userGui,
        virtualGui: virtualGui,
        enabled: vg.enabled !== false,
        ready: vg.ready === true,
        missing: Array.isArray(vg.missingRequired)
          ? vg.missingRequired
          : Array.isArray(vg.missing)
            ? vg.missing
            : [],
        reason: vg.reason ? String(vg.reason) : '',
      }
    }

    function planMissing(plan) {
      return !!plan && plan.ok !== true
    }

    function requiredPlanIds(capability) {
      var ids = []
      var rows =
        capability && Array.isArray(capability.prerequisites) ? capability.prerequisites : []
      for (var i = 0; i < rows.length; i++) {
        if (rows[i] && rows[i].optional !== true && rows[i].id) ids.push(String(rows[i].id))
      }
      return ids
    }

    function missingVirtualGuiPlans(capability, plans) {
      var missing = []
      var reported = capability && Array.isArray(capability.missing) ? capability.missing : []
      var required = requiredPlanIds(capability)
      for (var i = 0; i < (plans || []).length; i++) {
        var plan = plans[i]
        if (!planMissing(plan)) continue
        var id = String(plan.id || '')
        if (required.length) {
          if (required.indexOf(id) >= 0) missing.push(plan)
          continue
        }
        if (reported.length && reported.indexOf(id) >= 0) missing.push(plan)
      }
      return missing
    }

    function virtualGuiCapabilityText(capability) {
      if (!capability) return ''
      if (!capability.enabled)
        return '后端能力门未开启（features.virtualGui=' + String(capability.feature || 'off') + '）'
      if (capability.provider_implemented === false) return '当前平台还没有 virtual-gui provider'
      if (capability.ready) return '依赖已就绪'
      return capability.reason ? String(capability.reason) : '依赖尚未就绪'
    }

    function ConfigButton(props) {
      return React.createElement(
        'button',
        {
          type: 'button',
          disabled: !!props.disabled,
          onClick: props.onClick,
          style: {
            padding: '4px 10px',
            borderRadius: 6,
            border: '1px solid var(--dsh-border, rgba(127,127,127,.3))',
            background: props.primary ? 'var(--dsh-accent, #4C8DFF)' : 'transparent',
            color: props.primary ? '#fff' : 'var(--dsh-text, inherit)',
            cursor: props.disabled ? 'default' : 'pointer',
            opacity: props.disabled ? 0.55 : 1,
            fontSize: 12,
          },
        },
        props.children,
      )
    }

    function ConfigNotice(props) {
      var warning = props.kind === 'warning'
      return React.createElement(
        'div',
        {
          style: {
            padding: '8px 10px',
            margin: '8px 0',
            borderRadius: 6,
            fontSize: 12,
            lineHeight: 1.5,
            color: warning ? '#9a6700' : 'var(--dsh-text-secondary, #888)',
            background: warning ? 'rgba(210,153,34,.12)' : 'rgba(127,127,127,.08)',
            border: warning ? '1px solid rgba(210,153,34,.35)' : '1px solid rgba(127,127,127,.18)',
          },
        },
        props.children,
      )
    }

    function BlenderPluginConfigSummary() {
      return React.createElement(
        'div',
        { style: { fontSize: 12, color: 'var(--dsh-text-secondary, #888)' } },
        '管理 virtual-gui 的运行依赖与 agent 启动许可，配置实验性 DNN 视觉检测。',
      )
    }

    function virtualGuiRpc(payload) {
      if (!presenceRpc) {
        return Promise.resolve({
          ok: false,
          error: { code: 'rpc_unavailable', message: 'DSH 连接服务不可用' },
        })
      }
      return presenceRpc('/dsh-blender', 'virtual-gui', payload)
    }

    function applyVirtualGuiStatus(setView, value) {
      if (!value) return
      setView(function (prev) {
        return Object.assign({}, prev, {
          loading: false,
          error: null,
          permission: value.permission || prev.permission,
          capability: value.capability || null,
          plans: Array.isArray(value.plans) ? value.plans : [],
          install: value.install || null,
        })
      })
    }

    function showVirtualGuiError(setView, message) {
      setView(function (prev) {
        return Object.assign({}, prev, { loading: false, error: String(message || '') })
      })
    }

    function useVirtualGuiConfigCore() {
      var initialPermission = { allowed: false, source: 'config', updatedAt: null }
      var viewState = React.useState({
        loading: true,
        error: null,
        permission: initialPermission,
        capability: null,
        plans: [],
        install: null,
      })
      var view = viewState[0]
      var setView = viewState[1]
      var promptState = React.useState(null)
      var prompt = promptState[0]
      var setPrompt = promptState[1]
      var busyState = React.useState(false)
      var busy = busyState[0]
      var setBusy = busyState[1]
      var aliveRef = React.useRef(true)
      var pendingEnableRef = React.useRef(false)

      function writePermission(next) {
        if (!presenceRpc) return Promise.reject(new Error('DSH 连接服务不可用'))
        return presenceRpc('/dsh-blender', 'permission', { op: 'set', allow: next === true }).then(
          function (res) {
            if (!res || !res.ok || !res.value) {
              throw new Error(
                (res && res.error && res.error.message) || '无法更新 virtual-gui 权限',
              )
            }
            setView(function (prev) {
              return Object.assign({}, prev, { permission: res.value })
            })
            return res.value
          },
        )
      }

      function loadStatus() {
        setView(function (prev) {
          return Object.assign({}, prev, { loading: true, error: null })
        })
        return virtualGuiRpc({ op: 'status' })
          .then(function (res) {
            if (!aliveRef.current) return
            if (!res || !res.ok || !res.value) {
              showVirtualGuiError(
                setView,
                (res && res.error && res.error.message) || '无法读取 virtual-gui 状态',
              )
              return
            }
            applyVirtualGuiStatus(setView, res.value)
          })
          .catch(function (e) {
            if (aliveRef.current) showVirtualGuiError(setView, errText(e))
          })
      }

      React.useEffect(function () {
        aliveRef.current = true
        loadStatus()
        return function () {
          aliveRef.current = false
        }
      }, [])

      return {
        view: view,
        setView: setView,
        prompt: prompt,
        setPrompt: setPrompt,
        busy: busy,
        setBusy: setBusy,
        aliveRef: aliveRef,
        pendingEnableRef: pendingEnableRef,
        writePermission: writePermission,
        loadStatus: loadStatus,
      }
    }

    function syncVirtualGuiPermission(core, value, enableAfter) {
      var capability = value && value.capability ? value.capability : null
      var plans = value && Array.isArray(value.plans) ? value.plans : []
      var missing = missingVirtualGuiPlans(capability, plans)
      if (!capability || capability.ready !== true || missing.length) {
        if (missing.length) {
          core.setPrompt({
            mode: 'missing',
            plans: missing,
            enableAfter: !!enableAfter,
            title: '依赖仍未就绪',
          })
        } else {
          showVirtualGuiError(
            core.setView,
            'virtual-gui 尚未就绪：' + virtualGuiCapabilityText(capability),
          )
        }
        core.setBusy(false)
        return false
      }
      if (enableAfter) {
        core.pendingEnableRef.current = false
        return core
          .writePermission(true)
          .then(function () {
            core.setPrompt(null)
            core.setBusy(false)
            return true
          })
          .catch(function (e) {
            showVirtualGuiError(core.setView, errText(e))
            core.setBusy(false)
            return false
          })
      }
      core.setPrompt(null)
      core.setBusy(false)
      return true
    }

    function finishVirtualGuiInstall(core, next) {
      if (!next || next.state === 'installing') return
      if (next.state === 'failed') {
        var failedPlan = null
        for (var i = 0; i < core.view.plans.length; i++) {
          if (core.view.plans[i] && core.view.plans[i].id === next.planId) {
            failedPlan = core.view.plans[i]
            break
          }
        }
        core.setBusy(false)
        core.setPrompt({
          mode: 'failed',
          plans: failedPlan ? [failedPlan] : [],
          title: '安装失败',
          detail: next.detail || '安装命令没有成功退出',
          enableAfter: core.pendingEnableRef.current,
        })
        return
      }
      core.setBusy(true)
      virtualGuiRpc({ op: 'status' })
        .then(function (res) {
          if (!core.aliveRef.current) return
          if (!res || !res.ok || !res.value) {
            showVirtualGuiError(
              core.setView,
              (res && res.error && res.error.message) || '安装后无法重新检测依赖',
            )
            core.setBusy(false)
            return
          }
          applyVirtualGuiStatus(core.setView, res.value)
          syncVirtualGuiPermission(core, res.value, core.pendingEnableRef.current)
        })
        .catch(function (e) {
          if (core.aliveRef.current) showVirtualGuiError(core.setView, errText(e))
          core.setBusy(false)
        })
    }

    function requestVirtualGuiInstall(core, plan, enableAfter) {
      if (!plan || !presenceRpc || core.busy) return
      core.pendingEnableRef.current = enableAfter === true
      core.setBusy(true)
      core.setPrompt(null)
      virtualGuiRpc({ op: 'install', planId: plan.id })
        .then(function (res) {
          if (!core.aliveRef.current) return
          if (!res || !res.ok) {
            var error = (res && res.error) || {}
            if (error.code === 'manual_install_required') {
              core.setPrompt({
                mode: 'manual',
                plans: [plan],
                title: '需要手动安装',
                detail: error.message || '',
                command: (error.details && error.details.command) || plan.command || '',
                steps: (error.details && error.details.steps) || plan.steps || [],
                enableAfter: enableAfter === true,
              })
            } else {
              showVirtualGuiError(core.setView, error.message || '无法启动安装')
            }
            core.setBusy(false)
            return
          }
          core.setView(function (prev) {
            return Object.assign({}, prev, {
              install: (res.value && res.value.install) || null,
            })
          })
          core.setBusy(false)
        })
        .catch(function (e) {
          if (core.aliveRef.current) showVirtualGuiError(core.setView, errText(e))
          core.setBusy(false)
        })
    }

    function toggleVirtualGuiPermission(core, next) {
      if (!presenceRpc || core.busy) return
      if (!next) {
        core.setBusy(true)
        core
          .writePermission(false)
          .then(function () {
            core.setPrompt(null)
            core.setBusy(false)
          })
          .catch(function (e) {
            showVirtualGuiError(core.setView, errText(e))
            core.setBusy(false)
          })
        return
      }
      core.setBusy(true)
      virtualGuiRpc({ op: 'status' })
        .then(function (res) {
          if (!core.aliveRef.current) return
          if (!res || !res.ok || !res.value) {
            showVirtualGuiError(
              core.setView,
              (res && res.error && res.error.message) || '无法检测依赖',
            )
            core.setBusy(false)
            return
          }
          applyVirtualGuiStatus(core.setView, res.value)
          var capability = res.value.capability || null
          var plans = Array.isArray(res.value.plans) ? res.value.plans : []
          var missing = missingVirtualGuiPlans(capability, plans)
          if (missing.length) {
            core.pendingEnableRef.current = true
            core.setPrompt({
              mode: 'missing',
              plans: missing,
              enableAfter: true,
              title: '缺少 virtual-gui 依赖',
            })
            core.setBusy(false)
            return
          }
          if (!capability || capability.ready !== true) {
            showVirtualGuiError(
              core.setView,
              'virtual-gui 尚未就绪：' + virtualGuiCapabilityText(capability),
            )
            core.setBusy(false)
            return
          }
          core.pendingEnableRef.current = false
          core
            .writePermission(true)
            .then(function () {
              core.setBusy(false)
            })
            .catch(function (e) {
              showVirtualGuiError(core.setView, errText(e))
              core.setBusy(false)
            })
        })
        .catch(function (e) {
          if (core.aliveRef.current) showVirtualGuiError(core.setView, errText(e))
          core.setBusy(false)
        })
    }

    function recheckVirtualGuiAfterManual(core) {
      core.setBusy(true)
      virtualGuiRpc({ op: 'status' })
        .then(function (res) {
          if (!core.aliveRef.current) return
          if (!res || !res.ok || !res.value) {
            showVirtualGuiError(
              core.setView,
              (res && res.error && res.error.message) || '无法重新检测依赖',
            )
            core.setBusy(false)
            return
          }
          applyVirtualGuiStatus(core.setView, res.value)
          syncVirtualGuiPermission(core, res.value, core.pendingEnableRef.current)
        })
        .catch(function (e) {
          if (core.aliveRef.current) showVirtualGuiError(core.setView, errText(e))
          core.setBusy(false)
        })
    }

    function useVirtualGuiInstallPolling(core, installState) {
      React.useEffect(
        function () {
          if (installState !== 'installing') return
          var alive = true
          var timer = window.setInterval(function () {
            virtualGuiRpc({ op: 'install-status' })
              .then(function (res) {
                if (!alive || !res || !res.ok || !res.value || !res.value.install) return
                var next = res.value.install
                core.setView(function (prev) {
                  return Object.assign({}, prev, { install: next })
                })
                if (next.state === 'done' || next.state === 'failed') {
                  window.clearInterval(timer)
                  finishVirtualGuiInstall(core, next)
                }
              })
              .catch(function () {
                /* transient RPC failure: keep polling until the page closes */
              })
          }, 1000)
          return function () {
            alive = false
            window.clearInterval(timer)
          }
        },
        [installState],
      )
    }

    function ConfigHeader() {
      return React.createElement(
        'div',
        { style: { marginBottom: 8 } },
        React.createElement(
          'div',
          { style: { fontSize: 15, fontWeight: 600 } },
          'Blender virtual-gui',
        ),
        React.createElement(
          'div',
          { style: { marginTop: 2, fontSize: 12, color: 'var(--dsh-text-secondary, #888)' } },
          '启用时检测运行依赖；缺依赖先询问是否安装，安装期间保持显示进度。',
        ),
      )
    }

    function ConfigSwitch(props) {
      var dev = props.busy || props.installState === 'installing'
      return React.createElement(
        'div',
        null,
        React.createElement(
          'label',
          {
            style: {
              display: 'flex',
              alignItems: 'center',
              gap: 8,
              padding: '10px 0',
              fontSize: 13,
              cursor: dev ? 'default' : 'pointer',
            },
          },
          React.createElement('input', {
            type: 'checkbox',
            checked: props.permission.allowed === true,
            disabled: !presenceRpc || dev,
            onChange: function (e) {
              props.onToggle(!!(e && e.target && e.target.checked))
            },
          }),
          React.createElement('span', null, '允许 agent 启动 virtual-gui（本机虚拟 Blender 窗口）'),
        ),
        React.createElement(
          'div',
          {
            style: {
              marginLeft: 24,
              marginBottom: 10,
              fontSize: 11,
              color: 'var(--dsh-text-secondary, #888)',
            },
          },
          props.permission.allowed
            ? '当前已允许；开关只影响当前 DSH 进程，插件重载后回到 allowVirtualGui 配置值。'
            : '当前不允许；打开时先检测依赖，缺依赖会弹窗询问，拒绝安装则保持关闭。',
        ),
      )
    }

    function ConfigMissingBanner(props) {
      var installable = null
      for (var i = 0; i < props.missing.length; i++) {
        if (props.missing[i].supported === true && props.missing[i].oneClick) {
          installable = props.missing[i]
          break
        }
      }
      return React.createElement(
        ConfigNotice,
        { kind: 'warning' },
        React.createElement(
          'div',
          null,
          'virtual-gui 正开着，但依赖已缺失：' +
            props.missing
              .map(function (plan) {
                return plan.label || plan.id
              })
              .join('、') +
            '。',
        ),
        React.createElement(
          'div',
          { style: { display: 'flex', gap: 8, marginTop: 8, flexWrap: 'wrap' } },
          installable
            ? React.createElement(
                ConfigButton,
                {
                  primary: true,
                  onClick: function () {
                    props.onInstall(installable, false)
                  },
                },
                '安装缺失依赖',
              )
            : null,
          React.createElement(ConfigButton, { onClick: props.onDisable }, '关闭 virtual-gui'),
        ),
      )
    }

    function ConfigCapabilityStatus(props) {
      var capability = props.capability
      if (!capability) return null
      if (!capability.enabled || capability.provider_implemented === false) {
        return React.createElement(
          ConfigNotice,
          { kind: 'warning' },
          virtualGuiCapabilityText(capability),
          capability.fix ? React.createElement('div', null, '修复：' + capability.fix) : null,
        )
      }
      if (capability.ready) {
        return React.createElement(
          'div',
          { style: { fontSize: 12, color: '#2da44e', margin: '8px 0' } },
          '运行依赖已就绪。',
        )
      }
      return React.createElement(
        ConfigNotice,
        { kind: 'warning' },
        virtualGuiCapabilityText(capability),
      )
    }

    function ConfigPrerequisiteRow(props) {
      var prereq = props.prereq
      var plan = props.plan
      var ok = prereq.ok === true || (plan && plan.ok === true)
      var optional = prereq.optional === true
      return React.createElement(
        'div',
        {
          style: {
            display: 'flex',
            alignItems: 'flex-start',
            gap: 8,
            padding: '7px 0',
            borderTop: '1px solid var(--dsh-border, rgba(127,127,127,.12))',
            fontSize: 12,
          },
        },
        React.createElement(
          'div',
          { style: { flex: '1 1 auto', minWidth: 0 } },
          React.createElement(
            'div',
            { style: { fontWeight: 500 } },
            String(prereq.label || prereq.id) + (optional ? '（可选）' : ''),
          ),
          React.createElement(
            'div',
            { style: { marginTop: 2, color: 'var(--dsh-text-secondary, #888)' } },
            String(prereq.detail || prereq.check || ''),
          ),
          !ok && plan && plan.command
            ? React.createElement(
                'code',
                {
                  style: {
                    display: 'block',
                    marginTop: 4,
                    whiteSpace: 'pre-wrap',
                    wordBreak: 'break-all',
                    fontSize: 11,
                  },
                },
                plan.command,
              )
            : null,
        ),
        React.createElement(
          'span',
          {
            style: {
              flex: '0 0 auto',
              color: ok ? '#2da44e' : optional ? '#9a6700' : '#d33',
            },
          },
          ok ? '已就绪' : optional ? '可选缺失' : '缺失',
        ),
        !ok && plan && plan.supported && plan.oneClick
          ? React.createElement(
              ConfigButton,
              {
                onClick: function () {
                  props.onInstall(plan, false)
                },
              },
              '安装',
            )
          : null,
      )
    }

    function ConfigPrerequisites(props) {
      var capability = props.capability
      var prerequisites = Array.isArray(capability.prerequisites) ? capability.prerequisites : []
      if (!prerequisites.length) return null
      var rows = []
      for (var i = 0; i < prerequisites.length; i++) {
        var prereq = prerequisites[i]
        if (!prereq || !prereq.id) continue
        var plan = null
        for (var j = 0; j < props.plans.length; j++) {
          if (props.plans[j] && props.plans[j].id === prereq.id) {
            plan = props.plans[j]
            break
          }
        }
        rows.push(
          React.createElement(ConfigPrerequisiteRow, {
            key: 'prereq-' + prereq.id,
            prereq: prereq,
            plan: plan,
            onInstall: props.onInstall,
          }),
        )
      }
      return React.createElement(
        'div',
        { style: { marginTop: 6 } },
        React.createElement(
          'div',
          { style: { fontSize: 12, fontWeight: 600, marginBottom: 2 } },
          '运行依赖',
        ),
        rows,
      )
    }

    function ConfigActions(props) {
      return React.createElement(
        'div',
        { style: { display: 'flex', gap: 8, marginTop: 12 } },
        React.createElement(
          ConfigButton,
          {
            disabled: props.busy || props.installState === 'installing',
            onClick: props.onRecheck,
          },
          props.installState === 'installing' ? '安装中…' : '重新检测',
        ),
      )
    }

    function ConfigBody(props) {
      var core = props.core
      var view = core.view
      var capability = view.capability
      var children = [React.createElement(ConfigHeader, { key: 'header' })]
      if (view.error) {
        children.push(
          React.createElement(
            'div',
            { key: 'error', style: { color: '#d33', fontSize: 12, margin: '8px 0' } },
            view.error,
          ),
        )
      }
      children.push(
        React.createElement(ConfigSwitch, {
          key: 'switch',
          permission: view.permission,
          busy: core.busy,
          installState: props.installState,
          onToggle: props.onToggle,
        }),
      )
      if (view.permission.allowed && props.missing.length) {
        children.push(
          React.createElement(ConfigMissingBanner, {
            key: 'missing-banner',
            missing: props.missing,
            onInstall: props.onInstall,
            onDisable: props.onDisable,
          }),
        )
      }
      if (capability) {
        children.push(
          React.createElement(ConfigCapabilityStatus, {
            key: 'capability',
            capability: capability,
          }),
        )
        children.push(
          React.createElement(ConfigPrerequisites, {
            key: 'prerequisites',
            capability: capability,
            plans: view.plans,
            onInstall: props.onInstall,
          }),
        )
      }
      children.push(
        React.createElement(ConfigActions, {
          key: 'actions',
          busy: core.busy,
          installState: props.installState,
          onRecheck: core.loadStatus,
        }),
      )
      // 3.4：DNN 分组独立加载状态，不依赖 virtual-gui core。
      children.push(React.createElement(DnnConfigSection, { key: 'dnn' }))
      return React.createElement('div', null, children)
    }

    //#region DNN 视觉检测（实验性，features.dnn）
    /** 固定文档地址；配置卡与 GPU-DIY.md 都指向它。 */
    var DNN_DOCS_URL =
      'https://github.com/sixtysevenlf/dsh-blender-plugin/blob/main/docs/vision-custom-backend.md'
    /** 实验性影响说明：必须点明 GPL-3.0、默认 CPU、结果不稳定、GPU 自备。 */
    var DNN_EXPERIMENTAL_TEXT =
      '实验性功能：YOLOv5 预训练模型（GPL-3.0），默认仅 CPU 推理；检测结果可能不稳定，GPU 需要自备 ONNX Runtime 环境。插件不自动安装依赖，也不在运行时联网。'
    /** 与 src/host/dnn-pins.ts 的预设顺序一致。 */
    var DNN_MODEL_IDS = ['yolov5n', 'yolov5s', 'yolov5m', 'yolov5l', 'yolov5x']
    var DNN_MODEL_LABELS = {
      yolov5n: 'yolov5n（nano，最小，默认）',
      yolov5s: 'yolov5s（small）',
      yolov5m: 'yolov5m（medium）',
      yolov5l: 'yolov5l（large）',
      yolov5x: 'yolov5x（xlarge，最大）',
    }
    var DNN_MODEL_NOTES = {
      yolov5n: '最快、精度最低，适合 CPU 冒烟',
      yolov5s: 'CPU 上较慢，精度更高',
      yolov5m: '建议 GPU / 自备 onnxruntime',
      yolov5l: '建议 GPU / 自备 onnxruntime',
      yolov5x: '建议 GPU / 自备 onnxruntime',
    }
    /** pending / switching 时的状态轮询间隔。 */
    var DNN_STATUS_POLL_MS = 1500

    function dnnModelOptions() {
      var out = []
      for (var i = 0; i < DNN_MODEL_IDS.length; i++) {
        out.push({
          id: DNN_MODEL_IDS[i],
          label: DNN_MODEL_LABELS[DNN_MODEL_IDS[i]] || DNN_MODEL_IDS[i],
          note: DNN_MODEL_NOTES[DNN_MODEL_IDS[i]] || '',
        })
      }
      return out
    }

    function dnnDefaultSelection() {
      return ['yolov5n']
    }

    /** /dsh-blender dnn 端点（owner-only）；未挂载连接服务时返回结构化失败，不抛。 */
    function dnnRpc(payload) {
      if (!presenceRpc) {
        return Promise.resolve({
          ok: false,
          error: { code: 'rpc_unavailable', message: 'DSH 连接服务不可用' },
        })
      }
      return presenceRpc('/dsh-blender', 'dnn', payload)
    }

    function dnnRpcPayload(op, extra) {
      var out = { op: op }
      if (extra) {
        for (var key in extra) {
          if (Object.prototype.hasOwnProperty.call(extra, key)) out[key] = extra[key]
        }
      }
      return out
    }

    /** 把 status 的 active（扁平）或 saved（嵌套快照）还原成配置卡表单。 */
    /** /doctor 的 active/previous 是 {revision,values}；旧/回退 payload 可能是扁平值。 */
    function dnnUnwrapValues(raw) {
      if (!raw || typeof raw !== 'object') return null
      return raw.values && typeof raw.values === 'object' ? raw.values : raw
    }

    function dnnCandidateFromStatus(status) {
      var flat = dnnUnwrapValues(status && status.config && status.config.active)
      if (!flat || typeof flat !== 'object') {
        var saved = status && status.saved
        if (saved && saved.vision && typeof saved.vision === 'object') {
          var savedDnn =
            saved.vision.dnn && typeof saved.vision.dnn === 'object' ? saved.vision.dnn : {}
          flat = {
            dnn: !!(saved.features && saved.features.dnn === true),
            python: saved.vision.python,
            venv: saved.vision.venv,
            model: saved.vision.model,
            backend: savedDnn.backend,
            device: savedDnn.device,
          }
        }
      }
      flat = flat && typeof flat === 'object' ? flat : {}
      var backend = flat.backend === 'onnxruntime' ? 'onnxruntime' : 'opencv'
      var device = flat.device === 'auto' || flat.device === 'gpu' ? flat.device : 'cpu'
      if (backend === 'opencv') device = 'cpu'
      var model =
        typeof flat.model === 'string' && flat.model.trim() !== '' ? flat.model : 'yolov5n'
      var effectiveEnabled =
        status && status.features && typeof status.features.dnn === 'boolean'
          ? status.features.dnn
          : null
      var enabled = effectiveEnabled !== null ? effectiveEnabled : flat.dnn === true
      return {
        features: { dnn: enabled },
        vision: {
          python: typeof flat.python === 'string' ? flat.python : '',
          venv: typeof flat.venv === 'string' ? flat.venv : '',
          model: model,
          dnn: { backend: backend, device: device },
        },
      }
    }

    /** 表单身份串：用来判断“下载完成后是否需要顺带 apply 待保存的表单”。 */
    function dnnCandidateSignature(candidate) {
      var vision = (candidate && candidate.vision) || {}
      var dnn = vision.dnn || {}
      return JSON.stringify([
        !!(candidate && candidate.features && candidate.features.dnn),
        String(vision.python || ''),
        String(vision.venv || ''),
        String(vision.model || ''),
        String(dnn.backend || 'opencv'),
        String(dnn.device || 'cpu'),
      ])
    }

    /**
     * 打开下载弹窗只说明“开关将被开启”，并不等于已保存。下载成功后若表单与
     * 生效配置仍有差异（典型：刚把开关打开就下载），必须顺手 apply，否则
     * “下一次 vision_detect 自动使用新模型”的文案不成立。
     */
    function dnnShouldApplyAfterDownload(form, status) {
      if (!form || dnnCandidateInvalidReason(form)) return false
      return dnnCandidateSignature(form) !== dnnCandidateSignature(dnnCandidateFromStatus(status))
    }

    /** 提交前校验；返回 null 表示合法，否则返回给用户看的原因。 */
    function dnnCandidateInvalidReason(candidate) {
      if (!candidate || !candidate.vision || typeof candidate.vision !== 'object')
        return 'DNN 配置缺失'
      var vision = candidate.vision
      var dnn = vision.dnn && typeof vision.dnn === 'object' ? vision.dnn : {}
      var backend = dnn.backend === undefined || dnn.backend === null ? 'opencv' : dnn.backend
      var device = dnn.device === undefined || dnn.device === null ? 'cpu' : dnn.device
      if (backend !== 'opencv' && backend !== 'onnxruntime')
        return 'backend 只能是 opencv 或 onnxruntime'
      if (device !== 'cpu' && device !== 'auto' && device !== 'gpu')
        return 'device 只能是 cpu / auto / gpu'
      if (backend === 'opencv' && device !== 'cpu')
        return 'backend=opencv 只支持 device=cpu；GPU 需要自备 vision.venv + backend=onnxruntime'
      var model = String(vision.model || '')
      if (!/^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/.test(model))
        return '模型 id 非法（只能是字母/数字/._-，不允许路径或 URL）'
      return null
    }

    function dnnApplyPayload(candidate) {
      return { op: 'apply', candidate: candidate }
    }

    /** 把一次编辑合并进表单（patch 是扁平的 dnn/python/venv/model/backend/device）。 */
    function dnnFormUpdate(form, patch) {
      var base = form || dnnCandidateFromStatus(null)
      patch = patch || {}
      var vision = base.vision || {}
      var baseDnn = vision.dnn || { backend: 'opencv', device: 'cpu' }
      var nextDnn = {
        backend: patch.backend !== undefined ? patch.backend : baseDnn.backend,
        device: patch.device !== undefined ? patch.device : baseDnn.device,
      }
      if (nextDnn.backend === 'opencv') nextDnn.device = 'cpu'
      var enabled =
        patch.dnn !== undefined ? patch.dnn === true : base.features && base.features.dnn === true
      return {
        features: { dnn: enabled },
        vision: {
          python: patch.python !== undefined ? String(patch.python) : String(vision.python || ''),
          venv: patch.venv !== undefined ? String(patch.venv) : String(vision.venv || ''),
          model:
            patch.model !== undefined ? String(patch.model) : String(vision.model || 'yolov5n'),
          dnn: nextDnn,
        },
      }
    }

    /** 状态行文案：idle / pending / switching / failed / env 锁定。 */
    /** env 锁定只冻结 features.dnn 开关；DIY 字段仍可编辑并保存。 */
    function dnnControlState(status, busy, connected) {
      return {
        switchDisabled: !!(status && status.envLocked) || !!busy || !connected,
        fieldDisabled: !!busy,
      }
    }

    function dnnStatusText(status) {
      if (!status) return '正在读取 DNN 状态…'
      var config = status.config && typeof status.config === 'object' ? status.config : {}
      var activeRevision = config.activeRevision
      var pendingRevision = config.pendingRevision
      if (status.envLocked)
        return 'features.dnn 已由 DSH_BLENDER_DNN 环境变量锁定；配置卡不能改写该开关。'
      if (config.phase === 'failed') {
        var err = config.lastSwitchError || {}
        var tail =
          '上次切换失败：' +
          String(err.code || 'dnn_config_switch_failed') +
          (err.message ? '（' + String(err.message) + '）' : '')
        if (pendingRevision !== null && pendingRevision !== undefined)
          tail += '；pending revision ' + String(pendingRevision) + ' 仍保留，可回滚上一版。'
        return tail
      }
      if (config.phase === 'switching') {
        var waiting =
          config.waitingFor && config.waitingFor.op
            ? '（' + String(config.waitingFor.op) + '）'
            : ''
        return (
          '切换中：active revision ' +
          String(activeRevision) +
          ' → pending revision ' +
          String(pendingRevision) +
          '，等待在途请求结束' +
          waiting +
          '…'
        )
      }
      if (config.phase === 'pending')
        return (
          '待应用：active revision ' +
          String(activeRevision) +
          ' → pending revision ' +
          String(pendingRevision) +
          '；Gateway 下一次 /doctor 或 vision_* 请求时切换。'
        )
      if (activeRevision !== null && activeRevision !== undefined)
        return (
          '已生效：revision ' +
          String(activeRevision) +
          (pendingRevision !== null && pendingRevision !== undefined
            ? '（pending ' + String(pendingRevision) + '）'
            : '') +
          '。'
        )
      return '尚未保存 DNN 运行时配置。'
    }

    function dnnDownloadPromptVisible(status) {
      if (!status || !status.features || status.features.dnn !== true) return false
      var models = status.models && typeof status.models === 'object' ? status.models : {}
      return Number(models.onnxCount) === 0
    }

    function dnnProbeSummary(result) {
      var summary =
        result && result.summary && typeof result.summary === 'object' ? result.summary : {}
      return {
        pass: Number(summary.pass) || 0,
        warn: Number(summary.warn) || 0,
        fail: Number(summary.fail) || 0,
        ok: !!(result && result.ok === true),
      }
    }

    function dnnProbeText(result) {
      if (!result) return '尚未检测'
      if (result.error) return '检测失败：' + String(result.error)
      var summary = dnnProbeSummary(result)
      if (summary.fail > 0)
        return (
          '不可用：' +
          summary.fail +
          ' 项失败' +
          (summary.warn > 0 ? '，' + summary.warn + ' 项警告' : '')
        )
      if (summary.warn > 0) return '可用，但有 ' + summary.warn + ' 项警告'
      return '可用（全部通过）'
    }

    function dnnProbeChecksText(result) {
      var checks = result && Array.isArray(result.checks) ? result.checks : []
      var lines = []
      for (var i = 0; i < checks.length; i++) {
        var check = checks[i] || {}
        var status = String(check.status || '')
        var label = status === 'pass' ? '通过' : status === 'warn' ? '警告' : '失败'
        lines.push(
          '[' +
            label +
            '] ' +
            String(check.label || check.id || '') +
            '：' +
            String(check.detail || '') +
            (check.fix ? '（修复：' + String(check.fix) + '）' : ''),
        )
      }
      return lines.join('\n')
    }

    function dnnCanRollback(status) {
      var config = status && status.config && typeof status.config === 'object' ? status.config : {}
      var revision = config.previousRevision
      if (revision === null || revision === undefined) {
        revision =
          config.previous && typeof config.previous === 'object' ? config.previous.revision : null
      }
      return Number.isSafeInteger(Number(revision)) && Number(revision) > 0
    }

    function dnnCanCancelPending(status) {
      var config = status && status.config && typeof status.config === 'object' ? status.config : {}
      return config.pendingRevision !== null && config.pendingRevision !== undefined
    }

    function dnnFormatBytes(bytes) {
      var value = Number(bytes)
      if (!Number.isFinite(value) || value <= 0) return ''
      if (value >= 1024 * 1024 * 1024) return (value / (1024 * 1024 * 1024)).toFixed(1) + ' GiB'
      if (value >= 1024 * 1024) return (value / (1024 * 1024)).toFixed(1) + ' MiB'
      if (value >= 1024) return (value / 1024).toFixed(0) + ' KiB'
      return value + ' B'
    }

    function dnnDownloadSelectionText(selection) {
      if (!selection || !selection.length) return '请至少选择一个模型'
      return '已选 ' + selection.length + ' 个模型：' + selection.join('、')
    }

    function dnnErrorText(res) {
      var error = res && res.error && typeof res.error === 'object' ? res.error : {}
      var text = error.message
        ? String(error.message)
        : error.code
          ? String(error.code)
          : 'DNN 请求失败'
      var details = error.details && typeof error.details === 'object' ? error.details : null
      if (details) {
        if (details.savedForNextStart === true) text += '（配置已保存，下次启动生效）'
        else if (details.applied === false && details.revision !== undefined)
          text += '（revision ' + String(details.revision) + ' 尚未生效）'
      }
      return text
    }

    function DnnStatusDetails(props) {
      var status = props.status
      var config = status && status.config && typeof status.config === 'object' ? status.config : {}
      var parts = []
      if (config.activeRevision !== null && config.activeRevision !== undefined)
        parts.push('active revision ' + config.activeRevision)
      if (config.pendingRevision !== null && config.pendingRevision !== undefined)
        parts.push('pending revision ' + config.pendingRevision)
      var previous = config.previousRevision
      if (
        (previous === null || previous === undefined) &&
        config.previous &&
        config.previous.revision
      )
        previous = config.previous.revision
      if (previous !== null && previous !== undefined) parts.push('上一版 revision ' + previous)
      if (config.waitingFor && config.waitingFor.op) parts.push('等待 ' + config.waitingFor.op)
      if (status && status.gateway && status.gateway.reachable === false)
        parts.push('Gateway 未运行')
      if (status && status.models && status.models.modelsDir)
        parts.push(
          '模型目录 ' +
            status.models.modelsDir +
            '（onnx ' +
            Number(status.models.onnxCount || 0) +
            '）',
        )
      if (!parts.length) return null
      return React.createElement(
        'div',
        {
          style: {
            marginTop: 4,
            fontSize: 11,
            lineHeight: 1.6,
            color: 'var(--dsh-text-secondary, #888)',
            wordBreak: 'break-all',
          },
        },
        parts.join(' · '),
      )
    }

    function DnnFeedback(props) {
      var feedback = props.feedback
      if (!feedback) return null
      var kind = feedback.kind
      var color = kind === 'error' ? '#d33' : kind === 'ok' ? '#2da44e' : '#9a6700'
      var bg =
        kind === 'error'
          ? 'rgba(221,51,51,.1)'
          : kind === 'ok'
            ? 'rgba(45,164,78,.1)'
            : 'rgba(210,153,34,.12)'
      return React.createElement(
        'div',
        {
          style: {
            margin: '8px 0',
            padding: '8px 10px',
            borderRadius: 6,
            fontSize: 12,
            lineHeight: 1.5,
            color: color,
            background: bg,
          },
        },
        String(feedback.text || ''),
      )
    }

    function DnnProbeModal(props) {
      var result = props.result || {}
      var checks = Array.isArray(result.checks) ? result.checks : []
      var rows = []
      for (var i = 0; i < checks.length; i++) {
        var check = checks[i] || {}
        var status = String(check.status || '')
        var color = status === 'pass' ? '#2da44e' : status === 'warn' ? '#9a6700' : '#d33'
        var label = status === 'pass' ? '通过' : status === 'warn' ? '警告' : '失败'
        rows.push(
          React.createElement(
            'div',
            {
              key: 'probe-' + String(check.id || i),
              style: {
                padding: '7px 0',
                borderTop: '1px solid var(--dsh-border, rgba(127,127,127,.14))',
                fontSize: 12,
              },
            },
            React.createElement(
              'div',
              { style: { display: 'flex', gap: 8, alignItems: 'baseline' } },
              React.createElement('span', { style: { color: color, fontWeight: 600 } }, label),
              React.createElement(
                'span',
                { style: { fontWeight: 500 } },
                String(check.label || check.id || ''),
              ),
              React.createElement(
                'span',
                { style: { color: 'var(--dsh-text-secondary, #888)' } },
                String(check.detail || ''),
              ),
            ),
            check.fix
              ? React.createElement(
                  'div',
                  { style: { marginTop: 2, color: '#9a6700' } },
                  '修复：' + String(check.fix),
                )
              : null,
          ),
        )
      }
      return React.createElement(
        ConfigModalShell,
        null,
        React.createElement(
          'div',
          { style: { fontWeight: 600, marginBottom: 8 } },
          'DNN 可用性检测',
        ),
        React.createElement(
          'div',
          { style: { fontSize: 12, marginBottom: 6 } },
          dnnProbeText(result),
        ),
        result.error
          ? React.createElement(
              'div',
              { style: { color: '#d33', fontSize: 12 } },
              String(result.error),
            )
          : null,
        rows,
        React.createElement(
          'div',
          { style: { display: 'flex', justifyContent: 'flex-end', marginTop: 12 } },
          React.createElement(ConfigButton, { onClick: props.onClose }, '关闭'),
        ),
      )
    }

    function DnnDownloadModal(props) {
      var rows = []
      var downloadable = Array.isArray(props.downloadable) ? props.downloadable : []
      for (var i = 0; i < DNN_MODEL_IDS.length; i++) {
        var id = DNN_MODEL_IDS[i]
        var pin = null
        for (var p = 0; p < downloadable.length; p++) {
          if (downloadable[p] && downloadable[p].id === id) {
            pin = downloadable[p]
            break
          }
        }
        var size = pin ? dnnFormatBytes(pin.bytes) : ''
        var sizeText = size ? ' · ' + size : ' · 大小未采集'
        var installed = pin && pin.installed === true ? '（已存在）' : ''
        rows.push(
          React.createElement(
            'label',
            {
              key: 'model-' + id,
              style: {
                display: 'flex',
                gap: 8,
                alignItems: 'center',
                padding: '6px 0',
                fontSize: 12,
              },
            },
            React.createElement('input', {
              type: 'checkbox',
              checked: props.selection.indexOf(id) >= 0,
              disabled: !!props.busy,
              onChange: (function (modelId) {
                return function () {
                  props.onToggle(modelId)
                }
              })(id),
            }),
            React.createElement(
              'span',
              null,
              (DNN_MODEL_LABELS[id] || id) +
                ' — ' +
                (DNN_MODEL_NOTES[id] || '') +
                sizeText +
                installed,
            ),
          ),
        )
      }
      return React.createElement(
        ConfigModalShell,
        null,
        React.createElement(
          'div',
          { style: { fontWeight: 600, marginBottom: 8 } },
          '下载 YOLOv5 预设模型',
        ),
        React.createElement(
          'div',
          { style: { fontSize: 12, lineHeight: 1.6, color: 'var(--dsh-text-secondary, #888)' } },
          '来源：ultralytics/yolov5 v7.0 GitHub Release；许可：GPL-3.0。下载写入模型目录，并补齐 coco80.txt 与 models.yml（已有同名条目不会被覆盖）。下载完成后无需重启 Gateway，下一次 vision_detect 自动使用。',
        ),
        React.createElement('div', { style: { marginTop: 8 } }, rows),
        props.error
          ? React.createElement(
              'div',
              { style: { color: '#d33', fontSize: 12, marginTop: 6 } },
              String(props.error),
            )
          : null,
        React.createElement(
          'div',
          { style: { fontSize: 11, color: 'var(--dsh-text-secondary, #888)', marginTop: 6 } },
          '取消只会关闭本弹窗：features.dnn 保持开启，警告和下载入口仍会保留。',
        ),
        React.createElement(
          'div',
          { style: { display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 12 } },
          React.createElement(
            ConfigButton,
            { disabled: !!props.busy, onClick: props.onCancel },
            '取消',
          ),
          React.createElement(
            ConfigButton,
            {
              primary: true,
              disabled: !!props.busy || !props.selection.length,
              onClick: props.onConfirm,
            },
            props.busy ? '下载中…' : '开始下载',
          ),
        ),
      )
    }

    function useDnnConfigCore() {
      var state = React.useState({ loading: true, error: null, status: null, busy: false })
      var view = state[0]
      var setView = state[1]
      var aliveRef = React.useRef(true)

      function loadStatus(showLoading) {
        if (showLoading) {
          setView(function (prev) {
            return Object.assign({}, prev, { loading: true, error: null })
          })
        }
        return dnnRpc(dnnRpcPayload('status'))
          .then(function (res) {
            if (!aliveRef.current) return
            if (!res || !res.ok || !res.value) {
              setView(function (prev) {
                return Object.assign({}, prev, {
                  loading: false,
                  error: dnnErrorText(res),
                })
              })
              return
            }
            setView(function (prev) {
              return Object.assign({}, prev, { loading: false, error: null, status: res.value })
            })
          })
          .catch(function (e) {
            if (!aliveRef.current) return
            setView(function (prev) {
              return Object.assign({}, prev, { loading: false, error: errText(e) })
            })
          })
      }

      React.useEffect(function () {
        aliveRef.current = true
        loadStatus(true)
        return function () {
          aliveRef.current = false
        }
      }, [])

      var phase =
        view.status && view.status.config && view.status.config.phase
          ? String(view.status.config.phase)
          : ''
      React.useEffect(
        function () {
          if (phase !== 'pending' && phase !== 'switching') return
          var timer = window.setInterval(function () {
            loadStatus(false)
          }, DNN_STATUS_POLL_MS)
          return function () {
            window.clearInterval(timer)
          }
        },
        [phase],
      )

      return { view: view, setView: setView, aliveRef: aliveRef, loadStatus: loadStatus }
    }

    function setDnnBusy(core, busy) {
      core.setView(function (prev) {
        return Object.assign({}, prev, { busy: busy })
      })
    }

    /** 统一 RPC 流程：busy 包围、取消后不更新、失败返回结构化 error。 */
    function runDnnRequest(core, payload, done) {
      if (!presenceRpc) {
        done({ ok: false, error: { code: 'rpc_unavailable', message: 'DSH 连接服务不可用' } })
        return
      }
      setDnnBusy(core, true)
      dnnRpc(payload)
        .then(function (res) {
          if (!core.aliveRef.current) return
          setDnnBusy(core, false)
          done(res)
        })
        .catch(function (e) {
          if (!core.aliveRef.current) return
          setDnnBusy(core, false)
          done({ ok: false, error: { code: 'rpc_failed', message: errText(e) } })
        })
    }

    function DnnConfigSection() {
      var core = useDnnConfigCore()
      var status = core.view.status
      var formState = React.useState(null)
      var form = formState[0]
      var setForm = formState[1]
      var modalState = React.useState(null)
      var modal = modalState[0]
      var setModal = modalState[1]
      var selectionState = React.useState(dnnDefaultSelection())
      var selection = selectionState[0]
      var setSelection = selectionState[1]
      var probeState = React.useState(null)
      var probe = probeState[0]
      var setProbe = probeState[1]
      var feedbackState = React.useState(null)
      var feedback = feedbackState[0]
      var setFeedback = feedbackState[1]
      var downloadErrorState = React.useState(null)
      var downloadError = downloadErrorState[0]
      var setDownloadError = downloadErrorState[1]

      var effective = form || dnnCandidateFromStatus(status)
      var envLocked = !!(status && status.envLocked)
      var busy = core.view.busy
      var controls = dnnControlState(status, busy, !!presenceRpc)
      var enabled = effective.features.dnn === true

      function patch(patchValue) {
        setForm(dnnFormUpdate(effective, patchValue))
        setFeedback(null)
      }

      function toggleEnabled(next) {
        setForm(dnnFormUpdate(effective, { dnn: next }))
        setFeedback(null)
        if (next && dnnDownloadPromptVisible(status)) {
          setSelection(dnnDefaultSelection())
          setDownloadError(null)
          setModal('download')
        }
      }

      function toggleSelection(id) {
        var next = []
        for (var i = 0; i < selection.length; i++) if (selection[i] !== id) next.push(selection[i])
        if (next.length === selection.length) next.push(id)
        if (!next.length) setDownloadError('请至少选择一个模型')
        else setDownloadError(null)
        setSelection(next)
      }

      function runAction(payload) {
        var reason =
          payload.op === 'apply' || payload.op === 'probe'
            ? dnnCandidateInvalidReason(payload.candidate)
            : null
        if (reason) {
          setFeedback({ kind: 'error', text: reason })
          return
        }
        runDnnRequest(core, payload, function (res) {
          if (payload.op === 'probe') {
            if (!res || !res.ok || !res.value) setProbe({ error: dnnErrorText(res) })
            else setProbe(res.value)
            setModal('probe')
            return
          }
          if (payload.op === 'download') {
            var outcome = res && res.ok && res.value ? res.value : null
            if (!outcome || outcome.ok === false) {
              var message = outcome && outcome.message ? String(outcome.message) : dnnErrorText(res)
              if (outcome && outcome.code === 'dnn_pin_unavailable')
                message =
                  '预设校验值尚未采集，下载被拒绝（dnn_pin_unavailable）。' +
                  (outcome.message ? ' ' + String(outcome.message) : '')
              setDownloadError(message)
              setFeedback({ kind: 'error', text: message })
              core.loadStatus(false)
              return
            }
            var downloaded = 0
            var items = Array.isArray(outcome.items) ? outcome.items : []
            for (var i = 0; i < items.length; i++)
              if (items[i] && items[i].state === 'downloaded') downloaded++
            setModal(null)
            setDownloadError(null)
            core.loadStatus(false)
            // 仅关掉弹窗不改配置：开关刚打开时表单还没落盘，下载成功后补一次 apply。
            if (dnnShouldApplyAfterDownload(effective, status)) {
              runAction(dnnApplyPayload(effective))
              return
            }
            setFeedback({
              kind: 'ok',
              text:
                '下载完成：新下载 ' +
                downloaded +
                ' 个模型。无需重启 Gateway，下一次 vision_detect 自动使用新模型。',
            })
            return
          }
          if (!res || !res.ok || !res.value) {
            setFeedback({ kind: 'error', text: dnnErrorText(res) })
            return
          }
          var value = res.value
          if (payload.op === 'apply') {
            var text
            if (value.applied) text = '已保存并生效（revision ' + value.revision + '）。'
            else if (value.savedForNextStart)
              text = 'Gateway 未运行：配置已保存，下次启动生效（revision ' + value.revision + '）。'
            else
              text =
                '配置已保存（revision ' +
                value.revision +
                '），当前 phase=' +
                String(value.phase || 'unknown') +
                '；等待在途请求结束后生效。'
            setFeedback({ kind: value.applied ? 'ok' : 'warning', text: text })
            // 保存后让表单重新跟随状态，避免继续显示已被 env 锁定或热切换改写的旧值。
            setForm(null)
            core.loadStatus(false)
            // 3.4：保存并应用后自动跑一次 probe（候选配置一次性进程）。
            runAction(dnnRpcPayload('probe', { candidate: effective }))
            return
          } else if (payload.op === 'cancel-pending') {
            setFeedback({ kind: 'ok', text: '已取消等待：待应用配置已用当前生效配置覆盖。' })
            setForm(null)
          } else if (payload.op === 'rollback') {
            setFeedback({
              kind: 'ok',
              text: '已回滚上一版配置（revision ' + value.revision + '）。',
            })
            setForm(null)
          }
          core.loadStatus(false)
        })
      }

      var children = []
      children.push(
        React.createElement(
          'div',
          {
            key: 'dnn-head',
            style: { display: 'flex', alignItems: 'baseline', gap: 8, flexWrap: 'wrap' },
          },
          React.createElement(
            'div',
            { style: { fontSize: 14, fontWeight: 600 } },
            '视觉检测（DNN）',
          ),
          React.createElement(
            'span',
            {
              style: {
                padding: '1px 7px',
                borderRadius: 999,
                fontSize: 11,
                color: '#fff',
                background: '#bf8700',
              },
            },
            '实验性',
          ),
        ),
      )
      children.push(
        React.createElement(
          'div',
          {
            key: 'dnn-desc',
            style: {
              marginTop: 4,
              fontSize: 12,
              lineHeight: 1.6,
              color: 'var(--dsh-text-secondary, #888)',
            },
          },
          DNN_EXPERIMENTAL_TEXT,
        ),
      )
      children.push(
        React.createElement(
          'div',
          { key: 'dnn-doc', style: { marginTop: 4, fontSize: 12 } },
          React.createElement(
            'a',
            { href: DNN_DOCS_URL, target: '_blank', rel: 'noreferrer' },
            '自定义后端与 GPU DIY 文档',
          ),
        ),
      )

      var statusText = dnnStatusText(status)
      var statusColor =
        status && status.config && status.config.phase === 'failed'
          ? '#d33'
          : status &&
              status.config &&
              (status.config.phase === 'pending' || status.config.phase === 'switching')
            ? '#9a6700'
            : 'var(--dsh-text-secondary, #888)'
      children.push(
        React.createElement(
          'div',
          {
            key: 'dnn-status',
            style: { marginTop: 8, fontSize: 12, lineHeight: 1.6, color: statusColor },
          },
          statusText,
        ),
      )
      children.push(React.createElement(DnnStatusDetails, { key: 'dnn-details', status: status }))
      if (core.view.error) {
        children.push(
          React.createElement(
            'div',
            { key: 'dnn-error', style: { color: '#d33', fontSize: 12, marginTop: 6 } },
            core.view.error,
          ),
        )
      }
      if (envLocked) {
        children.push(
          React.createElement(
            ConfigNotice,
            { key: 'dnn-env-lock', kind: 'warning' },
            'features.dnn 由 DSH_BLENDER_DNN 环境变量锁定，开关不可用；其他 DNN 字段仍可保存并热应用。移除该环境变量并重启 Gateway 后才能在配置卡修改开关。',
          ),
        )
      }

      children.push(
        React.createElement(
          'label',
          {
            key: 'dnn-switch',
            style: {
              display: 'flex',
              alignItems: 'center',
              gap: 8,
              padding: '10px 0 4px',
              fontSize: 13,
              cursor: controls.switchDisabled ? 'default' : 'pointer',
            },
          },
          React.createElement('input', {
            type: 'checkbox',
            checked: enabled,
            disabled: controls.switchDisabled,
            onChange: function (e) {
              toggleEnabled(!!(e && e.target && e.target.checked))
            },
          }),
          React.createElement('span', null, '启用 features.dnn（YOLOv5 目标检测）'),
        ),
      )

      if (enabled) {
        if (dnnDownloadPromptVisible(status)) {
          children.push(
            React.createElement(
              ConfigNotice,
              { key: 'dnn-no-model', kind: 'warning' },
              React.createElement(
                'div',
                null,
                '模型目录里没有 .onnx 模型：现在保存后，vision_detect 会以 dnn_no_model 失败。请下载预设模型，或按文档自行放置模型与 models.yml。',
              ),
              React.createElement(
                'div',
                { style: { display: 'flex', gap: 8, marginTop: 8, flexWrap: 'wrap' } },
                React.createElement(
                  ConfigButton,
                  {
                    primary: true,
                    disabled: controls.fieldDisabled,
                    onClick: function () {
                      setSelection(dnnDefaultSelection())
                      setDownloadError(null)
                      setModal('download')
                    },
                  },
                  '下载预设模型',
                ),
                React.createElement(
                  'a',
                  {
                    href: DNN_DOCS_URL,
                    target: '_blank',
                    rel: 'noreferrer',
                    style: { fontSize: 12, alignSelf: 'center' },
                  },
                  '查看文档',
                ),
              ),
            ),
          )
        }

        children.push(
          React.createElement(
            'div',
            { key: 'dnn-diy-head', style: { marginTop: 10, fontSize: 12, fontWeight: 600 } },
            '自定义后端（可选；默认 OpenCV + CPU）',
          ),
        )
        children.push(
          React.createElement(
            'div',
            {
              key: 'dnn-fields',
              style: { display: 'flex', flexDirection: 'column', gap: 8, marginTop: 6 },
            },
            React.createElement(DnnField, {
              label: '解释器 python',
              hint: '留空自动探测；可填绝对路径',
              value: effective.vision.python,
              disabled: controls.fieldDisabled,
              placeholder: '例如 C:\\Python311\\python.exe',
              onChange: function (v) {
                patch({ python: v })
              },
            }),
            React.createElement(DnnField, {
              label: '自定义 venv（vision.venv）',
              hint: '留空使用托管 venv；GPU 需在此 venv 自行安装 onnxruntime',
              value: effective.vision.venv,
              disabled: controls.fieldDisabled,
              placeholder: '例如 C:\\venvs\\blender-onnx',
              onChange: function (v) {
                patch({ venv: v })
              },
            }),
            React.createElement(DnnSelect, {
              label: 'DNN 后端（vision.dnn.backend）',
              hint: 'opencv 默认 CPU；onnxruntime 需要自备 venv',
              value: effective.vision.dnn.backend,
              disabled: controls.fieldDisabled,
              options: [
                { value: 'opencv', label: 'opencv（默认，CPU）' },
                { value: 'onnxruntime', label: 'onnxruntime（自备 venv）' },
              ],
              onChange: function (v) {
                patch(v === 'opencv' ? { backend: v, device: 'cpu' } : { backend: v })
              },
            }),
            React.createElement(DnnSelect, {
              label: '设备（vision.dnn.device）',
              hint: 'opencv 只能 cpu；auto/gpu 需要 onnxruntime',
              value: effective.vision.dnn.device,
              disabled: controls.fieldDisabled || effective.vision.dnn.backend === 'opencv',
              options: [
                { value: 'cpu', label: 'cpu' },
                { value: 'auto', label: 'auto（优先 GPU，可回退 CPU）' },
                { value: 'gpu', label: 'gpu（无可用 provider 时报错）' },
              ],
              onChange: function (v) {
                patch({ device: v })
              },
            }),
            React.createElement(DnnField, {
              key: 'dnn-model',
              label: '模型（vision.model）',
              hint: '预设：yolov5n / yolov5s / yolov5m / yolov5l / yolov5x；也可填 models.yml 里的任意 key',
              value: effective.vision.model,
              disabled: controls.fieldDisabled,
              placeholder: 'yolov5n',
              onChange: function (v) {
                patch({ model: v })
              },
            }),
          ),
        )

        children.push(
          React.createElement(
            'div',
            {
              key: 'dnn-actions',
              style: { display: 'flex', gap: 8, marginTop: 10, flexWrap: 'wrap' },
            },
            React.createElement(
              ConfigButton,
              {
                primary: true,
                disabled: controls.fieldDisabled,
                onClick: function () {
                  runAction(dnnApplyPayload(effective))
                },
              },
              busy ? '处理中…' : '保存并应用',
            ),
            React.createElement(
              ConfigButton,
              {
                disabled: controls.fieldDisabled,
                onClick: function () {
                  runAction(dnnRpcPayload('probe', { candidate: effective }))
                },
              },
              '检测可用性',
            ),
            React.createElement(
              ConfigButton,
              {
                disabled: controls.fieldDisabled || !dnnCanCancelPending(status),
                onClick: function () {
                  runAction(dnnRpcPayload('cancel-pending'))
                },
              },
              '取消等待',
            ),
            React.createElement(
              ConfigButton,
              {
                disabled: controls.fieldDisabled || !dnnCanRollback(status),
                onClick: function () {
                  runAction(dnnRpcPayload('rollback'))
                },
              },
              '回滚上一版',
            ),
            React.createElement(
              ConfigButton,
              {
                disabled: controls.fieldDisabled,
                onClick: function () {
                  setSelection(dnnDefaultSelection())
                  setDownloadError(null)
                  setModal('download')
                },
              },
              '下载预设模型…',
            ),
            React.createElement(
              ConfigButton,
              {
                disabled: controls.fieldDisabled,
                onClick: function () {
                  core.loadStatus(false)
                },
              },
              '刷新状态',
            ),
          ),
        )
        children.push(React.createElement(DnnFeedback, { key: 'dnn-feedback', feedback: feedback }))
      } else {
        children.push(
          React.createElement(
            'div',
            {
              key: 'dnn-off-hint',
              style: { fontSize: 11, color: 'var(--dsh-text-secondary, #888)', marginTop: 4 },
            },
            'DNN 关闭时，vision_detect 会在启动 worker 之前返回 stage=config、code=dnn_disabled，不会自动下载模型。',
          ),
        )
      }

      if (modal === 'download') {
        children.push(
          React.createElement(DnnDownloadModal, {
            key: 'dnn-download-modal',
            selection: selection,
            busy: busy,
            error: downloadError,
            downloadable: status && Array.isArray(status.downloadable) ? status.downloadable : [],
            onToggle: toggleSelection,
            onCancel: function () {
              setModal(null)
              setDownloadError(null)
            },
            onConfirm: function () {
              runAction(dnnRpcPayload('download', { ids: selection.slice() }))
            },
          }),
        )
      }
      if (modal === 'probe') {
        children.push(
          React.createElement(DnnProbeModal, {
            key: 'dnn-probe-modal',
            result: probe,
            onClose: function () {
              setModal(null)
            },
          }),
        )
      }

      return React.createElement(
        'div',
        {
          style: {
            marginTop: 18,
            paddingTop: 14,
            borderTop: '1px solid var(--dsh-border, rgba(127,127,127,.22))',
          },
        },
        children,
      )
    }

    function DnnField(props) {
      return React.createElement(
        'label',
        { style: { display: 'flex', flexDirection: 'column', gap: 3, fontSize: 12 } },
        React.createElement('span', { style: { fontWeight: 500 } }, props.label),
        React.createElement('input', {
          type: 'text',
          value: props.value,
          disabled: !!props.disabled,
          placeholder: props.placeholder || '',
          onChange: function (e) {
            props.onChange(String((e && e.target && e.target.value) || ''))
          },
          style: {
            padding: '5px 8px',
            borderRadius: 6,
            border: '1px solid var(--dsh-border, rgba(127,127,127,.3))',
            background: 'transparent',
            color: 'inherit',
            fontSize: 12,
          },
        }),
        props.hint
          ? React.createElement(
              'span',
              { style: { fontSize: 11, color: 'var(--dsh-text-secondary, #888)' } },
              props.hint,
            )
          : null,
      )
    }

    function DnnSelect(props) {
      var options = []
      for (var i = 0; i < props.options.length; i++) {
        var option = props.options[i]
        options.push(
          React.createElement(
            'option',
            { key: String(option.value), value: option.value },
            String(option.label || option.value),
          ),
        )
      }
      return React.createElement(
        'label',
        { style: { display: 'flex', flexDirection: 'column', gap: 3, fontSize: 12 } },
        React.createElement('span', { style: { fontWeight: 500 } }, props.label),
        React.createElement(
          'select',
          {
            value: props.value,
            disabled: !!props.disabled,
            onChange: function (e) {
              props.onChange(String((e && e.target && e.target.value) || ''))
            },
            style: {
              padding: '5px 8px',
              borderRadius: 6,
              border: '1px solid var(--dsh-border, rgba(127,127,127,.3))',
              background: 'transparent',
              color: 'inherit',
              fontSize: 12,
            },
          },
          options,
        ),
        props.hint
          ? React.createElement(
              'span',
              { style: { fontSize: 11, color: 'var(--dsh-text-secondary, #888)' } },
              props.hint,
            )
          : null,
      )
    }
    //#endregion

    function ConfigModalShell(props) {
      return React.createElement(
        'div',
        {
          style: {
            position: 'fixed',
            inset: 0,
            zIndex: 1000,
            background: 'rgba(0,0,0,.42)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            padding: 16,
          },
        },
        React.createElement(
          'div',
          {
            style: {
              width: 'min(520px, 100%)',
              maxHeight: '80vh',
              overflow: 'auto',
              padding: 18,
              borderRadius: 10,
              background: 'var(--dsh-bg, #1f1f23)',
              color: 'var(--dsh-text, inherit)',
              boxShadow: '0 12px 40px rgba(0,0,0,.35)',
              fontSize: 13,
            },
          },
          props.children,
        ),
      )
    }

    function ConfigInstallModal(props) {
      var install = props.install
      var lines = Array.isArray(install.lines) ? install.lines : []
      return React.createElement(
        ConfigModalShell,
        null,
        React.createElement(
          'div',
          { style: { fontWeight: 600, marginBottom: 8 } },
          '正在安装 ' + String(install.label || install.planId || ''),
        ),
        React.createElement(
          'div',
          { style: { color: 'var(--dsh-text-secondary, #888)', fontSize: 12 } },
          '安装命令运行期间请保持此窗口打开；完成后会自动重新检测依赖。',
        ),
        React.createElement(
          'pre',
          {
            style: {
              marginTop: 10,
              padding: 8,
              maxHeight: 220,
              overflow: 'auto',
              borderRadius: 6,
              background: 'rgba(127,127,127,.12)',
              fontSize: 11,
              whiteSpace: 'pre-wrap',
              wordBreak: 'break-all',
            },
          },
          lines.length ? lines.join('\n') : '等待安装命令输出…',
        ),
      )
    }

    function ConfigPromptDetails(props) {
      var prompt = props.prompt
      var children = []
      if (prompt.detail)
        children.push(
          React.createElement(
            'div',
            {
              key: 'detail',
              style: { fontSize: 12, color: 'var(--dsh-text-secondary, #888)', marginBottom: 8 },
            },
            String(prompt.detail),
          ),
        )
      if (prompt.mode === 'missing' || prompt.mode === 'manual') {
        var labels = []
        for (var i = 0; i < (prompt.plans || []).length; i++) {
          var item = prompt.plans[i]
          if (item)
            labels.push((item.label || item.id || '') + (item.reason ? '：' + item.reason : ''))
        }
        children.push(
          React.createElement(
            'div',
            { key: 'list', style: { fontSize: 12, marginBottom: 8 } },
            labels.map(function (line, index) {
              return React.createElement('div', { key: 'missing-' + index }, line)
            }),
          ),
        )
      }
      if (prompt.command)
        children.push(
          React.createElement(
            'pre',
            {
              key: 'command',
              style: {
                margin: '8px 0',
                padding: 8,
                borderRadius: 6,
                background: 'rgba(127,127,127,.12)',
                fontSize: 11,
                whiteSpace: 'pre-wrap',
                wordBreak: 'break-all',
              },
            },
            String(prompt.command),
          ),
        )
      if (Array.isArray(prompt.steps) && prompt.steps.length) {
        children.push(
          React.createElement(
            'div',
            { key: 'steps', style: { fontSize: 12, marginTop: 6 } },
            prompt.steps.map(function (step, index) {
              return React.createElement(
                'div',
                { key: 'step-' + index, style: { marginTop: 4 } },
                String(step.description || '') + (step.command ? '：' + String(step.command) : ''),
              )
            }),
          ),
        )
      }
      return children
    }

    function ConfigPromptActions(props) {
      var prompt = props.prompt
      var actions = []
      var firstPlan = prompt.plans && prompt.plans[0]
      if (
        (prompt.mode === 'missing' || prompt.mode === 'failed') &&
        firstPlan &&
        firstPlan.supported &&
        firstPlan.oneClick
      ) {
        actions.push(
          React.createElement(
            ConfigButton,
            {
              key: 'install',
              primary: true,
              onClick: function () {
                props.onInstall(firstPlan, prompt.enableAfter === true)
              },
            },
            prompt.mode === 'failed'
              ? '重试安装'
              : prompt.enableAfter === true
                ? '安装并开启'
                : '安装',
          ),
        )
      }
      if (prompt.mode === 'manual') {
        actions.push(
          React.createElement(
            ConfigButton,
            { key: 'recheck', primary: true, onClick: props.onRecheck },
            '我已安装，重新检测',
          ),
        )
      }
      actions.push(
        React.createElement(ConfigButton, { key: 'cancel', onClick: props.onCancel }, '暂不安装'),
      )
      if (props.permission.allowed) {
        actions.push(
          React.createElement(
            ConfigButton,
            { key: 'disable', onClick: props.onDisable },
            '关闭 virtual-gui',
          ),
        )
      }
      return React.createElement(
        'div',
        { style: { display: 'flex', gap: 8, marginTop: 12, flexWrap: 'wrap' } },
        actions,
      )
    }

    function ConfigPromptModal(props) {
      return React.createElement(
        ConfigModalShell,
        null,
        React.createElement(
          'div',
          { style: { fontWeight: 600, marginBottom: 8 } },
          props.prompt.title || 'virtual-gui',
        ),
        React.createElement(ConfigPromptDetails, { prompt: props.prompt }),
        React.createElement(ConfigPromptActions, props),
      )
    }

    function BlenderPluginConfigPage() {
      var core = useVirtualGuiConfigCore()
      var installState = core.view.install && core.view.install.state
      var missing = missingVirtualGuiPlans(core.view.capability, core.view.plans)
      useVirtualGuiInstallPolling(core, installState)
      if (installState === 'installing') {
        return React.createElement(
          'div',
          { style: { padding: '4px 0', width: '100%', boxSizing: 'border-box' } },
          React.createElement(ConfigBody, {
            core: core,
            missing: missing,
            installState: installState,
            onToggle: function (next) {
              toggleVirtualGuiPermission(core, next)
            },
            onInstall: function (plan, enableAfter) {
              requestVirtualGuiInstall(core, plan, enableAfter)
            },
            onDisable: function () {
              toggleVirtualGuiPermission(core, false)
            },
          }),
          React.createElement(ConfigInstallModal, { install: core.view.install }),
        )
      }
      if (core.prompt) {
        return React.createElement(
          'div',
          { style: { padding: '4px 0', width: '100%', boxSizing: 'border-box' } },
          React.createElement(ConfigBody, {
            core: core,
            missing: missing,
            installState: installState,
            onToggle: function (next) {
              toggleVirtualGuiPermission(core, next)
            },
            onInstall: function (plan, enableAfter) {
              requestVirtualGuiInstall(core, plan, enableAfter)
            },
            onDisable: function () {
              toggleVirtualGuiPermission(core, false)
            },
          }),
          React.createElement(ConfigPromptModal, {
            prompt: core.prompt,
            permission: core.view.permission,
            onInstall: function (plan, enableAfter) {
              requestVirtualGuiInstall(core, plan, enableAfter)
            },
            onRecheck: function () {
              recheckVirtualGuiAfterManual(core)
            },
            onCancel: function () {
              core.setPrompt(null)
              core.pendingEnableRef.current = false
            },
            onDisable: function () {
              core.setPrompt(null)
              toggleVirtualGuiPermission(core, false)
            },
          }),
        )
      }
      return React.createElement(
        'div',
        { style: { padding: '4px 0', width: '100%', boxSizing: 'border-box' } },
        React.createElement(ConfigBody, {
          core: core,
          missing: missing,
          installState: installState,
          onToggle: function (next) {
            toggleVirtualGuiPermission(core, next)
          },
          onInstall: function (plan, enableAfter) {
            requestVirtualGuiInstall(core, plan, enableAfter)
          },
          onDisable: function () {
            toggleVirtualGuiPermission(core, false)
          },
        }),
      )
    }

    function BlenderPluginConfigPanel(props) {
      if (props && props.view === 'summary') return React.createElement(BlenderPluginConfigSummary)
      return React.createElement(BlenderPluginConfigPage)
    }

    function BlenderPresencePanel() {
      var state = React.useState({
        loading: true,
        error: null,
        sessions: [],
        virtualGui: {
          total: 0,
          userGui: 0,
          virtualGui: 0,
          enabled: true,
          ready: false,
          missing: [],
          reason: '',
        },
      })
      var view = state[0]
      var setView = state[1]
      var permissionState = React.useState({
        loaded: false,
        allowed: false,
        source: 'config',
        saving: false,
        error: null,
      })
      var permission = permissionState[0]
      var setPermission = permissionState[1]

      function readPermission(alive) {
        if (!presenceRpc) {
          setPermission(function (prev) {
            return Object.assign({}, prev, { loaded: true, error: 'DSH 连接服务不可用' })
          })
          return
        }
        presenceRpc('/dsh-blender', 'permission', { op: 'status' })
          .then(function (res) {
            if (!alive) return
            if (res && res.ok && res.value) {
              setPermission(function (prev) {
                if (prev.saving) return prev
                return {
                  loaded: true,
                  allowed: res.value.allowed === true,
                  source: String(res.value.source || 'config'),
                  saving: false,
                  error: null,
                }
              })
              return
            }
            setPermission(function (prev) {
              if (prev.saving) return prev
              return Object.assign({}, prev, {
                loaded: true,
                saving: false,
                error: (res && res.error && res.error.message) || '无法读取 virtual-gui 授权状态',
              })
            })
          })
          .catch(function (e) {
            if (!alive) return
            setPermission(function (prev) {
              return Object.assign({}, prev, {
                loaded: true,
                saving: false,
                error: errText(e),
              })
            })
          })
      }

      React.useEffect(function () {
        var alive = true
        function load() {
          fetchJSON(backend() + '/host/state', { cache: 'no-store' }).then(function (r) {
            if (!alive) return
            if (!r.ok || !r.body) {
              setView({
                loading: false,
                error: r.error || 'Gateway 不可达',
                sessions: [],
                virtualGui: virtualGuiCounts(null),
              })
              return
            }
            setView({
              loading: false,
              error: null,
              sessions: (r.body.presence && r.body.presence.sessions) || [],
              virtualGui: virtualGuiCounts(r.body),
            })
          })
          readPermission(alive)
        }
        load()
        var timer = window.setInterval(load, PRESENCE_POLL_MS)
        return function () {
          alive = false
          window.clearInterval(timer)
        }
      }, [])

      function togglePermission(next) {
        if (!presenceRpc || permission.saving) return
        setPermission(Object.assign({}, permission, { saving: true, error: null }))
        presenceRpc('/dsh-blender', 'permission', { op: 'set', allow: next })
          .then(function (res) {
            if (res && res.ok && res.value) {
              setPermission({
                loaded: true,
                allowed: res.value.allowed === true,
                source: String(res.value.source || 'runtime'),
                saving: false,
                error: null,
              })
              return
            }
            setPermission(
              Object.assign({}, permission, {
                saving: false,
                error: (res && res.error && res.error.message) || '无法更新 virtual-gui 授权',
              }),
            )
          })
          .catch(function (e) {
            setPermission(Object.assign({}, permission, { saving: false, error: errText(e) }))
          })
      }

      var rows = presenceRowsFromState({ presence: { sessions: view.sessions } })
      var children = []
      children.push(
        React.createElement(
          'div',
          {
            key: 'header',
            style: { display: 'flex', alignItems: 'baseline', gap: 10, marginBottom: 12 },
          },
          React.createElement(
            'h2',
            { style: { margin: 0, fontSize: 15, fontWeight: 600 } },
            'Blender Presence',
          ),
          React.createElement(
            'span',
            { style: { color: 'var(--dsh-text-secondary, #888)', fontSize: 12 } },
            rows.length +
              ' 个 agent · ' +
              view.virtualGui.total +
              ' 个 GUI Blender' +
              (view.virtualGui.total
                ? '（user-gui ' +
                  view.virtualGui.userGui +
                  ' · virtual-gui ' +
                  view.virtualGui.virtualGui +
                  '）'
                : ''),
          ),
        ),
      )
      children.push(
        React.createElement(
          'div',
          {
            key: 'permission',
            style: {
              padding: '10px 0 12px',
              marginBottom: 6,
              borderBottom: '1px solid var(--dsh-border, rgba(127,127,127,.18))',
            },
          },
          React.createElement(
            'label',
            {
              key: 'toggle',
              style: {
                display: 'flex',
                alignItems: 'center',
                gap: 8,
                fontSize: 13,
                cursor: 'pointer',
              },
            },
            React.createElement('input', {
              key: 'checkbox',
              type: 'checkbox',
              checked: permission.allowed,
              disabled: !presenceRpc || permission.saving,
              onChange: function (e) {
                togglePermission(!!(e && e.target && e.target.checked))
              },
            }),
            React.createElement(
              'span',
              { key: 'label' },
              '允许 agent 启动 virtual-gui（本机虚拟 Blender 窗口）',
            ),
          ),
          React.createElement(
            'div',
            {
              key: 'hint',
              style: {
                marginTop: 4,
                marginLeft: 24,
                fontSize: 11,
                color: permission.error ? '#d33' : 'var(--dsh-text-secondary, #888)',
              },
            },
            permission.error
              ? permission.error
              : permission.saving
                ? '正在更新…'
                : presenceRpc
                  ? '默认由插件配置 allowVirtualGui 决定；此开关只影响当前 DSH 进程。'
                  : 'DSH 连接服务不可用，开关禁用；默认不允许。',
          ),
        ),
      )
      if (view.permission.allowed && view.virtualGui.missing.length) {
        children.push(
          React.createElement(
            'div',
            {
              key: 'missing-deps',
              style: {
                color: '#9a6700',
                background: 'rgba(210,153,34,.12)',
                border: '1px solid rgba(210,153,34,.35)',
                borderRadius: 6,
                padding: '8px 10px',
                fontSize: 12,
                marginBottom: 12,
              },
            },
            'virtual-gui 缺少依赖：' +
              view.virtualGui.missing.join('、') +
              '。到 DSH Plugins → Blender 配置页安装，或关闭 virtual-gui。',
          ),
        )
      }
      if (view.error) {
        children.push(
          React.createElement(
            'div',
            { key: 'error', style: { color: '#d33', fontSize: 13, marginBottom: 12 } },
            '无法读取 Gateway：' + view.error,
          ),
        )
      }
      if (!rows.length && !view.loading) {
        children.push(
          React.createElement(
            'div',
            { key: 'empty', style: { color: 'var(--dsh-text-secondary, #888)', fontSize: 13 } },
            '暂无 agent 观察记录。运行 blender_see 后，这里会显示它的位置和最近活动。',
          ),
        )
      }
      for (var i = 0; i < rows.length; i++) {
        var row = rows[i]
        children.push(
          React.createElement(
            'div',
            {
              key: row.id || row.sessionId + ':' + i,
              style: {
                display: 'flex',
                alignItems: 'center',
                gap: 10,
                padding: '10px 0',
                borderBottom: '1px solid var(--dsh-border, rgba(127,127,127,.18))',
              },
            },
            React.createElement(PresenceDot, {
              color: row.isVirtualGui ? VIRTUAL_GUI_COLOR : row.color,
            }),
            React.createElement(
              'div',
              { style: { minWidth: 0, flex: '1 1 auto' } },
              React.createElement(
                'div',
                { style: { fontSize: 13, fontWeight: 500 } },
                (row.shortId ? row.shortId + ' · ' : '') + row.displayName,
              ),
              React.createElement(
                'div',
                { style: { fontSize: 11, color: 'var(--dsh-text-secondary, #888)', marginTop: 2 } },
                (row.viewpoint ? row.viewpoint + ' · ' : '') +
                  (row.observation === 'current' ? 'observation current · ' : 'no observation · ') +
                  row.activity +
                  ' · ' +
                  formatPresenceTime(row.lastActivityAt),
              ),
            ),
            React.createElement(
              'div',
              { style: { fontSize: 11, color: 'var(--dsh-text-secondary, #888)' }, title: row.id },
              row.isVirtualGui
                ? React.createElement(
                    'span',
                    {
                      style: {
                        display: 'inline-block',
                        padding: '1px 6px',
                        marginBottom: 2,
                        borderRadius: 999,
                        color: '#fff',
                        background: VIRTUAL_GUI_COLOR,
                        fontSize: 10,
                      },
                    },
                    'virtual-gui',
                  )
                : null,
              React.createElement(
                'div',
                null,
                row.sessionKind + (row.instanceId ? ' · ' + row.instanceId.slice(0, 8) : ''),
              ),
            ),
          ),
        )
      }
      return React.createElement(
        'div',
        { style: { padding: '14px 16px', width: '100%', boxSizing: 'border-box', minWidth: 0 } },
        children,
      )
    }
    //#endregion

    /**
     * cordis 插件入口。`slots` 是两条 UI 通路（输入区 dock、右侧栏 tab body）的必需服务；
     * 右侧栏 tab 还要 `sidebarRight`（打开 tab）和 `sidebarRightTabs`（注册 tab 类型 + 引导页）。
     * `inputTriggers` 用 `ctx.inject([...])` 懒等，避免"输入触发器没挂载 ⇒ 连原生选中桥也一起消失"。
     * v2.3：DSH 侧视口面板与 blender_rt_pick 已删除；这里留 @ 提及源、原生选中桥、agent presence。
     */
    function apply(ctx) {
      if (started) return
      started = true
      configuredBackend = resolveConfiguredBackend(ctx)

      // Blender Presence 的 virtual-gui 授权开关走 DSH 认证 RPC：只有人类浏览器
      // 能调到这个端点，Blender 内的 Python 拿不到浏览器 cookie。
      ctx.inject(['connection'], function (scope) {
        var connection = serviceOf(scope, 'connection')
        if (!connection || !connection.rpc || typeof connection.rpc.call !== 'function') return
        var call = function (channel, endpoint, payload) {
          return connection.rpc.call(channel, endpoint, payload)
        }
        presenceRpc = call
        scope.effect(function () {
          if (presenceRpc === call) presenceRpc = null
        }, 'dsh-blender-plugin: virtual-gui permission RPC')
      })

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

      // ③ Agent presence：右侧栏 tab 类型 + 引导页入口 + /blender-presence 命令
      ctx.effect(function () {
        return ctx.sidebarRightTabs.register({
          id: PRESENCE_PANEL_ID,
          kind: PRESENCE_TAB_KIND,
          priority: 'extension',
          title: function () {
            return 'Blender Presence'
          },
          guide: [
            {
              id: 'presence',
              order: 40,
              title: function () {
                return 'Blender Presence'
              },
              description: function () {
                return '查看已连接的 Blender agent 与最近观察位置'
              },
              icon: PresencePanelIcon,
            },
          ],
        })
      }, 'dsh-blender-plugin: presence right tab type')
      ctx.slots.inject('sidebar.right.pane.tab', function () {
        return ctx.slots.register(
          {
            name: 'sidebar.right.pane.tab',
            key: PRESENCE_PANEL_ID,
          },
          BlenderPresencePanel,
        )
      })
      // ④ DSH Plugins 页的 Blender 配置卡（依赖检测/安装询问/安装进度）
      ctx.slots.inject('plugins.item', function () {
        return ctx.slots.register(
          {
            name: 'plugins.item',
            id: PLUGIN_CONFIG_ID,
            order: 60,
            label: function () {
              return 'Blender'
            },
          },
          BlenderPluginConfigPanel,
        )
      })
      ctx.inject(['commandUi'], function (scope) {
        var commandUi = typeof scope.get === 'function' ? scope.get('commandUi') : undefined
        if (!commandUi || typeof commandUi.register !== 'function') return
        scope.effect(function () {
          return commandUi.register({
            name: 'blender-presence',
            label: function () {
              return 'Blender Presence'
            },
            description: function () {
              return '查看已连接的 Blender agent 与最近观察位置'
            },
            available: function () {
              return true
            },
            ui: {
              kind: 'action',
              run: function () {
                if (ctx.sidebarRight && typeof ctx.sidebarRight.openTab === 'function')
                  ctx.sidebarRight.openTab(PRESENCE_TAB_KIND)
              },
            },
          })
        }, 'dsh-blender-plugin: /blender-presence')
      })

      // ④ 卸载时复位守卫（便于热重载 / 重新 apply）
      ctx.effect(function () {
        return function () {
          started = false
        }
      }, 'dsh-blender-plugin: reset apply guard')
    }

    var inject = ['slots', 'sidebarRight', 'sidebarRightTabs']

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
      PRESENCE_PANEL_ID: PRESENCE_PANEL_ID,
      PRESENCE_TAB_KIND: PRESENCE_TAB_KIND,
      presenceRowsFromState: presenceRowsFromState,
      formatPresenceTime: formatPresenceTime,
      missingVirtualGuiPlans: missingVirtualGuiPlans,
      virtualGuiCounts: virtualGuiCounts,
      // 3.4（DNN 配置卡纯函数；供离线 vm 测试）
      DNN_DOCS_URL: DNN_DOCS_URL,
      DNN_EXPERIMENTAL_TEXT: DNN_EXPERIMENTAL_TEXT,
      DNN_MODEL_IDS: DNN_MODEL_IDS,
      DNN_STATUS_POLL_MS: DNN_STATUS_POLL_MS,
      dnnModelOptions: dnnModelOptions,
      dnnDefaultSelection: dnnDefaultSelection,
      dnnCandidateFromStatus: dnnCandidateFromStatus,
      dnnCandidateInvalidReason: dnnCandidateInvalidReason,
      dnnShouldApplyAfterDownload: dnnShouldApplyAfterDownload,
      dnnApplyPayload: dnnApplyPayload,
      dnnRpcPayload: dnnRpcPayload,
      dnnFormUpdate: dnnFormUpdate,
      dnnControlState: dnnControlState,
      dnnStatusText: dnnStatusText,
      dnnDownloadPromptVisible: dnnDownloadPromptVisible,
      dnnProbeSummary: dnnProbeSummary,
      dnnProbeText: dnnProbeText,
      dnnProbeChecksText: dnnProbeChecksText,
      dnnCanRollback: dnnCanRollback,
      dnnFormatBytes: dnnFormatBytes,
      dnnCanCancelPending: dnnCanCancelPending,
      dnnDownloadSelectionText: dnnDownloadSelectionText,
      dnnErrorText: dnnErrorText,
    }
    return module.exports
  },
})
