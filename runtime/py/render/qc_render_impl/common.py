# -*- coding: utf-8 -*-
"""DSH 内置渲染 harness（v0.9.1：93 反馈 A1/A2/E1/B1-B2 —— 未知名硬失败 / QC 默认 Standard / 渲染锁 / 可调用 API）
—— 一次调用出多视角 QC 图。

外部反馈原话：入参 {file, views[], res, samples, thr, outdir} → 自动按 bbox 精确取景
+ 固定三点光 + 逐张 jsonl 计时 + 不超过 budget 的判定 —— "这是任何建模任务都要重写一遍的东西"。

v0.8.11 从 SpatiaOS/Procedura（MIT；本地镜像 .procedura-ref/，评估见 docs/Procedura-融合分析.md）
搬来三件事（只搬判据与算法，不搬它的 TS/OpenSCAD 栈）：
  A. 逐部件配色（src/render/parts_color.ts + scripts/_render_parts_color_blender.py）
     → mode="parts_color"：一部件一色 + 12 色循环调色板 + 图例文件 + **出图后完整还原**；
  B. 实测设备行（src/render/device_line.ts + scripts/_blender_gpu.py）
     → jsonl 每行记 device：Cycles 会**静默回落 CPU**（约 7× 慢、零症状），
       "这一张到底跑的哪个后端"必须落在产物里；拿不到就 null + device_source，不许猜；
  C. 视角目录 + 别名归一（src/render/views.ts）
     → 22 个具名视角收成 4 组目录 + 别名容错 + 未知名原样回传
       （旧版会把认不出的名字**静默**当成 iso 渲：出图正常、但渲的不是点名的角，报告里查不出来）。

v0.9.1 来自上线项目 rifle-build《93 反馈》—— 两条 P0（A1/A2，静默失败污染证据链）+ 一条 P2（E1）+ API 形态（B1/B2）：
  A1. **未知名视角不再静默替换**（v0.8.11 行为：4 个名字一个都认不出 → 仍 ok:true 并静默渲
      iso/front/right/top 四张 —— 文件名对、角度错、调用方看不出来）：
        · views **非空且一个都没解析成功** → **ok:false**，返回 unknown_views（原始拼写）/
          available_views（22 个具名视角）/ view_menu（分组速查文本）/ requested_count /
          rendered_count(=0) / hint —— **一张都不渲，也不兜底替换**；
        · views **省略/空** → 与 v0.8.11 一致的默认 3 视角（iso/front/right，见 DEFAULT_VIEWS_LEGACY）。
          要用 Procedura 的 4 视角集（iso/front/right/top）就**显式传** DEFAULT_VIEWS_FALLBACK 那四个名字
          —— 补丁版不静默改默认产物集；显式传 views 的语义一个都没变；
        · 部分未知名 → 已知名照渲，但返回体**必有** requested_count / rendered_count / unknown_count
          三个数字（成员踩过"12 个名字只渲 8 张，不报错"）；
        · 补**纯拼写**别名（side_left / left_view / topdown … → 规范名，见 VIEW_ALIASES）；
          语义模糊的一律不猜 —— `*_end`（muzzle_end / breech_end）**永不映射**（VIEW_NO_GUESS_SUFFIXES），
          进 unknown_views 由调用方按目录显式映射（枪械语境 muzzle_end→front 是调用方的判断，插件不替它猜）。
  A2. **QC 渲染默认 view_transform="Standard"**（v0.8.11 默认 null = 沿用场景；Blender 5.x 出厂 AgX 把
      纯白底压到 ≈0.85，凡"按亮度阈值裁参考/算剪影"的分析整轮失效）：
        · 缺省 = "Standard"（QC 口径：去掉 AgX/Filmic 洗淡，阈值判读才可靠）；
        · "scene" = 沿用场景设置（旧行为，给 beauty 图用）；具体名（"AgX"/"Filmic"/"Standard"）= 用那个；
        · 出图后**必须还原**场景 view_settings.view_transform（逐项对拍；没还原 → ok:false，
          与 parts_color 还原同款纪律：改了场景没还干净比少出一张图严重得多）；
        · 返回体与**每行 jsonl** 都给 view_transform（实际生效的那个）；传 "scene" 而场景不是 Standard
          时给 scope_warnings："当前是 AgX，阈值判读需自行校正"；名字写错/本机没有 → 渲染前就 ok:false
          （不许静默用别的色彩变换出图）。
  E1. **内置渲染锁**（替代各成员自造的 render_lock.py：多进程并发跑 EEVEE 抢同一块 GPU，实测有人等锁 48s）：
        · render_lock(action="acquire"|"release"|"status", holder, ttl_s, wait_s, note)；
          锁文件 <workdir>/locks/render.lock（workdir = K.workdir / K.out_dir，即各进程共享的工作目录），
          内容 JSON {holder, pid, acquired_at, ttl_s, note}；**过期判定只认时间戳**（跨进程唯一可靠判据，
          不能靠进程存活）；过期锁被打破时返回 stale_broken:true 并记录 warning；
        · qc_render_views 渲染前**自动 acquire**（lock_wait_s 默认 120s；lock=false 关掉），
          finally 里 release（异常也放；holder 匹配才删）；等不到锁 → ok:false（不静默渲一堆虚高的计时）；
        · 返回体与每行 jsonl 给 lock {holder, waited_ms, acquired, stale_broken, path} —— 计时口径可审计。
  B1/B2. K.dsh_qc_render_api 是 **dict 子类**（_DshApi）：既能 api["render_views"]（老写法，返回 str），
      也能 api("render_views", {...}) / api({...})（返回**已解析的 dict**）；api["dispatch"](op, json_str)
      仍返回 **str**（引擎契约不变）。

API 挂 K.dsh_qc_render_api。入口两条：
    blender_op(op="qc_render_views", args={...})                    # 走 qc_render 模块（engine 路由）
    blender_run(kind="headless", file=..., preload="qc,qc_render", script=...)    # 无头/作业层（长活、天然隔离）

参数（除 views 外都有默认值）：
    file        .blend 路径。与当前文件不同时：后台 -b 直接打开；GUI 默认拒绝（会顶掉用户场景），
                需要 allow_open_file=true 才开
    views       ["front","iso","top"] 或 [{"name","az","el","from","look_at","lens","ortho","res","margin"}]
                具名视角：front/back/left/right/top/bottom/iso/iso_l/iso_back/front_high（全 22 名见
                qc_render_catalog / view_menu）；也可写 "az=35,el=20"；省略/空 = ["iso","front","right"]
                （与 v0.8.11 一致；要 4 视角集就显式传 iso/front/right/top）。
                v0.9.1（A1）：**一个名字都认不出**时 ok:false 且一张都不渲（不再静默兜底）
    res         [w,h] 或单个数（默认 [512,512]）
    samples     采样数（EEVEE=taa_render_samples / Cycles=samples，默认 64）
    budget_s    累计渲染秒数上限（别名 thr）。超了立即停：已出的图与 jsonl 行保留，within_budget=false
    outdir      输出目录（Windows 或 WSL 路径都收，默认 K.out_dir）
    ref_path    可选参考图：每张渲染调 qc.py 的 compare 算 IoU/剖面差并写进 jsonl（需注入 qc.py）
    ref_box     参考图裁切框 [x0,y0,x1,y1]；也可给 {"front":[..], "iso":[..]} 按视角区分
    ref_search  参考比对是否用搜索对齐（默认 false = 固定对齐口径，防刷分）
    lights      {"key":4.0,"fill":1.2,"rim":2.5} 或 false 关闭；默认叠在场景现有灯光之上
    lights_mode "add"（默认）| "only"（临时屏蔽场景里其它灯，出图后还原）
    margin      取景余量（默认 1.12；越大留白越多）
    engine      "keep"（默认，跟随进程当前引擎）| "eevee"（EEVEE+光追）| "cycles"
    view_transform  色彩变换（v0.9.1 起**缺省 = "Standard"**：QC 口径去掉 AgX/Filmic 洗淡，阈值判读才可靠；
                "scene" = 沿用场景设置（v0.8.11 的旧行为，给 beauty 图用）；具体名 = 用那个（如 "AgX"）。
                无论哪种，出图后都还原场景设置；返回体与每行 jsonl 都记实际生效值）
    warmup      默认 true：先渲一帧预热（EEVEE 着色器编译）**不计时、不进 jsonl、不占预算** —— 单张耗时才是干净数
    ladder      降质阶梯（v0.8.10）：[{"samples":64,"scale":1.0},{"samples":32,"scale":0.75},…]；
                配 per_view_budget_s 时，某张超过预算就自动降一档重渲并逐级留痕（后续视角沿用该档）
    aabb/focus  世界坐标框选（v0.8.10）：aabb=[x0,y0,z0,x1,y1,z1] 或 focus="x,y,z,r"（顶层或按视角给）
    sheet       >1 时把已出的图拼成 N 宫格接触表（需 montage 模块；结果里给 sheet 路径与每格指标）
    tag         输出文件名前缀（默认 "views"）；jsonl 默认 <outdir>/render_views.jsonl

v0.8.11 新增参数（老参数一个都没动）：
    mode        "views"（默认）| "parts_color"（逐部件配色）；parts_color=true 等价于 mode="parts_color"
    parts_group_by  parts_color 的分组口径："object"（默认，一对象一色）| "collection"（一集合一色）
    parts_flat  默认 false = Principled 纯色（有明暗，能看出穿插/悬空/朝向）；
                true = Emission 纯色（不受灯光影响，图上颜色≈图例色，适合照着色标读图）
    parts_palette  自定义调色板 [[R,G,B], ...]（sRGB 0-255；默认 12 色，见 PARTS_PALETTE）
    parts_legend  图例 .txt 路径（默认 <outdir>/parts_color_meta.txt；同目录再写一份同名 .json 细节）
    catalog_ortho  默认 false：具名视角仍按老口径（透视），目录里的 ortho 只当语义标注；
                传 true 才按目录投影（六面正交、其余透视）—— 老图与历史分数保持逐像素可比
    view_order  "catalog"（默认：字符串具名视角按目录顺序 + 去重后渲染）| "request"（严格按请求顺序）
    strict_views  默认 false：未知视角名进 unknown_views 并跳过（已知视角照常渲）；true = 有未知名就 ok:false

v0.9.1 新增参数（老参数一个都没动）：
    lock        默认 true：渲染前自动取渲染锁（跨进程串行化，计时口径才可审计）；false/0/"off" = 关掉
    lock_wait_s 等锁上限秒数（默认 120）；等不到 → ok:false（不静默渲一批虚高的计时）
    lock_dir    锁的工作目录（默认 K.workdir / K.out_dir）→ 锁文件 <lock_dir>/locks/render.lock
                锁原语也可直接调：K.dsh_qc_render_api("render_lock", {"action": "status"})
    lock_holder 持有者名（默认 DSH_SESSION → DSH_RUN_ID → pid-<pid>）；**同一会话里并发跑多个渲染进程时
                必须显式给不同名字** —— 否则同名 holder 无法区分"谁持有"，极端情况（TTL 过期被打破后
                原进程才回来放锁）会误删别人的锁

产物：<outdir>/<tag>_<view>.png × N + <outdir>/render_views.jsonl（每行 {view, ms, bytes, hash, device, ...}）
      parts_color 模式另出 <outdir>/parts_color_meta.txt（图例：每行 `名字<TAB>R,G,B<TAB>面数`，
      纯数据行、行数 == 部件数，方便 `wc -l` 对拍）+ 同名 .json（调色板/分组/还原自检等细节）

纪律（与本项目其它模块一致）：
  · 相机与三点光**只在本进程内临时建**；finally 里删掉，scene 的渲染设置全部还原；
  · 不写 .blend、不动用户相机、不动用户灯光（除非 lights_mode="only"，也是出图后还原）；
  · GUI 里默认不打开别的 .blend —— 长活走 headless/作业层（天然隔离）。
  · v0.8.11：parts_color 覆盖的材质槽 / 临时材质 / 链接副本 mesh **全部还原并逐项核对**，
    核对不过直接 ok:false（改了场景没还原干净，比少出一张图严重得多 —— 这是本项目的历史教训）；
  · v0.8.11：视角名容错但**不猜**：未知名原样进 unknown_views，绝不静默替换成 iso。
  · v0.9.1：**认不出就失败**（一个都没认出来 → ok:false + 一张不渲）；渲染前取锁、finally 放锁；
    色彩变换出图后逐项核对还原，没还原 → ok:false。
"""
import bpy
import hashlib as hashlib
import json
import math as math
import os
import tempfile
import time as time

from mathutils import Vector as Vector

import numpy as np  # noqa: F401  # re-exported to the split qc_render modules


QC_RENDER_VERSION = 3      # v0.9.1：A1 未知名硬失败 / A2 QC 默认 Standard / E1 渲染锁 / B1-B2 可调用 API
DEFAULT_RES = (512, 512)
# 固定三点光（强度 W/m² 级的 SUN，与场景尺度无关）：key 3.2 / fill 1.0 / rim 2.4
DEFAULT_LIGHTS = {"key": 3.2, "fill": 1.0, "rim": 2.4}
# 相对**相机方位角**的固定角度（度）：key 右前上 · fill 左前平 · rim 后方轮廓
LIGHT_ANGLES = {"key": (35.0, 45.0), "fill": (-50.0, 8.0), "rim": (165.0, 30.0)}
LIGHT_SOFT = {"key": 5.0, "fill": 14.0, "rim": 6.0}

# ---------------------------------------------------------------- v0.8.11（C）：视角目录 + 别名归一化
# 为什么有它（Procedura src/render/views.ts 的教训）：视角名原来散在 NAMED_VIEWS 里，模型写
# "isometric" / "iso-FR-top" / "isoright" 都不命中具名表 → 掉进 az/el 解析 → 解析失败 → **静默落回 iso**：
# 出图看着正常，但渲的根本不是点名的那个角，历史报告里查不出来（本项目 234 张假证据那类事故的同款）。
# 目录 = 唯一真源：NAMED_VIEWS 由它派生（老名字、老角度逐项不变，见 LEGACY_NAMED_VIEWS 冻结快照），
# 别名只做**拼写**容错；语义模糊的名字（如 "side"）不猜，一律进 unknown_views。
# ortho 字段是语义标注（Procedura 的六面是 ortho=true），**默认不套用** —— 老调用（views=["front"]）
# 一直是透视，改了会让老图与历史分数不可逐像素比较；要按目录投影就传 catalog_ortho=true。
VIEW_CATALOG = [
    # ── 正交六面（group=face）：六个轴对齐面 ──
    {"name": "front", "group": "face", "az": -90.0, "el": 0.0, "ortho": True, "desc": "正面（相机在 −Y 看 +Y）"},
    {"name": "back", "group": "face", "az": 90.0, "el": 0.0, "ortho": True, "desc": "背面（+Y）"},
    {"name": "left", "group": "face", "az": 180.0, "el": 0.0, "ortho": True, "desc": "左面（−X）"},
    {"name": "right", "group": "face", "az": 0.0, "el": 0.0, "ortho": True, "desc": "右面（+X）"},
    {"name": "top", "group": "face", "az": 0.0, "el": 90.0, "ortho": True, "desc": "顶面（+Z，正上方往下看）"},
    {"name": "bottom", "group": "face", "az": 0.0, "el": -90.0, "ortho": True, "desc": "底面（−Z，正下方往上看）"},
    # ─ 等轴测角（group=corner）：8 个 3/4 英雄位（透视）；iso 是历史默认名（Procedura 叫 isometric）──
    {"name": "iso", "group": "corner", "az": -45.0, "el": 25.0, "ortho": False,
     "desc": "前右上 3/4（默认英雄位；Procedura 名 isometric / iso-FR-top）"},
    {"name": "iso_l", "group": "corner", "az": -135.0, "el": 25.0, "ortho": False, "desc": "前左上 3/4（iso-FL-top）"},
    {"name": "iso_br", "group": "corner", "az": 45.0, "el": 25.0, "ortho": False, "desc": "后右上 3/4（iso-BR-top；新增）"},
    {"name": "iso_back", "group": "corner", "az": 135.0, "el": 25.0, "ortho": False, "desc": "后左上 3/4（iso-BL-top）"},
    {"name": "iso_fr_bot", "group": "corner", "az": -45.0, "el": -25.0, "ortho": False, "desc": "前右下 3/4（仰视；新增）"},
    {"name": "iso_fl_bot", "group": "corner", "az": -135.0, "el": -25.0, "ortho": False, "desc": "前左下 3/4（新增）"},
    {"name": "iso_br_bot", "group": "corner", "az": 45.0, "el": -25.0, "ortho": False, "desc": "后右下 3/4（新增）"},
    {"name": "iso_bl_bot", "group": "corner", "az": 135.0, "el": -25.0, "ortho": False, "desc": "后左下 3/4（新增）"},
    # ── 平视对角（group=diagonal）：相邻两面、el=0（无俯仰）──
    {"name": "front_right", "group": "diagonal", "az": -45.0, "el": 0.0, "ortho": False, "desc": "平视，正面与右面之间（新增）"},
    {"name": "front_left", "group": "diagonal", "az": -135.0, "el": 0.0, "ortho": False, "desc": "平视，正面与左面之间（新增）"},
    {"name": "back_right", "group": "diagonal", "az": 45.0, "el": 0.0, "ortho": False, "desc": "平视，背面与右面之间（新增）"},
    {"name": "back_left", "group": "diagonal", "az": 135.0, "el": 0.0, "ortho": False, "desc": "平视，背面与左面之间（新增）"},
    # ── 英雄俯仰（group=tilt）：正面/右面 ±仰角。front_high / right_high 是历史名，**角度不许改** ──
    {"name": "front_high", "group": "tilt", "az": -90.0, "el": 35.0, "ortho": False,
     "desc": "正面俯视 35°（历史名，角度沿用；Procedura 的 front-high 是 15°，这里不跟）"},
    {"name": "front_low", "group": "tilt", "az": -90.0, "el": -15.0, "ortho": False, "desc": "正面仰视 15°（新增）"},
    {"name": "right_high", "group": "tilt", "az": 0.0, "el": 35.0, "ortho": False, "desc": "右面俯视 35°（历史名）"},
    {"name": "right_low", "group": "tilt", "az": 0.0, "el": -15.0, "ortho": False, "desc": "右面仰视 15°（新增）"},
]
VIEW_GROUPS = [("face", "正交六面（轴对齐面；目录标 ortho，默认仍按老口径透视）"),
               ("corner", "等轴测角（透视 3/4 英雄位）"),
               ("diagonal", "平视对角（相邻两面、无俯仰）"),
               ("tilt", "英雄俯仰（正面/右面 ±仰角）")]
NAMED_VIEWS = dict((v["name"], (v["az"], v["el"])) for v in VIEW_CATALOG)   # 老接口：名字 → (az, el)
VIEW_ORDER = [v["name"] for v in VIEW_CATALOG]                              # 目录顺序 = 渲染顺序（去重后）
VIEW_ORTHO = dict((v["name"], bool(v["ortho"])) for v in VIEW_CATALOG)
VIEW_DESC = dict((v["name"], str(v["desc"])) for v in VIEW_CATALOG)
VIEW_GROUP_OF = dict((v["name"], str(v["group"])) for v in VIEW_CATALOG)
# 老口径冻结快照（v0.8.8 的 NAMED_VIEWS 字面量）：自检逐项对拍，防"收敛目录"时手滑改了老角度
LEGACY_NAMED_VIEWS = {"front": (-90.0, 0.0), "back": (90.0, 0.0), "right": (0.0, 0.0), "left": (180.0, 0.0),
                      "top": (0.0, 90.0), "bottom": (0.0, -90.0), "iso": (-45.0, 25.0), "iso_l": (-135.0, 25.0),
                      "iso_back": (135.0, 25.0), "front_high": (-90.0, 35.0), "right_high": (0.0, 35.0)}
# 别名（键已归一：小写 + 去空格/下划线/连字符）→ 规范名。只做**拼写**容错，不做语义猜测。
# v0.9.1（93 反馈 A1-4）补两类：
#   ① 纯拼写同义词（side_left / left_view / topdown / front_view …）—— 语义无歧义才收；
#   ② 修正两条历史错映射：isoright / isobackright 原来都指到 iso（前右上）与 iso_back（后左上），
#      但目录里 iso_br 的 desc 就是 "iso-BR-top"（后右上）→ 全部收敛到 iso_br（与 isobrtop 一致）。
# 语义模糊的**不猜**（side / muzzle / breech / 任何 `*_end`）：见 VIEW_NO_GUESS_SUFFIXES 与
# qc_render_catalog.no_guess —— 调用方要按自己项目的目录显式映射（如枪械 muzzle_end → front）。
VIEW_ALIASES = {"isometric": "iso", "isofrtop": "iso", "isofrontright": "iso",
                "isoright": "iso_br", "isobrtop": "iso_br", "isobackright": "iso_br",
                "isofltop": "iso_l", "isofrontleft": "iso_l", "isoleft": "iso_l",
                "isobltop": "iso_back", "isobackleft": "iso_back",
                "isofrbot": "iso_fr_bot", "isoflbot": "iso_fl_bot",
                "isobrbot": "iso_br_bot", "isoblbot": "iso_bl_bot",
                # —— v0.9.1 拼接/词序类拼写容错（只此三种写法，不做语义联想）——
                "sideleft": "left", "leftside": "left", "leftview": "left",
                "sideright": "right", "rightside": "right", "rightview": "right",
                "frontview": "front", "backview": "back",
                "topview": "top", "topdown": "top", "bottomview": "bottom"}
# v0.9.1（A1）：**不猜**纪律 —— 归一化后以这些后缀结尾的名字永不映射（哪怕将来有人往别名表里加）。
# 为什么单独列：`muzzle_end` / `breech_end` 看着像"某个端面"，但映射到哪个正交面是**项目语义**
# （枪口朝哪、哪边算 front），插件替调用方猜一次，就会在报告里留下一张"文件名对、角度错"的图。
VIEW_NO_GUESS_SUFFIXES = ("end",)
VIEW_NO_GUESS_NOTE = ("后缀 *_end（muzzle_end / breech_end …）与语义模糊名（side / muzzle …）一律不猜："
                      "原样进 unknown_views，请调用方按本项目的视角目录显式映射（如 muzzle_end → front）")

# v0.9.1（A1）定稿：**省略/空 views 仍走 v0.8.11 的老默认（3 张）** —— 补丁版不静默改默认产物集。
DEFAULT_VIEWS_LEGACY = ["iso", "front", "right"]
# Procedura 口径的 4 视角集（iso/front/right/top）：保留导出，调用方**显式**要就用它。
# 注意：v0.9.0 曾把"全部未知名"兜底到它，v0.9.1 起那种情况直接 ok:false（A1），所以它现在只是"推荐的一组视角"。
DEFAULT_VIEWS_FALLBACK = ["iso", "front", "right", "top"]



import sys as _sys_kit
_KIT = getattr(_sys_kit.modules.get("dsh_rt_kernel"), "dsh_kit", None)
if _KIT is None:
    raise RuntimeError("qc_render 需要共享内核 K.dsh_kit（由 KERNEL_BOOTSTRAP 注入）")


_j = _KIT.j  # 共享内核（原自带实现已删，见 S1）
def _unpack_args(args):
    """args 解包（dispatch 与 qc_render_views 共用同一口径）：
    JSON 字符串 / None / {"args": {...}} 再包一层 → dict（照抄 v0.8.8 的包一层解包写法）。"""
    a = args
    if isinstance(a, str):
        s = a.strip()
        a = json.loads(s) if s else {}
    if not isinstance(a, dict):
        return {}
    if "args" in a and isinstance(a["args"], dict):
        merged = dict(a["args"])
        merged.update((k, v) for k, v in a.items() if k != "args")
        a = merged
    return dict(a)


def _norm_view_name(s):
    """视角名归一化：小写 + 只留字母数字（"iso_l" / "iso-L" / "iso l" 都 → "isol"）。"""
    return "".join(ch for ch in str(s).strip().lower() if ch.isalnum())


_NORM2CANON = dict((_norm_view_name(n), n) for n in VIEW_ORDER)


def _canon_view(name):
    """拼写 → 规范名；不命中返回 None（**不猜**：交给调用方报 unknown_views）。

    v0.9.1（A1）：先过**不猜**纪律 —— 归一化后以 `*_end` 结尾的名字直接 None（不进别名表兜底）。
    """
    k = _norm_view_name(name)
    if not k:
        return None
    if k.endswith(VIEW_NO_GUESS_SUFFIXES):
        return None                       # 不猜：muzzle_end / breech_end … 由调用方按目录显式映射
    if k in _NORM2CANON:
        return _NORM2CANON[k]
    return VIEW_ALIASES.get(k)


def _no_guess_hit(name):
    """这个名字是不是被**不猜纪律**挡下的（用于返回体里区分"没这个名"与"故意不猜"）。"""
    k = _norm_view_name(name)
    return bool(k) and k.endswith(VIEW_NO_GUESS_SUFFIXES)



def _is_azel_spec(s):
    """老口径的 "az=35,el=20" 写法仍走解析（v0.8.8 行为，不許失效）；其余未知名一律进 unknown_views。"""
    t = str(s).lower().replace(" ", "")
    return ("az=" in t) or ("el=" in t)


def resolve_view_names(names):
    """字符串视角名列表 → {"names": 规范名[目录顺序+去重], "unknown": 原始拼写, "aliases": 命中记录,
    "deduped": 重复项, "no_guess": 被不猜纪律挡下的原始拼写}。

    Procedura 口径（src/render/views.ts resolveViews）：先归一到规范名，未知名**原样留下**由调用方报警，
    已知名按**目录顺序**输出 —— 顺序固定，跨次/跨会话才可比。
    v0.9.1（A1）：这里**不再**说"全部无效时回落 DEFAULT_VIEWS_FALLBACK" —— 由 qc_render_views 判
    "非空且一个都没认出来 = ok:false"（空/省略才用默认）；resolve 本身只做纯函数归一，不替调用方兜底。
    """
    want, unknown, aliases, deduped, no_guess = [], [], [], [], []
    seen = set()
    for raw in (names or []):
        s = str(raw).strip()
        if not s:
            continue
        canon = _canon_view(s)
        if canon is None:
            unknown.append(str(raw))          # 保留**用户原始拼写**，不是归一后的
            if _no_guess_hit(s):
                no_guess.append(str(raw))
            continue
        if _norm_view_name(s) != _norm_view_name(canon):
            aliases.append({"requested": str(raw), "resolved": canon})
        if canon in seen:
            deduped.append(str(raw))
            continue
        seen.add(canon)
        want.append(canon)
    return {"names": [n for n in VIEW_ORDER if n in seen], "unknown": unknown,
            "aliases": aliases, "deduped": deduped, "no_guess": no_guess}


def view_menu_text():
    """速查文本：按组分列全部具名视角（进 qc_render_help 的 view_menu，也供提示词直接点名）。"""
    lines = []
    for g, label in VIEW_GROUPS:
        names = [str(v["name"]) for v in VIEW_CATALOG if str(v["group"]) == g]
        lines.append("  • %s: %s" % (label, ", ".join(names)))
    return "\n".join(lines)


def view_menu_full_text():
    """view_menu_text() + 别名表 + **不猜**纪律（v0.9.1 A1）：给"名字写错了该怎么办"一个就地答案。"""
    return (view_menu_text()
            + "\n  别名（纯拼写容错）：" + ", ".join("%s→%s" % (k, v) for k, v in sorted(VIEW_ALIASES.items()))
            + "\n  不猜：" + VIEW_NO_GUESS_NOTE)



def qc_render_view_catalog():
    """视角目录（机器可读）：分组 / az,el / ortho 语义 / 人类可读描述 / 别名 / 兜底视角。"""
    return _j({"ok": True, "version": QC_RENDER_VERSION,
               "groups": [{"id": g, "label": label,
                           "views": [dict(v) for v in VIEW_CATALOG if str(v["group"]) == g]}
                          for g, label in VIEW_GROUPS],
               "order": list(VIEW_ORDER), "aliases": dict(VIEW_ALIASES),
               "alias_note": "别名只做**拼写**容错（大小写/下划线/连字符/词序都不敏感），不做语义猜测；"
                             "v0.9.1 修正：isoright / isobackright → iso_br（目录里 iso-BR-top = 后右上）",
               "no_guess": {"suffix": list(VIEW_NO_GUESS_SUFFIXES), "note": VIEW_NO_GUESS_NOTE,
                            "examples": ["muzzle_end", "breech_end"]},
               "default_views": list(DEFAULT_VIEWS_LEGACY),
               "default_views_fallback": list(DEFAULT_VIEWS_FALLBACK),
               "default_views_legacy": list(DEFAULT_VIEWS_LEGACY),
               "default_views_note": "views 省略/空 = default_views_legacy（3 张，与 v0.8.11 一致）；要 4 视角集就显式传 default_views_fallback 那四个名字",
               "view_transform_default": "Standard",
               "view_transform_note": "QC 口径默认 Standard（AgX/Filmic 会把白底压到 ≈0.85，阈值判读失效）；"
                                      "传 \"scene\" 沿用场景设置；出图后一律还原",
               "unknown_views_policy": "views 非空且一个都没认出来 → ok:false（一张不渲、不兜底替换）；"
                                       "部分未知名 → 已知名照渲 + requested_count/rendered_count/unknown_count",
               "lock": {"op": "render_lock", "path": "<workdir>/locks/render.lock",
                        "actions": ["acquire", "release", "status"],
                        "note": "跨进程渲染锁（时间戳判过期）；qc_render_views 渲染前自动 acquire、finally release"},
               "legacy_named_views": dict(LEGACY_NAMED_VIEWS),
               "menu_text": view_menu_full_text(),
               "ortho_note": "目录里的 ortho 是语义标注；实际投影默认按老口径（透视），"
                             "要按目录投影传 catalog_ortho=true"})


def _kernel():
    import sys
    return sys.modules.get("dsh_rt_kernel")


def _win(path):
    """WSL → Windows 侧路径（Blender 是 Windows 进程）。K.win_path 可用时用它。"""
    K = _kernel()
    if K is not None and hasattr(K, "win_path"):
        try:
            return K.win_path(path)
        except Exception:
            pass
    s = str(path)
    if s.startswith("/mnt/") and len(s) > 6:
        return s[5].upper() + ":" + chr(92) + s[7:].replace("/", chr(92))
    return s


def _out_dir():
    K = _kernel()
    d = getattr(K, "out_dir", None) if K is not None else None
    return d or os.path.join(tempfile.gettempdir(), "dsh_qc_render")


def _work_dir():
    """**工作目录**（不是单次调用的 outdir）：v0.9.1（E1）渲染锁的基址。

    取 K.workdir（engine 注入 = K.out_dir，同一启动器下的 GUI/无头/worker 进程**共享同一个**工作目录，
    所以锁是跨进程可见的 —— 并发方各自是独立无头进程，只有共享文件系统能协调），
    再退到 K.out_dir、DSH_OUTDIR 环境变量、临时目录。
    """
    K = _kernel()
    for attr in ("workdir", "out_dir"):
        d = getattr(K, attr, None) if K is not None else None
        if d:
            return str(d)
    return os.environ.get("DSH_OUTDIR") or os.path.join(tempfile.gettempdir(), "dsh_qc_render")


# ---------------------------------------------------------------- v0.9.1（A2）：本机可用的色彩变换清单
# 为什么不用 bl_rna 的 enum_items：Blender 5.x 的 view_transform 是 OCIO **动态枚举**，实测
# `bl_rna.properties["view_transform"].enum_items` 只回 ['NONE']（拿不到完整清单）；"逐个试写 + 读回"
# 是唯一可靠判据（名字本机没有时赋值会抛 enum not found），用完把原值写回去。
VT_PROBE_CANDIDATES = ("Standard", "AgX", "Filmic", "Filmic Log", "Khronos PBR Neutral", "Raw",
                       "False Color", "Linear", "Linear Rec.709", "sRGB", "None")


def _vt_options():
    """实测本机可用的 view_transform 名单（试写 + 读回，最后**还原原值**）。"""
    vs = getattr(bpy.context.scene, "view_settings", None)
    if vs is None:
        return []
    cur = str(getattr(vs, "view_transform", "") or "")
    opts = []
    try:
        for c in VT_PROBE_CANDIDATES:
            try:
                vs.view_transform = c
                if str(getattr(vs, "view_transform", "")) == c and c not in opts:
                    opts.append(c)
            except Exception:
                pass
    finally:
        if cur:
            try:
                vs.view_transform = cur
            except Exception:
                pass
    return opts


# ---------------------------------------------------------------- v0.9.1（E1）：跨进程渲染锁
# 为什么在 python 侧做（93 反馈 E1）：并发方各自是**独立无头进程**，没有共享内存，只有共享文件系统；
# 实测多进程同抢一块 GPU 时单张耗时虚高（有人等锁 48s），成员只能自造 render_lock.py。
# 判过期的**唯一可靠判据是锁文件里的时间戳**（跨进程不能靠"进程还活着吗"：进程可能被 kill -9、
# 可能是另一台容器里的同名 pid、也可能正在无响应地跑渲染）；所以 TTL 一律按 acquired_at 算，
# 读不到 acquired_at 就退到文件 mtime（并把 age_source 标出来，不假装知道）。
LOCK_POLL_S = 0.25
LOCK_DEFAULT_TTL_S = 600.0




__all__ = [name for name in list(globals()) if not name.startswith("__")]
