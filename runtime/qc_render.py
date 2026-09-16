# -*- coding: utf-8 -*-
"""DSH 内置渲染 harness（v0.8.11：Procedura 融合 —— 逐部件配色 / 实测设备行 / 视角目录）—— 一次调用出多视角 QC 图。

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

API 挂 K.dsh_qc_render_api。入口两条：
    blender_rt_plan(op="qc_render_views", args={...})                    # 走 qc_render 模块（engine 路由）
    blender_rt_headless(file=..., preload="qc,qc_render", script=...)    # 无头/作业层（长活、天然隔离）

参数（除 views 外都有默认值）：
    file        .blend 路径。与当前文件不同时：后台 -b 直接打开；GUI 默认拒绝（会顶掉用户场景），
                需要 allow_open_file=true 才开
    views       ["front","iso","top"] 或 [{"name","az","el","from","look_at","lens","ortho","res","margin"}]
                具名视角：front/back/left/right/top/bottom/iso/iso_l/iso_back/front_high
                也可写 "az=35,el=20"；默认 ["iso","front","right"]
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
    view_transform  色彩变换（默认 null = 保留场景设置；给 "Standard" 可去掉 AgX 洗淡）
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
"""
import bpy
import hashlib
import json
import math
import os
import tempfile
import time

import numpy as np
from mathutils import Vector

QC_RENDER_VERSION = 2
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
# 别名（键已归一：小写 + 去空格/下划线/连字符）→ 规范名。只做拼写容错，不做语义猜测。
VIEW_ALIASES = {"isometric": "iso", "isofrtop": "iso", "isoright": "iso", "isofrontright": "iso",
                "isofltop": "iso_l", "isofrontleft": "iso_l",
                "isobrtop": "iso_br", "isobackright": "iso_br",
                "isobltop": "iso_back", "isobackleft": "iso_back",
                "isofrbot": "iso_fr_bot", "isoflbot": "iso_fl_bot",
                "isobrbot": "iso_br_bot", "isoblbot": "iso_bl_bot"}
# v0.8.11：views 一个有效的都没有时的兜底 4 视角（Procedura DEFAULT_VIEWS 口径）
DEFAULT_VIEWS_FALLBACK = ["iso", "front", "right", "top"]
# views 参数**缺省**时的老默认（v0.8.8 行为：三点就三点，一个都不许变）
DEFAULT_VIEWS_LEGACY = ["iso", "front", "right"]


def _j(o):
    return json.dumps(o, ensure_ascii=False, default=str)


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
    """拼写 → 规范名；不命中返回 None（**不猜**：交给调用方报 unknown_views）。"""
    k = _norm_view_name(name)
    if not k:
        return None
    if k in _NORM2CANON:
        return _NORM2CANON[k]
    return VIEW_ALIASES.get(k)


def _is_azel_spec(s):
    """老口径的 "az=35,el=20" 写法仍走解析（v0.8.8 行为，不許失效）；其余未知名一律进 unknown_views。"""
    t = str(s).lower().replace(" ", "")
    return ("az=" in t) or ("el=" in t)


def resolve_view_names(names):
    """字符串视角名列表 → {"names": 规范名[目录顺序+去重], "unknown": 原始拼写, "aliases": 命中记录, "deduped": 重复项}。

    Procedura 口径（src/render/views.ts resolveViews）：先归一到规范名，未知名**原样留下**由调用方报警，
    已知名按**目录顺序**输出 —— 顺序固定，跨次/跨会话才可比。全部无效时由调用方回落 DEFAULT_VIEWS_FALLBACK。
    """
    want, unknown, aliases, deduped = [], [], [], []
    seen = set()
    for raw in (names or []):
        s = str(raw).strip()
        if not s:
            continue
        canon = _canon_view(s)
        if canon is None:
            unknown.append(str(raw))          # 保留**用户原始拼写**，不是归一后的
            continue
        if _norm_view_name(s) != _norm_view_name(canon):
            aliases.append({"requested": str(raw), "resolved": canon})
        if canon in seen:
            deduped.append(str(raw))
            continue
        seen.add(canon)
        want.append(canon)
    return {"names": [n for n in VIEW_ORDER if n in seen], "unknown": unknown,
            "aliases": aliases, "deduped": deduped}


def view_menu_text():
    """速查文本：按组分列全部具名视角（进 qc_render_help 的 view_menu，也供提示词直接点名）。"""
    lines = []
    for g, label in VIEW_GROUPS:
        names = [str(v["name"]) for v in VIEW_CATALOG if str(v["group"]) == g]
        lines.append("  • %s: %s" % (label, ", ".join(names)))
    return "\n".join(lines)


def qc_render_view_catalog():
    """视角目录（机器可读）：分组 / az,el / ortho 语义 / 人类可读描述 / 别名 / 兜底视角。"""
    return _j({"ok": True, "version": QC_RENDER_VERSION,
               "groups": [{"id": g, "label": label,
                           "views": [dict(v) for v in VIEW_CATALOG if str(v["group"]) == g]}
                          for g, label in VIEW_GROUPS],
               "order": list(VIEW_ORDER), "aliases": dict(VIEW_ALIASES),
               "default_views_fallback": list(DEFAULT_VIEWS_FALLBACK),
               "default_views_legacy": list(DEFAULT_VIEWS_LEGACY),
               "legacy_named_views": dict(LEGACY_NAMED_VIEWS),
               "menu_text": view_menu_text(),
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


def _safe(name):
    s = "".join((c if (c.isalnum() or c in "-_.") else "_") for c in str(name))
    return s.strip("_") or "view"


def _md5(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------- 目标 AABB + 取景

def _collect_objs(names=None):
    scn = bpy.context.scene
    skipped = {"hidden": 0, "empty": 0, "missing": []}
    objs = []
    if names:
        for n in names:
            ob = bpy.data.objects.get(str(n))
            if ob is None:
                skipped["missing"].append(str(n))
                continue
            objs.append(ob)
        return objs, skipped
    for ob in scn.objects:
        if ob.type != "MESH":
            continue
        if ob.hide_render:
            skipped["hidden"] += 1
            continue
        try:
            if not ob.visible_get():
                skipped["hidden"] += 1
                continue
        except Exception:
            pass
        if ob.data is None or len(ob.data.vertices) == 0:
            skipped["empty"] += 1
            continue
        objs.append(ob)
    return objs, skipped


def _aabb(objs):
    mn = Vector((1e30, 1e30, 1e30))
    mx = Vector((-1e30, -1e30, -1e30))
    n = 0
    for ob in objs:
        mw = ob.matrix_world
        for c in ob.bound_box:
            p = mw @ Vector((c[0], c[1], c[2]))
            for i in range(3):
                if p[i] < mn[i]:
                    mn[i] = p[i]
                if p[i] > mx[i]:
                    mx[i] = p[i]
        n += 1
    if n == 0:
        return None, None, 0
    return mn, mx, n


def _corners(mn, mx):
    return [Vector((x, y, z)) for x in (mn[0], mx[0]) for y in (mn[1], mx[1]) for z in (mn[2], mx[2])]


def _fov_tans(w, h, lens, sensor, sensor_fit="AUTO"):
    """与 bpy Camera.calc_matrix_camera 同一口径（AUTO：sensor 贴长边），返回 (tan_x, tan_y)。"""
    fit = ("H" if w >= h else "V") if str(sensor_fit).upper().startswith("AUTO") else str(sensor_fit)[0].upper()
    if fit == "H":
        hx = sensor / 2.0
        hy = hx * (h / float(w))
    else:
        hy = sensor / 2.0
        hx = hy * (w / float(h))
    return hx / max(1e-9, float(lens)), hy / max(1e-9, float(lens))


def _basis(az, el):
    """(相机方位角, 仰角) → (center→camera 单位向量, 视线方向, right, up)。"""
    a = math.radians(float(az))
    e = math.radians(float(el))
    away = Vector((math.cos(e) * math.cos(a), math.cos(e) * math.sin(a), math.sin(e)))
    d = -away
    up_hint = Vector((0.0, 0.0, 1.0))
    if abs(d.dot(up_hint)) > 0.999:
        up_hint = Vector((0.0, 1.0, 0.0))
    right = d.cross(up_hint)
    right = right.normalized() if right.length > 1e-9 else Vector((1.0, 0.0, 0.0))
    up = right.cross(d).normalized()
    return away, d, right, up


def _solve_view(center, corners, az, el, w, h, lens, sensor, margin, ortho=False, ortho_scale=None):
    """解相机位置：让 AABB 八角全部落在视锥内，再乘 margin 留白。返回 (loc, d, ortho_scale_used, 统计)。"""
    away, d, right, up = _basis(az, el)
    tx, ty = _fov_tans(w, h, lens, sensor)
    half_x = half_y = 0.0
    dist = 0.0
    for p in corners:
        r = p - center
        x = r.dot(right)
        y = r.dot(up)
        z = r.dot(-d)          # 朝相机方向为正
        half_x = max(half_x, abs(x))
        half_y = max(half_y, abs(y))
        if not ortho:
            dist = max(dist, abs(x) / tx - z, abs(y) / ty - z)
    if ortho:
        if w >= h:
            need = max(2.0 * half_x, 2.0 * half_y * (w / float(h)))
        else:
            need = max(2.0 * half_y, 2.0 * half_x * (h / float(w)))
        scale = float(ortho_scale) if ortho_scale else need * float(margin)
        dd = max(2.0 * max(half_x, half_y), 1e-3) * 4.0
        return center - d * dd, d, scale, {"half_x": half_x, "half_y": half_y, "dist": dd}
    dist = max(dist * float(margin), 1e-3)
    return center - d * dist, d, None, {"half_x": half_x, "half_y": half_y, "dist": dist}


def _proj_matrix_fallback(cam, w, h):
    """与 view.py 同源的投影矩阵（calc_matrix_camera 不可用时的兜底），相机在原点朝 -Z。"""
    from mathutils import Matrix
    cd = cam.data
    lens = float(cd.lens)
    sensor = float(cd.sensor_width)
    fit = str(cd.sensor_fit)
    tx, ty = _fov_tans(w, h, lens, sensor, fit)
    if cd.type == "ORTHO":
        sx = float(cd.ortho_scale)
        if w >= h:
            p00, p11 = 2.0 / sx, 2.0 / (sx * (h / float(w)))
        else:
            p00, p11 = 2.0 / (sx * (w / float(h))), 2.0 / sx
        return Matrix(((p00, 0.0, 2.0 * cd.shift_x, 0.0), (0.0, p11, 2.0 * cd.shift_y, 0.0),
                       (0.0, 0.0, -2.0 / (cd.clip_end - cd.clip_start),
                        -(cd.clip_end + cd.clip_start) / (cd.clip_end - cd.clip_start)), (0.0, 0.0, 0.0, 1.0)))
    p00 = 1.0 / tx
    p11 = 1.0 / ty
    cs, ce = float(cd.clip_start), float(cd.clip_end)
    return Matrix(((p00, 0.0, 2.0 * cd.shift_x, 0.0), (0.0, p11, 2.0 * cd.shift_y, 0.0),
                   (0.0, 0.0, -(ce + cs) / (ce - cs), -(2.0 * ce * cs) / (ce - cs)), (0.0, 0.0, -1.0, 0.0)))


def _fallback_px(cam, points, w, h):
    """自算投影矩阵 → 像素（与 view.py 同源；Blender 5.2 起 Camera.calc_matrix_camera 已被移除）。"""
    pm = _proj_matrix_fallback(cam, w, h)
    vm = cam.matrix_world.inverted()
    xs, ys = [], []
    for p in points:
        # 注意：mathutils 里 4x4 @ 3D 向量不做透视除法 → 显式拼成 4D 点
        v = vm @ Vector((float(p[0]), float(p[1]), float(p[2])))
        c = pm @ Vector((float(v[0]), float(v[1]), float(v[2]), 1.0))
        ww = float(c[3])
        if abs(ww) < 1e-9:
            ww = 1e-9
        xs.append((float(c[0]) / ww * 0.5 + 0.5) * w)
        ys.append((0.5 - float(c[1]) / ww * 0.5) * h)
    return xs, ys


def _project_px(cam, points, w, h):
    """世界点 → 像素 bbox。主口径 = Blender 自己的 world_to_camera_view（真相），
    自算矩阵作对拍；两者逐点像素差进 proj_err_px（正常应在 1e-3 像素以内）。

    返回 (bbox, source, err_px)。
    """
    xs, ys = [], []
    src = "fallback"
    try:
        from bpy_extras.object_utils import world_to_camera_view
        scn = bpy.context.scene
        for p in points:
            c = world_to_camera_view(scn, cam, p)
            xs.append(float(c[0]) * w)
            ys.append((1.0 - float(c[1])) * h)
        src = "world_to_camera_view"
    except Exception:
        xs, ys, src = [], [], "fallback"
    fbx, fby = _fallback_px(cam, points, w, h)
    if src == "world_to_camera_view" and len(fbx) == len(xs):
        err = max([abs(a - b) for a, b in zip(xs, fbx)] + [abs(a - b) for a, b in zip(ys, fby)] or [0.0])
        err = round(float(err), 6)
    else:
        xs, ys, src, err = fbx, fby, "fallback", None
    return [round(min(xs), 1), round(min(ys), 1), round(max(xs), 1), round(max(ys), 1)], src, err


# ---------------------------------------------------------------- 灯光

# ---- v0.8.10（C5）：世界坐标框选（aabb=[x0,y0,z0,x1,y1,z1] / focus="x,y,z,r"）
def _focus_box(spec):
    """focus="x,y,z,r"（球心+半径，按外接立方取景）或 [x0,y0,z0,x1,y1,z1] → (min,max)；非法返回 None。"""
    if not spec:
        return None
    try:
        if isinstance(spec, (list, tuple)):
            v = [float(x) for x in spec]
            if len(v) == 6:
                mn = Vector((min(v[0], v[3]), min(v[1], v[4]), min(v[2], v[5])))
                mx = Vector((max(v[0], v[3]), max(v[1], v[4]), max(v[2], v[5])))
                return mn, mx
            if len(v) == 4:                       # x,y,z,r
                c = Vector((v[0], v[1], v[2]))
                r = abs(v[3])
                return c - Vector((r, r, r)), c + Vector((r, r, r))
            return None
        if isinstance(spec, dict):
            if "mn" in spec and "mx" in spec:
                return Vector([float(x) for x in spec["mn"]]), Vector([float(x) for x in spec["mx"]])
            if "center" in spec and "radius" in spec:
                c = Vector([float(x) for x in spec["center"]])
                r = abs(float(spec["radius"]))
                return c - Vector((r, r, r)), c + Vector((r, r, r))
            return None
        s = str(spec).strip()
        if not s:
            return None
        v = [float(x) for x in s.replace(" ", "").split(",")]
        return _focus_box(v)
    except Exception:
        return None


def _apply_step(rs, ladder, step_i, w, h):
    """按降质档设置分辨率/采样（v0.8.10 C2 的降质阶梯）。返回 (w_eff, h_eff, step_used)。"""
    if not ladder:
        rs.resolution_x, rs.resolution_y = w, h
        return w, h, None
    step_i = max(0, min(int(step_i), len(ladder) - 1))
    step = ladder[step_i] or {}
    sc_ = float(step.get("scale", 1.0) or 1.0)
    w_eff = max(16, int(round(w * sc_)))
    h_eff = max(16, int(round(h * sc_)))
    rs.resolution_x, rs.resolution_y = w_eff, h_eff
    n = step.get("samples")
    if n:
        scn = bpy.context.scene
        ee = getattr(scn, "eevee", None)
        if ee is not None and hasattr(ee, "taa_render_samples"):
            try:
                ee.taa_render_samples = int(n)
            except Exception:
                pass
        if hasattr(scn, "cycles"):
            try:
                scn.cycles.samples = int(n)
            except Exception:
                pass
    return w_eff, h_eff, step


def _db_counts():
    """datablock 计数（v0.8.10 D1：临时资源泄漏自检）。"""
    out = {}
    for k in ("objects", "meshes", "materials", "lights", "cameras", "images", "collections"):
        try:
            out[k] = len(getattr(bpy.data, k))
        except Exception:
            out[k] = None
    try:
        out["orphans"] = len([d for d in bpy.data.lights if d.users == 0]) + len([d for d in bpy.data.cameras if d.users == 0])
    except Exception:
        out["orphans"] = None
    return out


def _montage_api():
    """拿 montage 模块（拼图）：K.dsh_montage_api → 否则从 K.runtime_dir 现场加载 montage.py。"""
    K = _kernel()
    api = getattr(K, "dsh_montage_api", None) if K is not None else None
    if api:
        return api
    d = getattr(K, "runtime_dir", None) if K is not None else None
    if d:
        p = os.path.join(str(d), "montage.py")
        if os.path.isfile(p):
            with open(p, encoding="utf-8") as fh:
                src = fh.read()
            exec(compile(src, p, "exec"), {"__name__": "dsh_montage", "__file__": p})
            api = getattr(K, "dsh_montage_api", None)
    return api


def _add_rig(az, energies):
    made = []
    for key in ("key", "fill", "rim"):
        en = float((energies or {}).get(key, 0.0) or 0.0)
        if en <= 0:
            continue
        daz, el = LIGHT_ANGLES[key]
        a = math.radians(float(az) + daz)
        e = math.radians(el)
        from_dir = Vector((math.cos(e) * math.cos(a), math.cos(e) * math.sin(a), math.sin(e)))
        ld = bpy.data.lights.new("DSH_QC_LIGHT_" + key.upper(), type="SUN")
        ld.energy = en
        try:
            ld.angle = math.radians(LIGHT_SOFT[key])
        except Exception:
            pass
        ob = bpy.data.objects.new("DSH_QC_LIGHT_" + key.upper(), ld)
        bpy.context.scene.collection.objects.link(ob)
        ob.location = Vector((0.0, 0.0, 0.0))
        ob.rotation_mode = "QUATERNION"
        ob.rotation_quaternion = (-from_dir).to_track_quat("-Z", "Y")
        made.append(ob)
    return made


def _aim_rig(objs, az):
    for ob in objs:
        key = ob.name.rsplit("_", 1)[-1].lower()
        daz, el = LIGHT_ANGLES.get(key, (0.0, 45.0))
        a = math.radians(float(az) + daz)
        e = math.radians(el)
        from_dir = Vector((math.cos(e) * math.cos(a), math.cos(e) * math.sin(a), math.sin(e)))
        ob.rotation_quaternion = (-from_dir).to_track_quat("-Z", "Y")


# ---------------------------------------------------------------- 读回：alpha 包围盒

def _alpha_stats(path, thr=0.02):
    img = bpy.data.images.load(path, check_existing=False)
    try:
        w, h = int(img.size[0]), int(img.size[1])
        buf = np.empty(w * h * 4, dtype=np.float32)
        img.pixels.foreach_get(buf)
    finally:
        bpy.data.images.remove(img)
    a = buf.reshape(h, w, 4)[::-1, :, 3]
    m = a > float(thr)
    ys, xs = np.nonzero(m)
    if len(xs) == 0:
        return {"bbox": None, "coverage": 0.0, "alpha_max": round(float(a.max()), 4), "size": [w, h]}
    x0, x1, y0, y1 = int(xs.min()), int(xs.max()), int(ys.min()), int(ys.max())
    return {"bbox": [x0, y0, x1, y1], "margin_px": [x0, y0, w - 1 - x1, h - 1 - y1],
            "coverage": round(float(m.mean()), 4),
            "fill": round(float((x1 - x0 + 1) * (y1 - y0 + 1)) / float(w * h), 4),
            "alpha_max": round(float(a.max()), 4), "size": [w, h]}


# ---------------------------------------------------------------- 引擎 / 渲染设置

def _set_engine(mode):
    """mode: eevee | cycles | keep。返回 (before, after) 供还原。"""
    sc = bpy.context.scene
    before = sc.render.engine
    m = str(mode or "keep").lower()
    if m in ("eevee", "eevee_rt", "e"):
        try:
            sc.render.engine = "BLENDER_EEVEE"
        except Exception:
            sc.render.engine = "BLENDER_EEVEE_NEXT"
        ee = getattr(sc, "eevee", None)
        if ee is not None:
            for attr, val in (("use_raytracing", True), ("use_shadows", True)):
                if hasattr(ee, attr):
                    try:
                        setattr(ee, attr, val)
                    except Exception:
                        pass
            if hasattr(ee, "ray_tracing_method"):
                try:
                    ee.ray_tracing_method = "SCREEN"
                except Exception:
                    pass
    elif m in ("cycles", "c"):
        sc.render.engine = "CYCLES"
    return before, sc.render.engine


def _engine_label():
    sc = bpy.context.scene
    e = str(sc.render.engine)
    if "EEVEE" in e:
        ee = getattr(sc, "eevee", None)
        return e + ("+RT" if getattr(ee, "use_raytracing", False) else "")
    return e


def _apply_samples(n):
    sc = bpy.context.scene
    n = int(max(1, int(n)))
    done = {}
    ee = getattr(sc, "eevee", None)
    if ee is not None and hasattr(ee, "taa_render_samples"):
        done["eevee.taa_render_samples"] = int(ee.taa_render_samples)
        ee.taa_render_samples = n
    if hasattr(sc, "cycles"):
        try:
            done["cycles.samples"] = int(sc.cycles.samples)
            sc.cycles.samples = n
        except Exception:
            pass
    return done


# ---------------------------------------------------------------- v0.8.11（B）：实测渲染设备
# 为什么有它（Procedura src/render/device_line.ts 的原文教训）：Cycles 会**静默回落 CPU**（同一张约 7× 慢），
# 而 jsonl 里原来只有 {view, ms, bytes, hash} —— 慢的原因在产物里一个字都查不到；
# 他们那边 `_blender_gpu.py` 专门打印了 "[render] device: …"，却被上层"成功就把 stdout 扔掉"给吞了。
# 纪律：**只读 bpy 的真实状态，拿不到就 null + device_source**，绝不按引擎名猜设备型号。
# （perf.py 的 _gpu_setup / _pick_denoiser 是"设置"语义：它改设备；这里只读，不调用它、更不改 perf.py。）

def _cycles_prefs():
    """拿 cycles 偏好对象 → (prefs, None) 或 (None, 原因)。"""
    try:
        addon = bpy.context.preferences.addons.get("cycles")
        prefs = getattr(addon, "preferences", None) if addon else None
        if prefs is None:
            return None, "拿不到 preferences.addons['cycles'].preferences（addon 未启用或该构建无 Cycles）"
        return prefs, None
    except Exception as e:
        return None, "读 cycles 偏好抛错：%s: %s" % (type(e).__name__, str(e)[:120])


def _cycles_device_list(prefs):
    """实测设备列表 [{name,type,use}] —— 读之前快照 use、读之后原样回写，绝不动用户的设备勾选。

    坑（实测）：prefs.devices 在**没调过 get_devices()** 时是**空列表**（新起的 -b 进程里必然如此），
    直接读会得出"一个设备都没有"的假结论 —— 静默 CPU 那类误判就是这么来的。实测 5.2.2 上
    get_devices() 会保留 use 标记，但这里仍按"先快照后回写"处理：读操作不留副作用。
    """
    snap = {}
    try:
        for d in prefs.devices:
            snap[str(d.name)] = bool(d.use)
    except Exception:
        pass
    try:
        prefs.get_devices()
    except Exception as e:
        return None, "prefs.get_devices() 失败：%s: %s" % (type(e).__name__, str(e)[:120])
    out = []
    try:
        for d in prefs.devices:
            nm = str(d.name)
            if nm in snap:
                try:
                    d.use = snap[nm]       # 回写：读取不该改变用户的启用状态
                except Exception:
                    pass
            out.append({"name": nm, "type": str(d.type), "use": bool(d.use)})
    except Exception as e:
        return None, "遍历 prefs.devices 失败：%s: %s" % (type(e).__name__, str(e)[:120])
    return out, None


def _device_info():
    """这一次渲染会走哪个设备 —— {"engine","device","gpu","source","detail"}，device 可为 null（不许猜）。

    device=null 的情形都是**真的拿不到判据**，source 里逐条写明为什么；能拿到时给出判据出处：
      ① CYCLES：scene.cycles.device + preferences['cycles'].preferences.devices 里 use=True 的非 CPU 设备。
         · device=GPU 且确有启用的 GPU → device=<设备 type：OPTIX/CUDA/HIP/ONEAPI/METAL>、gpu=设备名；
         · device=GPU 但**没有任何启用的 GPU** → null，并在 source 里点名"Cycles 会静默回落 CPU（≈7×）"；
         · device=CPU → "CPU"（bpy 不区分"用户就要 CPU"与"GPU 不可用后的回落"，所以 CPU 本身不代表正常）；
      ② EEVEE / WORKBENCH：GPU 光栅化引擎、没有 CPU 路径 → device="GPU"（引擎语义，不是猜型号）；
         具体设备型号 bpy 不暴露（后台进程里 gpu.platform 需要 gpu.init，本模块**不调用**以免副作用）。
    """
    sc = bpy.context.scene
    eng = str(getattr(sc.render, "engine", "") or "")
    info = {"engine": eng or None, "device": None, "gpu": None, "source": None, "detail": {}}
    if not eng:
        info["source"] = "拿不到：scene.render.engine 为空"
        return info
    if "CYCLES" in eng:
        c = getattr(sc, "cycles", None)
        if c is None:
            info["source"] = "拿不到：引擎是 %s 但 scene.cycles 不存在 → 不猜" % eng
            return info
        cdev = str(getattr(c, "device", "") or "")
        prefs, perr = _cycles_prefs()
        devs, derr = (None, perr) if prefs is None else _cycles_device_list(prefs)
        ctype = None
        try:
            ctype = str(prefs.compute_device_type)
        except Exception:
            ctype = None
        info["detail"] = {"cycles_device": cdev or None, "compute_device_type": ctype,
                          "devices": devs, "devices_error": derr}
        enabled = [d for d in (devs or []) if d.get("use") and str(d.get("type")) != "CPU"]
        if cdev == "GPU" and enabled:
            info["device"] = str(enabled[0]["type"])
            info["gpu"] = ", ".join(sorted(set(str(d["name"]) for d in enabled)))
            info["source"] = ("bpy 实测：scene.cycles.device=GPU，且偏好里 use=True 的非 CPU 设备 = %s"
                              % info["gpu"])
        elif cdev == "GPU":
            info["source"] = ("拿不到：scene.cycles.device=GPU，但偏好里没有任何 use=True 的非 CPU 设备"
                              "（设备列表读取：%s）→ Cycles 会**静默回落 CPU**（约 7× 慢，就是 Procedura "
                              "device_line 要抓的那个坑）；bpy 不回报实际后端，故 device=null 不猜"
                              % (derr or "读到 0 个启用设备"))
        elif cdev == "CPU":
            info["device"] = "CPU"
            info["source"] = ("bpy 实测：scene.cycles.device=CPU —— 注意 bpy 不区分「用户就要 CPU」与"
                              "「GPU 不可用后的回落」，所以 device=CPU 本身**不代表正常**")
        else:
            info["source"] = "拿不到：scene.cycles.device=%r 既不是 CPU 也不是 GPU → 不猜" % cdev
        return info
    if "EEVEE" in eng or "WORKBENCH" in eng:
        info["device"] = "GPU"
        r = None
        try:
            import gpu
            r = "%s %s" % (str(gpu.platform.vendor_get()), str(gpu.platform.renderer_get()))
        except Exception as e:
            info["detail"] = {"gpu_platform_error": "%s: %s" % (type(e).__name__, str(e)[:100])}
        info["gpu"] = r
        info["source"] = (("引擎语义：%s 是 GPU 光栅化（无 CPU 路径）→ device=GPU；型号串来自 gpu.platform 实测"
                           % eng) if r else
                          ("引擎语义：%s 是 GPU 光栅化（无 CPU 路径）→ device=GPU；型号串这次拿不到"
                           "（-b 进程首次渲染前 gpu.platform 不可用，_device_live 会在渲完后补上）" % eng))
        return info
    info["source"] = "拿不到：未知引擎 %s 不知道走什么设备 → null 不猜（要判据就扩 _device_info）" % eng
    return info


def _device_slim(info):
    """jsonl 每行用的精简设备块（{engine,device,gpu,source}）—— 不塞设备全表，否则每行几百字节。"""
    d = {"engine": info.get("engine"), "device": info.get("device"), "gpu": info.get("gpu")}
    det = info.get("detail") or {}
    for k in ("cycles_device", "compute_device_type"):
        if det.get(k) is not None:
            d[k] = det[k]
    d["source"] = info.get("source")
    return d


def _device_line(info):
    """一行版（Procedura device_line.ts 的 `[render] device: …`）：抓日志时 grep 一行就够。"""
    if info.get("device") is None:
        return "[render] device: null — %s" % (info.get("source") or "拿不到")
    return "[render] device: %s%s (%s)" % (info.get("device"),
                                           (" — " + str(info["gpu"])) if info.get("gpu") else "",
                                           info.get("engine"))


def _device_live(base):
    """渲染完一张后的**即时回读**（entry 里那行 device 用这个）。

    为什么不能只在批前测一次：踩过的坑 ——
      · 后台进程里 `gpu.platform` 在**首次渲染之后**才可用（GL/Vulkan 上下文这时才起来）：
        批前测 EEVEE 只能给 device=GPU/gpu=null，渲染后能补上真实 renderer 串
        （实测："NVIDIA Corporation NVIDIA GeForce RTX 4060 Laptop GPU/PCIe/SSE2"）；
      · Cycles 的 `scene.cycles.device` 可以在批次中途被人改（脚本/驱动器），所以每张都重读一次；
        设备表沿用批内快照（每张都 get_devices() 没必要，纯读也会刷新设备列表缓存）。
    """
    info = dict(base or {})
    info["detail"] = dict((base or {}).get("detail") or {})
    eng = str(info.get("engine") or "")
    if "EEVEE" in eng or "WORKBENCH" in eng:
        if not info.get("gpu"):
            try:
                import gpu
                info["gpu"] = "%s %s" % (str(gpu.platform.vendor_get()), str(gpu.platform.renderer_get()))
                info["source"] = ("引擎语义：%s 是 GPU 光栅化（无 CPU 路径）→ device=GPU；renderer 串来自 "
                                  "渲染后实测 gpu.platform（后台进程首次渲染前拿不到）" % eng)
            except Exception as e:
                info["detail"]["gpu_platform_error_after_render"] = "%s: %s" % (type(e).__name__, str(e)[:100])
        return info
    if "CYCLES" in eng:
        try:
            now = str(bpy.context.scene.cycles.device or "")
        except Exception:
            now = None
        old = info["detail"].get("cycles_device")
        if now and now != old:
            info["detail"]["cycles_device_changed_from"] = old
            info["detail"]["cycles_device"] = now
            if now == "CPU":
                info["device"], info["gpu"] = "CPU", None
                info["source"] = ("实测：批次中途 scene.cycles.device 变成了 CPU（批前是 %s）→ 这一张是 CPU 渲的；"
                                  "bpy 不区分「用户要 CPU」与「GPU 不可用回落」" % old)
            elif now == "GPU":
                devs = [d for d in (info["detail"].get("devices") or [])
                        if d.get("use") and str(d.get("type")) != "CPU"]
                if devs:
                    info["device"] = str(devs[0]["type"])
                    info["gpu"] = ", ".join(sorted(set(str(d["name"]) for d in devs)))
                    info["source"] = "实测：批次中途切到 GPU 且启用设备 = %s" % info["gpu"]
                else:
                    info["device"], info["gpu"] = None, None
                    info["source"] = "拿不到：批次中途切到 GPU，但设备表里没有启用的非 CPU 设备 → 会静默回落 CPU"
        return info
    return info


# ---------------------------------------------------------------- v0.8.11（A）：逐部件配色（parts_color）
# 为什么有它（Procedura src/render/parts_color.ts + _render_parts_color_blender.py）：装配关系 / 穿插 /
# 悬空 / 镜像错位在 AO 图与 beauty 图里要"看"很久，逐部件一色则一眼可读；代价是**材质必须覆盖**，
# 覆盖就必须完整还原 —— 本项目 234 张假证据的教训：改了场景不还原，后面每一张渲染都是脏的。
# 判据与阈值来源：
#   · 12 色调色板（PARTS_PALETTE，sRGB 0-255）：Procedura 用 12 步循环；相邻色相拉开、明暗交替，
#     缩略图上也能分辨。色数不够**不报错**：多出来的组按名字稳定哈希摊到"当前用得最少"的颜色上。
#   · 图例 <outdir>/parts_color_meta.txt：每行 `名字<TAB>R,G,B<TAB>面数` —— 纯数据行（无表头），
#     所以 `wc -l` 就等于部件数（自检也这么断言）；调色板/分组/还原自检等细节写同名 .json。
PARTS_PALETTE = [
    (230, 60, 60), (60, 130, 230), (70, 200, 90), (240, 190, 50),
    (170, 90, 220), (40, 200, 200), (240, 130, 40), (130, 130, 240),
    (200, 220, 60), (240, 110, 180), (90, 200, 150), (150, 150, 150),
]


def _srgb_to_linear(v):
    """sRGB(0-1) → 线性。Base Color / Emission 吃的是线性值；把 sRGB 直接塞进去会整体偏亮，
    图例与图上颜色就对不上了（颜色编码类渲染，图例必须能对上图）。"""
    v = max(0.0, min(1.0, float(v)))
    return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4


def _palette_linear(rgb255):
    return tuple(round(_srgb_to_linear(float(c) / 255.0), 6) for c in rgb255[:3])


def _stable_hash(s):
    """跨进程稳定的字符串哈希。坑：Python 内置 hash() 带随机盐（PYTHONHASHSEED），
    同一场景两次渲染会换色 —— 确定性配色必须用 md5。"""
    return int(hashlib.md5(str(s).encode("utf-8")).hexdigest()[:8], 16)


def _parts_groups(objs, group_by):
    """部件分组 → [(组名, [obj, ...])]，按组名排序（确定性）。

    group_by="object"（默认，一对象一部件）| "collection"（一集合一部件：对象属于多个集合时取
    **名字最小**的那个集合 —— 别用 users_collection 的自然顺序，那是随场景结构变的）。
    """
    g = {}
    for ob in objs:
        if str(group_by) == "collection":
            cols = sorted([str(c.name) for c in ob.users_collection]) or ["(无集合)"]
            key = cols[0]
        else:
            key = str(ob.name)
        g.setdefault(key, []).append(ob)
    return sorted(g.items(), key=lambda kv: str(kv[0]))


def _parts_assign(groups, palette):
    """按组名排序分配颜色 → {组名: {"index","rgb","source"}}（同场景多次渲染配色一致）。

    · 组数 ≤ 色数：第 i 组吃 palette[i]（source="order"）；
    · 组数 > 色数：多出来的组按名字哈希排定次序，逐个塞进"当前用得最少"的颜色（source="hash"）——
      确定性 + 复用摊得最匀；**绝不因为色数不够报错**。
    """
    n = len(palette)
    asg = {}
    used = [0] * n
    for i, (name, _objs) in enumerate(groups):
        if i < n:
            asg[str(name)] = {"index": i, "rgb": [int(palette[i][0]), int(palette[i][1]), int(palette[i][2])],
                              "source": "order"}
            used[i] += 1
    rest = [str(nm) for i, (nm, _o) in enumerate(groups) if i >= n]
    for nm in sorted(rest, key=lambda s: (_stable_hash(s), s)):
        k = min(range(n), key=lambda j: (used[j], _stable_hash("%s#%d" % (nm, j))))   # 用得最少的色，哈希定序
        used[k] += 1
        asg[nm] = {"index": k, "rgb": [int(palette[k][0]), int(palette[k][1]), int(palette[k][2])],
                   "source": "hash"}
    return asg


def _make_part_material(idx, rgb255, flat=False):
    """纯色临时材质：默认 Principled（有明暗 → 能看出穿插/悬空/朝向；Procedura 也是这个口径），
    flat=True 用 Emission（不受灯光影响，图上颜色≈图例色，适合照着色标读图）。
    材质名带序号与色值（DSH_PC_07_F0BE32），事后在 .blend 里能一眼认出是谁留下的。"""
    rgb255 = [int(rgb255[0]), int(rgb255[1]), int(rgb255[2])]
    name = "DSH_PC_%02d_%02X%02X%02X" % (int(idx), rgb255[0], rgb255[1], rgb255[2])
    mat = bpy.data.materials.new(name)
    try:
        mat.use_nodes = True           # 5.2 唯一的开节点方式（6.0 起会移除这个开关 → 不写死）
    except Exception:
        pass
    lin = _palette_linear(rgb255)
    nt = getattr(mat, "node_tree", None)
    if nt is None:
        raise RuntimeError("材质 %s 拿不到 node_tree（Blender 版本差异）" % name)
    out = None
    for nd in nt.nodes:
        if nd.bl_idname == "ShaderNodeOutputMaterial":
            out = nd
    if out is None:
        out = nt.nodes.new("ShaderNodeOutputMaterial")
    if flat:
        nd = nt.nodes.new("ShaderNodeEmission")
        nd.inputs["Color"].default_value = (lin[0], lin[1], lin[2], 1.0)
        try:
            nd.inputs["Strength"].default_value = 1.0
        except Exception:
            pass
        nt.links.new(nd.outputs[0], out.inputs["Surface"])
    else:
        b = None
        for nd in nt.nodes:
            if nd.bl_idname == "ShaderNodeBsdfPrincipled":
                b = nd
        if b is None:
            b = nt.nodes.new("ShaderNodeBsdfPrincipled")
            nt.links.new(b.outputs[0], out.inputs["Surface"])
        b.inputs["Base Color"].default_value = (lin[0], lin[1], lin[2], 1.0)
        for k, v in (("Roughness", 0.55), ("Metallic", 0.0), ("Specular IOR Level", 0.3)):
            if k in b.inputs:
                try:
                    b.inputs[k].default_value = v
                except Exception:
                    pass
    return mat


def _parts_override(objs, assign_by_name, group_of, flat):
    """临时覆盖材质槽 → (state, mats_by_group, err)。state 逐对象记下还原所需的一切。

    两个坑（都实测过，写在注释里防后人踩）：
      · **链接副本**（多个对象共享同一 mesh）改 DATA 槽会串色到别的对象（实测 shared_bleeds=true）→
        先 `ob.data = ob.data.copy()` 隔离，还原时换回原 mesh 并删掉副本（mesh 计数必须回基线）；
      · **0 槽对象**要 append 一个槽才渲得出色，还原时 `mesh.materials.clear()` 清回 0 槽
        （直接赋 None 会留下一个空槽，槽数就对不上了）。
    """
    state, mats, err = [], {}, None
    mat_cache = {}
    for ob in objs:
        key = str(group_of(ob))
        asg = (assign_by_name or {}).get(key) or {}
        rgb = asg.get("rgb") or [int(PARTS_PALETTE[0][0]), int(PARTS_PALETTE[0][1]), int(PARTS_PALETTE[0][2])]
        idx = int(asg.get("index") or 0)
        mk = (idx, tuple(int(x) for x in rgb), bool(flat))
        mat = mat_cache.get(mk)
        if mat is None:
            mat = _make_part_material(idx, rgb, flat)
            mat_cache[mk] = mat
        mats[key] = mat
        st = {"object": str(ob.name), "group": key, "material": str(mat.name),
              "slots": [[str(s.link), (str(s.material.name) if s.material else None)] for s in ob.material_slots],
              "n_slots": len(ob.material_slots), "appended": False,
              "mesh_before": None, "mesh_copy": None}
        try:
            if int(getattr(ob.data, "users", 0) or 0) > 1:
                st["mesh_before"] = ob.data
                ob.data = ob.data.copy()
                st["mesh_copy"] = ob.data
            if len(ob.material_slots) == 0:
                ob.data.materials.append(mat)
                st["appended"] = True
            else:
                for s in ob.material_slots:
                    s.material = mat
        except Exception as e:
            err = "覆盖材质失败（%s）：%s: %s" % (ob.name, type(e).__name__, str(e)[:160])
            state.append(st)
            break
        state.append(st)
    return state, mats, err


def _parts_restore(state, mats):
    """还原 parts_color 现场：材质槽（含链接副本换回原 mesh、删副本）→ 删临时材质 → **逐项核对**。

    核对口径：还原后的 `[(slot.link, slot.material.name)]` 必须与覆盖前逐槽相等；
    核对不过 → 调用方把 ok 置 false（改了场景没还原干净，比少出一张图严重得多）。
    """
    rep = {"objects": len(state), "slots_restored": 0, "mesh_copies_removed": 0,
           "temp_materials_removed": 0, "temp_materials_left": [], "mismatches": [], "ok": False}
    for st in reversed(state):
        ob = bpy.data.objects.get(str(st.get("object")))
        if ob is None:
            rep["mismatches"].append("%s：对象已不在（无法核对材质槽）" % st.get("object"))
            continue
        try:
            if st.get("appended"):
                ob.data.materials.clear()
            else:
                for i, pair in enumerate(st.get("slots") or []):
                    if i >= len(ob.material_slots):
                        rep["mismatches"].append("%s：槽数 %d < 记录 %d"
                                                 % (ob.name, len(ob.material_slots), len(st.get("slots") or [])))
                        break
                    s = ob.material_slots[i]
                    if str(s.link) != str(pair[0]):
                        s.link = str(pair[0])
                    s.material = (bpy.data.materials.get(str(pair[1])) if pair[1] else None)
        except Exception as e:
            rep["mismatches"].append("%s：还原材质槽抛错 %s: %s"
                                     % (st.get("object"), type(e).__name__, str(e)[:140]))
        if st.get("mesh_copy") is not None:
            try:
                ob.data = st["mesh_before"]
                bpy.data.meshes.remove(st["mesh_copy"])
                rep["mesh_copies_removed"] += 1
            except Exception as e:
                rep["mismatches"].append("%s：链接副本 mesh 还原失败 %s: %s"
                                         % (st.get("object"), type(e).__name__, str(e)[:140]))
        try:
            now = [[str(s.link), (str(s.material.name) if s.material else None)] for s in ob.material_slots]
            if now != [list(x) for x in (st.get("slots") or [])]:
                rep["mismatches"].append("%s：材质槽与渲染前不等 %s != %s"
                                         % (ob.name, now, st.get("slots")))
            else:
                rep["slots_restored"] += 1
        except Exception as e:
            rep["mismatches"].append("%s：核对材质槽抛错 %s: %s" % (ob.name, type(e).__name__, str(e)[:120]))
    for mat in set(mats.values()):
        nm = str(mat.name)
        try:
            bpy.data.materials.remove(mat, do_unlink=True)
        except Exception as e:
            rep["mismatches"].append("临时材质 %s 删除失败 %s: %s" % (nm, type(e).__name__, str(e)[:120]))
        if bpy.data.materials.get(nm) is not None:
            rep["temp_materials_left"].append(nm)
        else:
            rep["temp_materials_removed"] += 1
    rep["ok"] = (not rep["mismatches"]) and (not rep["temp_materials_left"])
    return rep


def _parts_legend_rows(groups, assign):
    """图例行：`名字<TAB>R,G,B<TAB>面数`（面数 = 该部件所有对象的 polygon 之和）。"""
    rows = []
    for name, objs in groups:
        asg = assign.get(str(name)) or {}
        rgb = asg.get("rgb") or [0, 0, 0]
        faces = 0
        for ob in objs:
            try:
                faces += len(ob.data.polygons)
            except Exception:
                pass
        rows.append({"name": str(name).replace("\t", " ").replace("\n", " "),
                     "rgb": [int(rgb[0]), int(rgb[1]), int(rgb[2])], "faces": int(faces),
                     "objects": [str(o.name) for o in objs], "color_index": asg.get("index"),
                     "color_source": asg.get("source")})
    return rows


def _write_parts_legend(txt_path, rows, meta):
    """写图例：.txt = 每行 `名字<TAB>R,G,B<TAB>面数`（**纯数据行、无表头** → `wc -l` 即部件数）；
    .json = 同名的全量细节（调色板、sRGB→线性换算、分组口径、还原自检…），人照样读得到。"""
    with open(txt_path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write("%s\t%d,%d,%d\t%d" % (r["name"], r["rgb"][0], r["rgb"][1], r["rgb"][2], r["faces"]) + chr(10))
    jpath = os.path.splitext(txt_path)[0] + ".json"
    with open(jpath, "w", encoding="utf-8") as f:
        f.write(_j(dict(meta, legend_txt=txt_path, legend_json=jpath, rows=rows)))
    return jpath


def _legend_line_count(path):
    """图例数据行数（空行不计）—— 自检用它断言"行数 == 部件数"。"""
    n = 0
    with open(path, encoding="utf-8") as f:
        for ln in f:
            if ln.strip():
                n += 1
    return n


# ---------------------------------------------------------------- 主入口

def qc_render_views(args=None):
    t0 = time.perf_counter()
    a = _unpack_args(args)          # JSON 字符串 / {"args": {...}} 包一层 / None —— 口径与 dispatch 一致

    # ---- v0.8.10（B1）：参数作用域强校验 —— "只认顶层的键"若出现在子字典里，必须报出来（本项目 234 张假证据的根因）
    TOP_KEYS = set(["file", "views", "res", "samples", "budget_s", "thr", "outdir", "jsonl", "ref_path", "ref_box",
                    "ref_search", "lights", "lights_mode", "margin", "engine", "view_transform", "tag", "targets",
                    "allow_open_file", "film_transparent", "lens", "sensor_width", "warmup", "ladder",
                    "per_view_budget_s", "aabb", "focus", "sheet", "sheet_cols", "sheet_tile", "chunk",
                    "sheet_grid", "sheet_scale_m", "args",
                    # v0.8.11（Procedura 融合：parts_color / 视角目录 / 设备）
                    "mode", "parts_color", "parts_group_by", "parts_flat", "parts_palette", "parts_legend",
                    "catalog_ortho", "view_order", "strict_views"])
    VIEW_KEYS = set(["name", "az", "el", "from", "loc", "location", "look_at", "target", "to", "lens", "sensor",
                     "sensor_fit", "ortho", "ortho_scale", "res", "margin", "path", "ref_path", "ref_box",
                     "aabb", "focus", "samples"])
    scope_warnings = []
    for k in sorted(a.keys()):
        if k not in TOP_KEYS:
            scope_warnings.append("未知顶层参数 %s（已忽略；可用参数见 qc_render_help）" % k)
    _views_raw = a.get("views")
    if isinstance(_views_raw, list):
        for _i, _v in enumerate(_views_raw):
            if not isinstance(_v, dict):
                continue
            for _k in _v.keys():
                if _k in TOP_KEYS and _k not in VIEW_KEYS:
                    scope_warnings.append("views[%d] 里的 %s **只在顶层生效，已被忽略**；实际生效值 = %r（把它挪到 args 顶层）"
                                          % (_i, _k, a.get(_k)))
                elif _k not in VIEW_KEYS:
                    scope_warnings.append("views[%d] 未知键 %s（已忽略）" % (_i, _k))

    # ---- 输出目录与 jsonl
    outdir = _win(a.get("outdir") or _out_dir())
    try:
        os.makedirs(outdir, exist_ok=True)
    except Exception as e:
        return _j({"ok": False, "error": "outdir 建不出来: %s (%s)" % (outdir, e)})
    tag = _safe(a.get("tag") or "views")
    jsonl = a.get("jsonl") or os.path.join(outdir, "render_views.jsonl")
    jsonl = _win(jsonl)

    # ---- file：与当前不同才开（GUI 默认拒绝，保护用户场景）
    want_file = a.get("file")
    opened = False
    if want_file:
        target = _win(want_file)
        cur = bpy.data.filepath or ""
        same = False
        try:
            same = bool(cur) and os.path.normcase(os.path.abspath(cur)) == os.path.normcase(os.path.abspath(target))
        except Exception:
            same = (cur == target)
        if not same:
            if bool(getattr(bpy.app, "background", False)) or a.get("allow_open_file"):
                bpy.ops.wm.open_mainfile(filepath=target)
                opened = True
            else:
                return _j({"ok": False, "error": "GUI 进程里拒绝打开别的 .blend（会顶掉你当前的场景）",
                           "file": target, "current": cur,
                           "hint": "长活请走 blender_rt_headless(file=..., preload=\"qc,qc_render\", as_job=true)；"
                                   "确实要在 GUI 里换文件就传 allow_open_file=true"})

    # ---- 引擎 / 采样
    # v0.8.11 顺序修正：原来 `_set_engine`/`_apply_samples` 在"取景失败/没有可见 mesh"的早退之前，
    # 于是早退路径会把引擎改掉却不还原（pre-existing 现场泄漏）。取景与视角归一都不依赖引擎，
    # 所以把它们挪到所有早退之后 —— 一旦动过状态，就一定在下面的 try/finally 里还回去。
    sc = bpy.context.scene
    eng_mode = str(a.get("engine") or "keep")
    samples = int(a.get("samples") or 64)

    # ---- 目标与 AABB（+ v0.8.10 B1：把"实际生效的筛选集"回显出来）
    _t_req = a.get("targets")
    objs, skipped = _collect_objs(_t_req)
    try:
        bpy.context.view_layer.update()
    except Exception:
        pass
    mn, mx, nobj = _aabb(objs)
    if nobj == 0 or mn is None:
        return _j({"ok": False, "error": "取景失败：没有任何可见 mesh（可传 targets=[名字] 指定）",
                   "file": bpy.data.filepath, "skipped": skipped})
    targets_resolved = {"requested": ([str(x) for x in _t_req] if isinstance(_t_req, (list, tuple)) else None),
                        "resolved": [ob.name for ob in objs], "count": len(objs),
                        "missing": list(skipped.get("missing") or []), "skipped_hidden": int(skipped.get("hidden") or 0)}
    if targets_resolved["missing"]:
        scope_warnings.append("targets 里这些名字不存在（已忽略）：%s" % ", ".join(targets_resolved["missing"]))
    if targets_resolved["requested"] and len(objs) < len(targets_resolved["requested"]):
        scope_warnings.append("targets 请求 %d 个、实际参与取景 %d 个（差集见 targets_resolved.missing）"
                              % (len(targets_resolved["requested"]), len(objs)))
    center = (mn + mx) * 0.5
    size = mx - mn
    corners = _corners(mn, mx)

    # ---- 视角归一化（v0.8.11：走目录 + 别名；未知名原样回传，绝不静默替换）
    views = a.get("views")
    views_from = "default_legacy"
    if not views:                                   # None / "" / [] 都按老口径取默认三点（行为不变）
        views = list(DEFAULT_VIEWS_LEGACY)
    else:
        views_from = "request"
    if isinstance(views, str):
        views = [v.strip() for v in views.split(",") if v.strip()]
    base_res = a.get("res") or DEFAULT_RES
    if isinstance(base_res, (int, float)):
        base_res = [int(base_res), int(base_res)]
    base_res = [int(base_res[0]), int(base_res[1])]
    view_order = str(a.get("view_order") or "catalog").lower()
    strict_views = bool(a.get("strict_views"))
    catalog_ortho = bool(a.get("catalog_ortho"))
    named_req = [v for v in views if isinstance(v, str) and not _is_azel_spec(v)]
    rv = resolve_view_names(named_req)
    canon_order, unknown_views, alias_applied, deduped_views = rv["names"], rv["unknown"], rv["aliases"], rv["deduped"]
    explicit_specs, spec_err = [], None
    # 显式视角（dict / az=el 字符串）保持请求顺序，且**不受**目录排序与去重影响（老参数原样可用的关键）
    for i, v in enumerate(views):
        if isinstance(v, dict):
            explicit_specs.append(dict(v))
        elif isinstance(v, str) and _is_azel_spec(v):
            s = {"name": v, "_source": "azel"}
            try:
                kv = {}
                for part in v.strip().lower().replace(" ", "").split(","):
                    if "=" in part:
                        kk, vv = part.split("=", 1)
                        kv[kk] = vv
                if "az" in kv:
                    s["az"] = float(kv["az"])
                if "el" in kv:
                    s["el"] = float(kv["el"])
            except Exception as e:
                spec_err = "views[%d] 的 az/el 写法解析失败：%r（%s）" % (i, v, str(e)[:100])
                break
            s.setdefault("az", NAMED_VIEWS["iso"][0])
            s.setdefault("el", NAMED_VIEWS["iso"][1])
            explicit_specs.append(s)
        elif not isinstance(v, str):
            return _j({"ok": False, "error": "views[%d] 类型不支持：%r" % (i, v)})
    if spec_err:
        return _j({"ok": False, "error": spec_err, "views": [str(x) for x in views]})
    fallback_used = False
    if not canon_order and not explicit_specs:
        # 一个有效的都没有（或给的全是未知名）→ 兜底 4 视角（Procedura DEFAULT_VIEWS 口径）
        canon_order = list(DEFAULT_VIEWS_FALLBACK)
        fallback_used = True
        scope_warnings.append("views 里没有一个认得出来的视角名 → 回落到默认 4 视角 %s；你给的 %s 原样保留在 "
                              "unknown_views（不是静默替换）"
                              % (",".join(DEFAULT_VIEWS_FALLBACK), unknown_views or [str(x) for x in views]))
    if unknown_views:
        scope_warnings.append("未知视角名 %s 已跳过（原样返回在 unknown_views；具名表见 qc_render_help.view_menu）"
                              % ", ".join(unknown_views))
    if deduped_views:
        scope_warnings.append("重复视角 %s 已去重（同一规范名只渲一次；要渲两次请用 dict 视角显式指定 path）"
                              % ", ".join(deduped_views))
    if strict_views and unknown_views:
        return _j({"ok": False, "error": "views 里有认不出来的名字，strict_views=true 时不许带病交付",
                   "unknown_views": unknown_views, "known_views": canon_order,
                   "view_menu": view_menu_text()})
    named_specs = {}
    req_spelling = {}
    for raw in named_req:                       # 别名/大小写容错时记下用户原始拼写（结果里可追溯）
        c = _canon_view(raw)
        if c and _norm_view_name(raw) != _norm_view_name(c):
            req_spelling.setdefault(c, str(raw))
    for nm in canon_order:
        s = {"name": nm, "az": NAMED_VIEWS[nm][0], "el": NAMED_VIEWS[nm][1], "_source": "catalog"}
        if nm in req_spelling:
            s["_requested"] = req_spelling[nm]
        if catalog_ortho:
            s["ortho"] = VIEW_ORTHO[nm]
            s["_ortho_src"] = "catalog"
        named_specs[nm] = s
    if view_order == "request":
        # 老口径逃生门：完全按请求顺序（字符串按首次出现位置插回，dict/az=el 保持原位）
        vlist, _emitted = [], set()
        for v in views:
            if isinstance(v, dict) or (isinstance(v, str) and _is_azel_spec(v)):
                vlist.append(explicit_specs.pop(0))
            else:
                nm = _canon_view(v)
                if nm and nm in named_specs and nm not in _emitted:
                    _emitted.add(nm)
                    vlist.append(named_specs[nm])
    else:
        vlist = [named_specs[n] for n in canon_order] + explicit_specs
    for i, s in enumerate(vlist):
        if str(s.get("_source")) == "azel":
            s["_requested"] = str(s.get("name"))     # az=el 原样留痕（名字就是原串）
        s.setdefault("name", "v%d" % (i + 1))
        s.setdefault("res", base_res)

    # ---- v0.8.11（A）：逐部件配色（parts_color）—— 覆盖材质槽之前先把"要覆盖谁、覆盖成什么"定下来
    mode = str(a.get("mode") or ("parts_color" if a.get("parts_color") else "views")).lower()
    if mode in ("parts", "parts_color", "parts_color_views", "parts-color"):
        mode = "parts_color"
    parts_enabled = (mode == "parts_color")
    parts_group_by = str(a.get("parts_group_by") or "object").lower()
    if parts_group_by not in ("object", "collection"):
        scope_warnings.append("parts_group_by=%r 不认（只有 object/collection）→ 按 object 走" % parts_group_by)
        parts_group_by = "object"
    parts_flat = bool(a.get("parts_flat"))
    parts_palette = PARTS_PALETTE
    if a.get("parts_palette"):
        try:
            pp = [tuple(int(x) for x in c[:3]) for c in a["parts_palette"]]
            pp = [c for c in pp if len(c) == 3]
            if pp:
                parts_palette = pp
            else:
                scope_warnings.append("parts_palette 里没有合法的 [R,G,B] → 用默认 12 色")
        except Exception as e:
            scope_warnings.append("parts_palette 解析失败（%s）→ 用默认 12 色" % str(e)[:100])
    parts_groups, parts_assign, parts_rows, parts_info = [], {}, [], None
    if parts_enabled:
        pc_objs, pc_skipped = _collect_objs(None)        # 参与渲染的**全部可见 mesh**（与 targets 取景无关）
        if not pc_objs:
            return _j({"ok": False, "error": "parts_color：场景里没有可见 mesh，没东西可配色",
                       "skipped": pc_skipped})
        parts_groups = _parts_groups(pc_objs, parts_group_by)
        parts_assign = _parts_assign(parts_groups, parts_palette)
        parts_rows = _parts_legend_rows(parts_groups, parts_assign)
        _reused = [r["name"] for r in parts_rows if r.get("color_source") == "hash"]
        if len(parts_rows) > len(parts_palette):
            scope_warnings.append("部件数 %d > 调色板 %d 色：%d 个部件按名字哈希复用颜色"
                                  "（图例里 color_source=hash；同名同色跨次稳定，不报错）"
                                  % (len(parts_rows), len(parts_palette), len(_reused)))
        if a.get("ref_path"):
            scope_warnings.append("parts_color 模式下 ref 比对意义有限（材质被覆盖成色块，IoU/剖面差不再是几何判据）")
        parts_info = {"enabled": True, "mode": mode, "group_by": parts_group_by, "flat": parts_flat,
                      "parts": len(parts_rows), "palette_size": len(parts_palette),
                      "palette": [[int(c[0]), int(c[1]), int(c[2])] for c in parts_palette],
                      "palette_space": "sRGB 0-255（图例色）；渲染时按 srgb→linear 写入材质，"
                                       "Principled 还叠灯光/色彩变换，故图上颜色不会与图例逐像素相等"
                                       "（要逐像素对上用 parts_flat=true + view_transform=\"Standard\"）",
                      "assignments": dict((k, v) for k, v in parts_assign.items()),
                      "color_reused_by_hash": _reused,
                      "scope": "全部可见 mesh（%d 个对象 / %d 个部件）；与 targets 取景相互独立"
                               % (len(pc_objs), len(parts_rows)),
                      "skipped": pc_skipped,
                      "touched": "只改对象材质槽（+ 链接副本临时复制 mesh）；可见性/世界/灯光/相机不动"}
    else:
        parts_info = {"enabled": False, "mode": mode,
                      "hint": "要逐部件配色渲图：传 mode=\"parts_color\"（或 parts_color=true）"}
    if a.get("parts_legend"):
        legend_txt = _win(a["parts_legend"])
    else:
        legend_txt = _win(os.path.join(outdir, "parts_color_meta.txt"))
    eng_before, eng_after = _set_engine(eng_mode)
    samp_before = _apply_samples(samples)
    # v0.8.11（B）：设备**必须在引擎切完之后**测 —— 批前测会把"上一批/上一次的引擎"记进这一批
    # （踩过：engine="cycles" 的批次里 device 报的是 EEVEE 的 GPU 语义，等于把判据挂错了对象）
    dev_batch = _device_info()

    # ---- v0.8.10（C7 分片）：chunk=[i,n] → 只渲第 i 份（1-based），大活可切多份并行/续跑
    _chunk = a.get("chunk")
    if isinstance(_chunk, (list, tuple)) and len(_chunk) == 2:
        try:
            _ci, _cn = int(_chunk[0]), max(1, int(_chunk[1]))
            _sel = [v for _idx, v in enumerate(vlist) if (_idx % _cn) == (_ci - 1)]
            if _sel:
                vlist = _sel
                scope_warnings.append("分片模式：chunk=[%d,%d] → 本次只渲 %d/%d 张" % (_ci, _cn, len(vlist), _cn))
        except Exception as e:
            scope_warnings.append("chunk 参数不认（应为 [i,n]）：%s" % str(e)[:80])

    budget_s = a.get("budget_s", a.get("thr"))
    try:
        budget_ms = int(round(float(budget_s) * 1000.0)) if budget_s not in (None, "", 0, "0") else 0
        # v0.8.11：正数但 < 0.5ms 的预算会被 round 成 0，而 0 在下游等于「不设预算」—— 静默忽略预算
        # 与"超预算立刻停"的约定正好相反；这里抬到 1ms 并说清楚（None/""/0/"0" 仍照旧 = 不设预算）。
        if budget_ms == 0 and budget_s not in (None, "", 0, "0"):
            try:
                if float(budget_s) > 0:
                    budget_ms = 1
                    scope_warnings.append("budget_s=%r 太小（取整后 <1ms），按 1ms 处理（不退化成「不设预算」）"
                                          % (budget_s,))
            except Exception:
                pass
    except Exception:
        budget_ms = 0

    lights_cfg = a.get("lights", DEFAULT_LIGHTS)
    if lights_cfg is False or lights_cfg is None:
        lights_cfg = {}
    elif lights_cfg is True:
        lights_cfg = dict(DEFAULT_LIGHTS)
    elif isinstance(lights_cfg, dict):
        lights_cfg = dict(DEFAULT_LIGHTS, **{k: v for k, v in lights_cfg.items() if k in ("key", "fill", "rim")})
    else:
        lights_cfg = dict(DEFAULT_LIGHTS)
    lights_mode = str(a.get("lights_mode") or "add").lower()
    margin = float(a.get("margin", 1.12))
    lens_def = float(a.get("lens", 50.0))
    sensor_def = float(a.get("sensor_width", 36.0))
    view_transform = a.get("view_transform")
    # v0.8.11（A）：parts_color + flat（Emission）时默认 Standard 色彩变换 ——
    # 默认的 AgX/Filmic 会把纯色洗淡，图例与图上颜色就对不上了；用户显式传了 view_transform 就听用户的。
    if parts_enabled and parts_flat and not view_transform:
        view_transform = "Standard"
        parts_info["view_transform_defaulted"] = "Standard"
        scope_warnings.append("parts_color+parts_flat：未指定 view_transform，已默认 Standard"
                              "（AgX/Filmic 会把图例色洗淡；出图后还原原设置）")

    # ---- 现场保存
    css = getattr(sc, "view_settings", None)
    vt_before = getattr(css, "view_transform", None) if css is not None else None
    rs = sc.render
    saved = {
        "camera": sc.camera, "engine": eng_before,
        "res_x": int(rs.resolution_x), "res_y": int(rs.resolution_y), "res_pct": int(rs.resolution_percentage),
        "filepath": str(rs.filepath), "use_ext": bool(rs.use_file_extension),
        "fmt": str(rs.image_settings.file_format), "color_mode": str(rs.image_settings.color_mode),
        "film_transparent": bool(rs.film_transparent),
    }
    db_before = _db_counts()          # v0.8.10（D1）：临时资源泄漏自检的基线
    db_after = None
    hidden_lights = []
    made_cam = None
    rig = []
    entries = []
    skipped_views = []
    stopped_early = False
    spent_ms = 0
    err = None
    # v0.8.10：即使中途异常，结果字典也必须有这些字段（否则 except 之后会 NameError）
    warmup_info = None
    ladder = None
    ladder_idx = 0
    per_view_budget_ms = 0
    engine_check = None
    sheet_path = None
    sheet_metrics = None
    # v0.8.11（A/B）：parts_color 现场状态 + 设备复核 + 图例产物
    pc_state, pc_mats, pc_restore = [], {}, None
    pc_legend_json = None
    pc_err = None
    dev_after = None
    device_check = None
    device_used = dev_batch      # 顶部汇总：渲染完成后换成"渲完之后"的实测回读
    try:
        # ---- v0.8.11（A）：逐部件配色 —— 覆盖材质槽（放在三点光之前：万一覆盖就失败，灯还没建）
        if parts_enabled:
            pc_state, pc_mats, pc_err = _parts_override(pc_objs, parts_assign,
                                                        lambda ob: (sorted([str(c.name) for c in ob.users_collection])
                                                                    or ["(无集合)"])[0]
                                                        if parts_group_by == "collection" else str(ob.name),
                                                        parts_flat)
        if pc_err:
            raise RuntimeError(pc_err)
        # 三点光（只在本进程内临时建）
        if lights_cfg and lights_mode != "off":
            if lights_mode == "only":
                for ob in list(sc.objects):
                    if ob.type == "LIGHT" and not ob.hide_render:
                        ob.hide_render = True
                        hidden_lights.append(ob)
            rig = _add_rig(0.0, lights_cfg)
        # 临时相机
        cam_data = bpy.data.cameras.new("DSH_QC_CAM")
        made_cam = bpy.data.objects.new("DSH_QC_CAM", cam_data)
        sc.collection.objects.link(made_cam)
        made_cam.rotation_mode = "QUATERNION"
        cam_data.clip_start = 0.001
        cam_data.clip_end = 100000.0
        cam_data.sensor_width = sensor_def
        cam_data.sensor_fit = "AUTO"

        # 渲染设置（出图后全部还原）
        rs.image_settings.file_format = "PNG"
        rs.image_settings.color_mode = "RGBA"
        rs.film_transparent = bool(a.get("film_transparent", True))
        rs.resolution_percentage = 100
        rs.use_file_extension = False
        sc.camera = made_cam
        if view_transform and css is not None:
            try:
                css.view_transform = str(view_transform)
            except Exception:
                pass

        lines = []
        # ---- v0.8.10（C2）：预热帧（不计时/不进预算/不进 jsonl）+ 降质阶梯状态
        do_warmup = bool(a.get("warmup", True))
        warmup_info = None
        ladder = a.get("ladder")
        if isinstance(ladder, dict):
            ladder = [ladder]
        elif isinstance(ladder, str):
            try:
                ladder = json.loads(ladder)
            except Exception:
                ladder = None
        if not isinstance(ladder, list):
            ladder = None
        ladder_idx = 0
        try:
            per_view_budget_ms = int(round(float(a.get("per_view_budget_s")) * 1000)) if a.get("per_view_budget_s") \
                else int(a.get("per_view_budget_ms") or 0)
        except Exception:
            per_view_budget_ms = 0
        for idx, v in enumerate(vlist):
            if budget_ms and spent_ms >= budget_ms:
                stopped_early = True
                skipped_views = [str(x.get("name")) for x in vlist[idx:]]
                break
            rv = v.get("res") or base_res
            if isinstance(rv, (int, float)):
                rv = [int(rv), int(rv)]
            w, h = max(16, int(rv[0])), max(16, int(rv[1]))
            lens = float(v.get("lens", lens_def))
            ortho = bool(v.get("ortho", False))
            vmargin = float(v.get("margin", margin))
            nm = _safe(v.get("name"))
            path = _win(v.get("path") or os.path.join(outdir, "%s_%s.png" % (tag, nm)))
            corners_used = corners
            frm = v.get("from") or v.get("loc")
            look = v.get("look_at") or v.get("target")
            if frm and look:
                loc = Vector([float(x) for x in frm[:3]])
                d = (Vector([float(x) for x in look[:3]]) - loc)
                d = d.normalized() if d.length > 1e-9 else Vector((0.0, -1.0, 0.0))
                az = math.degrees(math.atan2(-d[1], -d[0]))
                el = math.degrees(math.asin(max(-1.0, min(1.0, -d[2]))))
                stats = {"explicit": True}
                oscale = float(v.get("ortho_scale")) if (ortho and v.get("ortho_scale")) else (float(v.get("ortho_scale")) if v.get("ortho_scale") else None)
            else:
                az = float(v.get("az", NAMED_VIEWS["iso"][0]))
                el = float(v.get("el", NAMED_VIEWS["iso"][1]))
                # v0.8.10（C5）：世界坐标框选 —— aabb=[...] / focus="x,y,z,r"（按视角优先，其次顶层）
                _frame = _focus_box(v.get("aabb")) or _focus_box(v.get("focus")) or _focus_box(a.get("aabb")) or _focus_box(a.get("focus"))
                if _frame:
                    corners_used = _corners(_frame[0], _frame[1])
                    _fc = (_frame[0] + _frame[1]) * 0.5
                    loc, d, oscale, stats = _solve_view(_fc, corners_used, az, el, w, h, lens, sensor_def, vmargin,
                                                        ortho=ortho, ortho_scale=v.get("ortho_scale"))
                    stats["framed_by"] = "aabb/focus"
                else:
                    loc, d, oscale, stats = _solve_view(center, corners, az, el, w, h, lens, sensor_def, vmargin,
                                                        ortho=ortho, ortho_scale=v.get("ortho_scale"))
            made_cam.location = loc
            made_cam.rotation_quaternion = d.to_track_quat("-Z", "Y")
            cam_data.lens = lens
            cam_data.type = "ORTHO" if ortho else "PERSP"
            if ortho:
                cam_data.ortho_scale = float(oscale or a.get("ortho_scale") or 10.0)
            # 相机与灯光角度：贴住视锥 + 别插进近裁剪面
            radius = max((mx - mn).length * 0.5, 1e-3)
            cam_data.clip_start = max(0.001, radius * 0.01)
            cam_data.clip_end = max(radius * 100.0, cam_data.clip_start * 1000.0)
            if rig:
                _aim_rig(rig, az)
            rs.resolution_x = w
            rs.resolution_y = h
            rs.filepath = path
            try:
                bpy.context.view_layer.update()
            except Exception:
                pass
            # ---- v0.8.10（C2）预热帧：首帧 EEVEE 着色器编译不计时、不进预算、不进 jsonl
            if do_warmup and idx == 0 and warmup_info is None:
                try:
                    rs.filepath = _win(os.path.join(outdir, "_warmup.png"))
                    tw = time.perf_counter()
                    bpy.ops.render.render(write_still=True)
                    warmup_info = {"ms": int(round((time.perf_counter() - tw) * 1000.0)),
                                   "path": _win(os.path.join(outdir, "_warmup.png")), "counted": False}
                except Exception as e:
                    warmup_info = {"error": str(e)[:120], "counted": False}
                rs.filepath = path
            # ---- 渲染（降质阶梯：超 per_view_budget_s 且还有更粗档 → 降一档重渲，逐级留痕）
            step_i = ladder_idx
            ms_hist = []
            while True:
                w_eff, h_eff, step_used = _apply_step(rs, ladder, step_i, w, h)
                t1 = time.perf_counter()
                bpy.ops.render.render(write_still=True)
                ms = int(round((time.perf_counter() - t1) * 1000.0))
                ms_hist.append(ms)
                if per_view_budget_ms and ms > per_view_budget_ms and ladder and step_i + 1 < len(ladder):
                    step_i += 1
                    continue
                break
            if ladder and step_i != ladder_idx:
                ladder_idx = step_i      # 后续视角沿用更粗的档（逐级留痕见 ladder_trace）
            ladder_used = (ladder[step_i] if ladder else None)
            spent_ms += ms
            # 投影对拍按**实际**渲染分辨率（阶梯降质后分辨率会变）
            pred, pm_src, pm_err = _project_px(made_cam, corners_used, w_eff, h_eff)
            got = os.path.isfile(path)
            nbytes = int(os.path.getsize(path)) if got else 0
            if not got:
                raise RuntimeError("渲染没有产出文件：%s" % path)
            _dev_live = _device_live(dev_batch)      # 渲完即时回读（EEVEE 能在这一步补上真实 renderer 串）
            entry = {"view": str(v.get("name")), "ms": ms, "bytes": nbytes, "hash": _md5(path),
                     "path": path, "res": [w_eff, h_eff], "engine": _engine_label(), "samples": samples,
                     "warmup": bool(do_warmup and idx == 0), "step": (step_i if ladder else 0),
                     "step_cfg": ladder_used, "ms_history": ms_hist,
                     # v0.8.11（B）：这一张"实际用的渲染设备"（渲完即时回读；拿不到就 null + device_source）
                     "device": _device_slim(_dev_live), "device_source": _dev_live.get("source"),
                     "camera": {"az": round(az, 3), "el": round(el, 3), "lens": lens, "ortho": ortho,
                                "loc": [round(float(x), 4) for x in loc],
                                "look_at": [round(float(x), 4) for x in (loc + d)],
                                "ortho_scale": (round(float(cam_data.ortho_scale), 4) if ortho else None)},
                     "frame": {"pred_bbox_px": pred, "proj_source": pm_src, "proj_err_px": pm_err,
                               "solve": {k: (round(float(vv), 4) if isinstance(vv, float) else vv) for k, vv in stats.items()}},
                     "ts": time.strftime("%Y-%m-%dT%H:%M:%S")}
            # v0.8.11：视角溯源（别名归一/az=el 原样留痕）与 parts_color 图例路径
            if v.get("_requested") and str(v.get("_requested")) != str(v.get("name")):
                entry["view_requested"] = str(v.get("_requested"))
            if v.get("_source"):
                entry["view_source"] = str(v.get("_source"))
            if catalog_ortho and v.get("_ortho_src"):
                entry["ortho_source"] = str(v.get("_ortho_src"))
            if parts_enabled:
                entry["mode"] = "parts_color"
                entry["legend_path"] = legend_txt
                entry["parts"] = len(parts_rows)
                entry["parts_flat"] = bool(parts_flat)
            try:
                entry["frame"].update(_alpha_stats(path))
            except Exception as e:
                entry["frame"]["alpha_err"] = str(e)[:120]
            # ---- 可选：参考比对（走 qc.py 的 compare）
            ref_path = v.get("ref_path") or a.get("ref_path")
            if ref_path:
                K = _kernel()
                qapi = getattr(K, "dsh_qc_api", None) if K is not None else None
                if qapi is None:
                    entry["ref"] = {"ok": False, "error": "需要先注入 qc.py（headless preload 里带上 qc，或引擎自动注入）"}
                else:
                    rb = v.get("ref_box", None)
                    if rb is None:
                        rball = a.get("ref_box")
                        rb = rball.get(str(v.get("name")), None) if isinstance(rball, dict) else rball
                    try:
                        rr = json.loads(qapi["compare"](_win(ref_path), rb, path, str(v.get("name")),
                                                        None, None, bool(a.get("ref_search", False))))
                        met = rr.get("metrics", {}) or {}
                        entry["ref"] = {"ok": True, "iou": rr.get("iou"), "iou_fixed": met.get("iou_fixed"),
                                        "iou_search": met.get("iou_search"), "dice": met.get("dice"),
                                        "missing_px": met.get("missing_px"), "extra_px": met.get("extra_px"),
                                        "boundary_mean_px": (met.get("boundary") or {}).get("mean_px"),
                                        "profile_mean_diff_px": ((rr.get("profile") or {}).get("diff") or {}).get("mean_diff_px"),
                                        "sheet": rr.get("sheet"), "overlay": rr.get("overlay"),
                                        "ms": rr.get("ms"), "search": bool(a.get("ref_search", False))}
                    except Exception as e:
                        entry["ref"] = {"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:160])}
            entries.append(entry)
            lines.append(entry)
            # 逐张追加（第一张截断重写）：预算中途停下也留下已完成的行
            with open(jsonl, ("w" if len(lines) == 1 else "a"), encoding="utf-8") as f:
                f.write(_j(entry) + chr(10))
            if budget_ms and spent_ms > budget_ms:
                stopped_early = True
                skipped_views = [str(x.get("name")) for x in vlist[idx + 1:]]
                break

        # ---- v0.8.11（B）：批内设备复核 + 引擎回读校验前的实测回读
        dev_after = _device_info()
        _d0 = _device_slim(dev_batch)
        _d1 = _device_slim(dev_after)
        _d_used = dict(dev_after or dev_batch or {})
        _d_used["measured"] = "after_render（批次结束时的实测回读）"
        device_used = _d_used
        device_check = {"same_device": (_d0.get("device") == _d1.get("device") and _d0.get("engine") == _d1.get("engine")),
                        "identical_reading": (_d0 == _d1),
                        "before_render": _d0, "after_render": _d1,
                        "note": "EEVEE 的 renderer 串在后台进程里**首次渲染后**才拿得到（gpu.platform 需 GL 上下文），"
                                "所以 before/after 可能只差 gpu 字段 —— 只要 device/engine 相同就算同一设备"}
        if not device_check["same_device"]:
            scope_warnings.append("渲染前后设备回读不一致（before=%r / after=%r）—— 设备可能在批次中途被改动，"
                                  "本批的 ms 不可比" % (_d0.get("device"), _d1.get("device")))
        elif _d1.get("device") is None:
            scope_warnings.append("设备未知（device=null）：%s" % (_d1.get("source") or "拿不到判据"))

        # ---- v0.8.11（A）：图例落盘（渲染成功才写 —— 没有图却先有图例，是在造假证据）
        if parts_enabled and entries:
            try:
                pc_legend_json = _write_parts_legend(
                    legend_txt, parts_rows,
                    {"version": QC_RENDER_VERSION, "kind": "parts_color_legend",
                     "scene_file": bpy.data.filepath, "outdir": outdir, "tag": tag,
                     "group_by": parts_group_by, "flat": bool(parts_flat),
                     "view_transform": (str(view_transform) if view_transform else None),
                     "palette_size": len(parts_palette),
                     "palette_srgb": [[int(c[0]), int(c[1]), int(c[2])] for c in parts_palette],
                     "color_space_note": "R,G,B 是 sRGB 0-255（图例色）；材质里写的是同色的线性值"
                                         "（srgb_to_linear），Principled 还叠灯光与色彩变换",
                     "color_reused_by_hash": [r["name"] for r in parts_rows if r.get("color_source") == "hash"],
                     "views": [str(e.get("view")) for e in entries],
                     "ts": time.strftime("%Y-%m-%dT%H:%M:%S")})
                parts_info["legend_path"] = legend_txt
                parts_info["legend_json"] = pc_legend_json
                parts_info["legend_lines"] = _legend_line_count(legend_txt)
                if parts_info["legend_lines"] != len(parts_rows):
                    scope_warnings.append("图例行数 %d != 部件数 %d（图例文件被外部改过？）"
                                          % (parts_info["legend_lines"], len(parts_rows)))
            except Exception as e:
                if err is None:
                    err = {"error": "写图例失败：%s: %s" % (type(e).__name__, str(e)[:200])}

        # ---- v0.8.10（C2）引擎回读校验：判据必须自带"当时是什么引擎"
        _eng_now = _engine_label()
        engine_check = {"requested": eng_mode, "configured_at_start": eng_after, "actual": _eng_now,
                        "rt": bool("+RT" in _eng_now), "samples": samples, "ok": True, "warnings": []}
        if str(eng_mode).lower() in ("eevee", "eevee_rt", "e") and "+RT" not in _eng_now:
            engine_check["ok"] = False
            engine_check["warnings"].append("请求 EEVEE+光追但回读没有 RT —— 玻璃/折射类材质不会正确")
        elif "+RT" not in _eng_now:
            engine_check["warnings"].append("本次引擎未开光追（%s）：与 EEVEE+RT 的历史分数不可直接比较" % _eng_now)

        # ---- v0.8.10（C5）N 宫格接触表（拼图 + 每格指标）
        sheet_path, sheet_metrics = None, None
        if int(a.get("sheet") or 0) > 1 and entries:
            try:
                mapi = _montage_api()
                if mapi is None:
                    scope_warnings.append("sheet 需要 montage 模块（runtime/montage.py）—— 未找到，已跳过拼图")
                else:
                    _sp = _win(os.path.join(outdir, "%s_sheet.png" % tag))
                    _r = mapi["montage"]([e["path"] for e in entries], int(a.get("sheet_cols") or 0) or None,
                                         int(a.get("sheet_tile") or 420), _sp, [e["view"] for e in entries],
                                         a.get("sheet_grid"), a.get("sheet_scale_m"))
                    if isinstance(_r, dict):
                        sheet_path = _r.get("path") or _sp
                        sheet_metrics = _r.get("tiles")
                    else:
                        sheet_path = _r or _sp
            except Exception as e:
                scope_warnings.append("拼图失败：%s" % str(e)[:140])
    except BaseException as e:
        import traceback
        err = {"error": "%s: %s" % (type(e).__name__, str(e)[:300]), "traceback": traceback.format_exc()[-2000:]}
    finally:
        # ---- v0.8.11（A）：parts_color 现场还原**排在最前** —— 材质槽没还回去就去删灯/相机，
        # 中途一步抛错就再也回不去了（渲染设置还原在下面，顺序同理：先还"改了内容的"，再还"设置"）。
        if pc_state:
            try:
                pc_restore = _parts_restore(pc_state, pc_mats)
            except Exception as e:
                pc_restore = {"ok": False, "mismatches": ["parts_color 还原抛错 %s: %s"
                                                          % (type(e).__name__, str(e)[:200])],
                              "temp_materials_left": [], "objects": len(pc_state)}
        # ---- 还原现场（临时对象删掉、设置回滚）
        try:
            for ob in rig:
                try:
                    _ld = ob.data if ob.type == "LIGHT" else None
                    bpy.data.objects.remove(ob, do_unlink=True)
                    if _ld is not None:
                        # v0.8.10（D1）：灯的 datablock 必须一起删 —— 旧版只删 object，一轮下来会攒出几十个孤儿灯
                        try:
                            bpy.data.lights.remove(_ld, do_unlink=True)
                        except Exception:
                            pass
                except Exception:
                    pass
            if made_cam is not None:
                try:
                    cd = made_cam.data
                    bpy.data.objects.remove(made_cam, do_unlink=True)
                    bpy.data.cameras.remove(cd, do_unlink=True)
                except Exception:
                    pass
            for ob in hidden_lights:
                try:
                    ob.hide_render = False
                except Exception:
                    pass
            sc.camera = saved["camera"]
            rs = sc.render
            rs.resolution_x = saved["res_x"]
            rs.resolution_y = saved["res_y"]
            rs.resolution_percentage = saved["res_pct"]
            rs.filepath = saved["filepath"]
            rs.use_file_extension = saved["use_ext"]
            rs.image_settings.file_format = saved["fmt"]
            rs.image_settings.color_mode = saved["color_mode"]
            rs.film_transparent = saved["film_transparent"]
            try:
                sc.render.engine = saved["engine"]
            except Exception:
                pass
            ee = getattr(sc, "eevee", None)
            if ee is not None and "eevee.taa_render_samples" in samp_before:
                try:
                    ee.taa_render_samples = samp_before["eevee.taa_render_samples"]
                except Exception:
                    pass
            if "cycles.samples" in samp_before:
                try:
                    sc.cycles.samples = samp_before["cycles.samples"]
                except Exception:
                    pass
            if view_transform and css is not None and vt_before is not None:
                try:
                    css.view_transform = vt_before
                except Exception:
                    pass
            db_after = _db_counts()
        except Exception as e2:
            if err is None:
                err = {"error": "还原现场失败: %s" % str(e2)[:200]}

    # ---- v0.8.10（D1）：临时资源泄漏自检
    db_delta = {}
    try:
        if db_before and db_after:
            for k, v0 in db_before.items():
                v1 = db_after.get(k)
                if isinstance(v0, int) and isinstance(v1, int) and v1 != v0:
                    db_delta[k] = v1 - v0
        if db_delta:
            scope_warnings.append("临时资源计数未回到基线：%s（灯/相机泄漏会拖慢后续渲染）" % db_delta)
    except Exception:
        pass
    # ---- v0.8.11（A）：parts_color 还原核对不过 = 场景被污染 → 不静默成功
    if parts_enabled and pc_restore is not None:
        if not pc_restore.get("ok"):
            scope_warnings.append("parts_color 现场还原**没通过核对**：%s"
                                  % (_j(pc_restore.get("mismatches"))[:400]))
            if err is None:
                err = {"error": "parts_color 还原核对失败（场景材质槽可能与渲染前不同，务必先检查再继续出图）",
                       "restore": pc_restore}
        parts_info["restore"] = pc_restore
    total_ms = int(round((time.perf_counter() - t0) * 1000.0))
    within = (not stopped_early) if budget_ms else True
    out = {"ok": err is None and len(entries) > 0,
           "version": QC_RENDER_VERSION,
           "file": bpy.data.filepath, "opened_file": opened,
           "mode": mode,
           "outdir": outdir, "jsonl": jsonl, "jsonl_lines": len(entries),
           "views": entries, "count": len(entries),
           "total_ms": total_ms, "render_ms": spent_ms,
           "budget_ms": budget_ms, "within_budget": within, "stopped_early": stopped_early,
           "skipped_views": skipped_views,
           "engine": _engine_label(), "engine_mode": eng_mode, "samples": samples,
           # ---- v0.8.11（B）：本批实际的渲染设备（顶部汇总 = **渲完之后**的实测回读，最接近"实际用的"）
           "device": device_used,
           "device_before_render": dev_batch,
           "device_line": _device_line(dev_after if dev_after is not None else dev_batch),
           "device_check": device_check,
           # ---- v0.8.11（C）：视角目录/别名/未知名
           "view_order": view_order, "catalog_ortho": catalog_ortho,
           "views_requested": ([str(x) for x in views] if views_from == "request" else None),
           "views_from": views_from, "render_order": [str(v.get("name")) for v in vlist],
           "unknown_views": unknown_views, "view_aliases_applied": alias_applied,
           "deduped_views": deduped_views, "default_views_fallback": (list(DEFAULT_VIEWS_FALLBACK) if fallback_used else None),
           "view_group_of": dict((str(v.get("name")), VIEW_GROUP_OF.get(str(v.get("name")))) for v in vlist
                                 if str(v.get("name")) in VIEW_GROUP_OF),
           "parts_color": parts_info,
           "res": base_res, "objects": nobj, "aabb": {"min": [round(float(x), 4) for x in mn],
                                                      "max": [round(float(x), 4) for x in mx],
                                                      "size": [round(float(x), 4) for x in size],
                                                      "center": [round(float(x), 4) for x in center]},
           "skipped_objects": skipped, "lights": {"mode": lights_mode, "config": lights_cfg,
                                                  "angles_deg": LIGHT_ANGLES, "relative_to": "camera azimuth"},
           "margin": margin,
           "warmup": warmup_info, "per_view_budget_ms": per_view_budget_ms,
           "ladder": ladder, "ladder_final_step": (ladder_idx if ladder else None),
           "engine_check": engine_check,
           "scope_warnings": scope_warnings, "targets_resolved": targets_resolved,
           "datablocks": {"before": db_before, "after": db_after, "delta": db_delta},
           "sheet": sheet_path, "sheet_tiles": sheet_metrics,
           "note": "每行 jsonl = 一张图；ms 是单张渲染耗时（首张含 EEVEE 着色器编译）；"
                   "device 是**实测**的渲染设备（null = 拿不到判据，见 device_source，绝不许猜）；"
                   "within_budget=false 表示累计超预算已停（已出的图保留）"}
    if err is not None:
        out["error"] = err
    return _j(out)


def qc_render_help():
    return _j({
        "version": QC_RENDER_VERSION,
        "entry": ["blender_rt_plan(op='qc_render_views', args={...})",
                  "blender_rt_plan(op='qc_render_help')  # 本速查",
                  "blender_rt_plan(op='qc_render_catalog')  # 视角目录（22 名 / 4 组 / 别名）",
                  "blender_rt_plan(op='qc_render_device_info')  # 当前设备实测（不渲染）",
                  "blender_rt_plan(op='qc_render_selftest', args={...})  # 自检：GUI 里会拒绝，走无头",
                  "blender_rt_headless(file=..., preload='qc,qc_render', script=...)  # 长活/隔离"],
        "args": {"file": ".blend（GUI 需 allow_open_file）", "views": "具名/az+el/显式 from+look_at",
                 "res": "[w,h] 或单个数", "samples": "默认 64", "budget_s|thr": "累计渲染秒数上限",
                 "outdir": "默认 K.out_dir", "ref_path/ref_box/ref_search": "可选参考比对（qc.py compare）",
                 "lights": "{key,fill,rim} 或 false", "lights_mode": "add|only", "margin": "默认 1.12",
                 "engine": "keep|eevee|cycles", "tag": "文件名前缀", "targets": "限定参与取景的对象",
                 "mode": "\"views\"（默认）| \"parts_color\"（逐部件配色）",
                 "parts_color": "true = mode=\"parts_color\"", "parts_group_by": "object（默认）| collection",
                 "parts_flat": "false=Principled 纯色（默认，有明暗）| true=Emission 纯色（颜色最准）",
                 "parts_palette": "自定义调色板 [[R,G,B],...]（sRGB 0-255，默认 12 色）",
                 "parts_legend": "图例 .txt 路径（默认 <outdir>/parts_color_meta.txt，另写同名 .json）",
                 "catalog_ortho": "true = 具名视角按目录投影（六面正交）；默认 false 走老口径（透视）",
                 "view_order": "catalog（默认，按目录顺序+去重）| request（严格按请求顺序）",
                 "strict_views": "true = 有未知视角名就 ok:false（默认只跳过并报 unknown_views）"},
        "named_views": NAMED_VIEWS,
        "view_groups": dict(VIEW_GROUPS),
        "view_menu": view_menu_text(),
        "view_aliases": dict(VIEW_ALIASES),
        "default_views_legacy": list(DEFAULT_VIEWS_LEGACY),
        "default_views_fallback": list(DEFAULT_VIEWS_FALLBACK),
        "parts_palette": [[int(c[0]), int(c[1]), int(c[2])] for c in PARTS_PALETTE],
        "device": "每行 jsonl 的 device = **实测**渲染设备（Cycles 静默回落 CPU 是 ≈7× 慢且无症状的坑）；"
                  "拿不到就 null + device_source，绝不猜。返回体顶部另有 device 汇总 + device_line 一行版",
        "outputs": ["<outdir>/<tag>_<view>.png",
                    "<outdir>/render_views.jsonl（每行 {view, ms, bytes, hash, device, ...}）",
                    "parts_color 模式：<outdir>/parts_color_meta.txt（每行 `名字<TAB>R,G,B<TAB>面数`，"
                    "行数 == 部件数）+ 同名 .json"],
        "guarantees": ["相机/三点光只在进程内临时建，finally 删除", "渲染设置全部还原",
                       "parts_color 覆盖的材质槽/临时材质/链接副本 mesh 全部还原并**逐项核对**，"
                       "核对不过直接 ok:false",
                       "未知名视角原样进 unknown_views（绝不静默替换成 iso）",
                       "GUI 不打开别的 .blend", "预算超了立即停、已出的图保留"],
    })


def _selftest_projection():
    """老自检内容（v0.8.8 原样保留）：解算出的相机用 Blender 自己的 world_to_camera_view 复核。
    给出每个具名视角的：解算距离 / 8 角投影后的像素 bbox / 是否全部在画幅内 /
    自算矩阵与 Blender 真值逐点像素差（proj_err_px）。"""
    box_mn = Vector((-1.0, -0.5, 0.0))
    box_mx = Vector((1.0, 0.5, 2.0))
    center = (box_mn + box_mx) * 0.5
    corners = _corners(box_mn, box_mx)
    w = h = 512
    lens, sensor = 50.0, 36.0
    margin = 1.12
    sc = bpy.context.scene
    rx, ry = int(sc.render.resolution_x), int(sc.render.resolution_y)
    sc.render.resolution_x, sc.render.resolution_y = w, h
    cd = bpy.data.cameras.new("DSH_QC_SELFTEST_CAM")
    cd.lens = lens
    cd.sensor_width = sensor
    cd.sensor_fit = "AUTO"
    ob = bpy.data.objects.new("DSH_QC_SELFTEST_CAM", cd)
    sc.collection.objects.link(ob)
    ob.rotation_mode = "QUATERNION"
    rows = []
    srcs = {}
    errs = []
    try:
        for name, (az, el) in sorted(NAMED_VIEWS.items()):
            loc, d, osc, st = _solve_view(center, corners, az, el, w, h, lens, sensor, margin)
            ob.location = loc
            ob.rotation_quaternion = d.to_track_quat("-Z", "Y")
            try:
                bpy.context.view_layer.update()
            except Exception:
                pass
            bbox, src, err = _project_px(ob, corners, w, h)
            srcs[src] = srcs.get(src, 0) + 1
            if err is not None:
                errs.append(err)
            rows.append({"view": name, "dist": round(st["dist"], 4),
                         "bbox_px": bbox,
                         "inside_frame": bool(bbox[0] >= -0.5 and bbox[1] >= -0.5 and bbox[2] <= w + 0.5 and bbox[3] <= h + 0.5),
                         "fill": round(max((bbox[2] - bbox[0]) / float(w), (bbox[3] - bbox[1]) / float(h)), 4),
                         "proj_err_px": err})
    finally:
        try:
            bpy.data.objects.remove(ob, do_unlink=True)
            bpy.data.cameras.remove(cd)
        except Exception:
            pass
        sc.render.resolution_x, sc.render.resolution_y = rx, ry
    return {"res": [w, h], "margin": margin, "views": rows, "proj_sources": srcs,
            "proj_err_px_max": (round(max(errs), 6) if errs else None),
            "all_inside": all(r["inside_frame"] for r in rows),
            "expect": "all_inside=true；fill ≈ %.3f（=1/margin）；proj_err_px ≈ 0（自算矩阵 vs Blender 真值）"
                      % (1.0 / margin)}


# ---------------------------------------------------------------- 场景快照（自检用）

def _scene_snapshot():
    """场景快照：对象数 + 逐对象材质槽(link/材质名)/可见性/mesh 与用户数 + 渲染设置 + datablock 计数。

    为什么逐对象记材质槽：parts_color 是**改材质槽**实现的，还原漏一个对象就等于污染了用户的成品文件；
    link 也要记（DATA/OBJECT 槽的写入口不同），mesh 名与 users 一起记（链接副本会临时换 mesh）。
    """
    sc = bpy.context.scene
    rs = sc.render
    objs = {}
    for ob in sc.objects:
        try:
            slots = [[str(s.link), (str(s.material.name) if s.material else None)] for s in ob.material_slots]
        except Exception:
            slots = None
        objs[str(ob.name)] = {"type": str(ob.type), "slots": slots,
                              "hide_render": bool(ob.hide_render),
                              "mesh": (str(ob.data.name) if ob.data is not None else None),
                              "mesh_users": (int(getattr(ob.data, "users", 0) or 0) if str(ob.type) == "MESH" else None)}
    ee = getattr(sc, "eevee", None)
    counts = _db_counts()
    try:
        # 渲染器自己的常驻图像（"Render Result" / "Viewer Node"）在**首次渲染时**才会出现，不是现场改动 ——
        # 快照里剔掉它们，否则 -b 新进程里自检会把自己第一次渲染误判成"没还原"（实测首渲 +2 个 image）。
        counts["images"] = len([i for i in bpy.data.images
                                if str(i.name) not in ("Render Result", "Viewer Node")])
        counts["images_note"] = "已剔除渲染器常驻的 Render Result / Viewer Node（不是现场改动）"
    except Exception:
        pass
    return {"objects": objs, "n_objects": len(sc.objects),
            "render": {"engine": str(rs.engine), "res": [int(rs.resolution_x), int(rs.resolution_y)],
                       "pct": int(rs.resolution_percentage), "film_transparent": bool(rs.film_transparent),
                       "filepath": str(rs.filepath), "format": str(rs.image_settings.file_format),
                       "color_mode": str(rs.image_settings.color_mode), "frame": int(sc.frame_current)},
            "camera": (str(sc.camera.name) if sc.camera else None),
            "view_transform": str(getattr(getattr(sc, "view_settings", None), "view_transform", None)),
            "samples": {"eevee_taa": (int(getattr(ee, "taa_render_samples", -1)) if ee is not None else None),
                        "cycles": (int(sc.cycles.samples) if hasattr(sc, "cycles") else None)},
            "counts": counts}


def _snap_diff(a, b, path=""):
    """逐项比较两个快照 → 差异字符串列表（空 = 逐项相等）。自检用它断言"渲染后场景 = 渲染前场景"。"""
    diffs = []
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(list(a.keys()) + list(b.keys()))):
            p = ("%s.%s" % (path, k)) if path else str(k)
            if k not in a:
                diffs.append("%s：渲染前没有、渲染后多出 %r" % (p, b[k]))
            elif k not in b:
                diffs.append("%s：渲染前有、渲染后丢了（前 %r）" % (p, a[k]))
            else:
                diffs.extend(_snap_diff(a[k], b[k], p))
        return diffs
    if a != b:
        diffs.append("%s：%r != %r" % (path or "(root)", a, b))
    return diffs


def _png_palette_hits(path, rgbs, tol=16):
    """读 PNG，数每个图例色在图里出现的像素数（±tol/255 逐通道）。

    为什么这么判：颜色编码图的价值全在"图例对不对得上图" —— flat(Emission)+Standard 下
    部件实心区的像素就是图例色，对不上说明材质/色彩变换/图例三者有一个错了。
    """
    img = bpy.data.images.load(path, check_existing=False)
    try:
        w, h = int(img.size[0]), int(img.size[1])
        buf = np.empty(w * h * 4, dtype=np.float32)
        img.pixels.foreach_get(buf)
    finally:
        bpy.data.images.remove(img)
    px = np.clip(buf.reshape(h, w, 4)[:, :, :3], 0.0, 1.0) * 255.0
    hits = []
    for rgb in rgbs:
        d = np.abs(px - np.array([float(rgb[0]), float(rgb[1]), float(rgb[2])], dtype=np.float32))
        hits.append(int(np.count_nonzero(np.all(d <= float(tol), axis=2))))
    return hits


def _st_box_mesh(name, size=1.0, loc=(0.0, 0.0, 0.0)):
    """自检用的方块 mesh（from_pydata 手搓：不依赖 bpy.ops 的上下文，无头/GUI 都能跑）。"""
    s = float(size) * 0.5
    verts = [(-s, -s, -s), (s, -s, -s), (s, s, -s), (-s, s, -s),
             (-s, -s, s), (s, -s, s), (s, s, s), (-s, s, s)]
    faces = [(0, 1, 2, 3), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    me = bpy.data.meshes.new(name + "_mesh")
    me.from_pydata([(v[0] + loc[0], v[1] + loc[1], v[2] + loc[2]) for v in verts], [], faces)
    me.update()
    return me


def qc_render_selftest(args=None):
    """渲染 harness 自检（v0.8.11 扩展）：投影对拍（老内容）+ Procedura 融合三项能力的**断言**。

    **必须在无头隔离进程里跑**（会临时建对象、覆盖材质槽、出图；虽然全部还原，但你在看着的场景不该被碰）：
        blender_rt_headless(preload="qc,qc_render", engine="eevee", factory_startup=True, timeout_ms=300000,
                            script="import json;print('HEADLESS ' + K.dsh_qc_render_api['selftest']())")
    GUI 里默认拒绝；确实要跑传 {"allow_gui": true}（自检自己建的对象会删干净，但你的场景会被渲几张）。

    args：{"outdir": 产物目录（默认 K.out_dir）, "res": 默认 [192,192], "samples": 默认 16,
           "allow_gui": bool, "views_batch1": [...], "keep_files": bool}

    断言（ok = 全部 pass；每条都在 assertions 里给 pass + detail，不静默）：
      A1 parts_color 出图（≥1 张、字节 > 0）                A2 渲后场景**逐项还原**（对象数/材质槽/引擎/分辨率/采样/色彩变换）
      A3 图例存在且行数 == 部件数                            A4 flat 图上能找到每一个部件的图例色（颜色编码没写错）
      B1 jsonl 每行都有 device 键（值可为 null）             B2 device 是实测：给出 engine/device 或 null+出处
      C1 未知名进 unknown_views，已知视角照常渲              C2 别名归一（iso-FR-top/isometric/isoright → iso）
      C3 老具名视角角度逐项未变（LEGACY_NAMED_VIEWS）        C4 目录顺序 + 去重生效（catalog 口径）
      D1 投影对拍 proj_err_px ≈ 0（老自检内容）
      E1 材质覆盖确实进了渲染（同视角 plain vs parts_color 的 hash 不同）
      E2 自检对象清理干净（场景回到进入自检前的快照）
    """
    a = _unpack_args(args)
    assertions = []

    def _chk(name, ok, detail):
        assertions.append({"name": name, "pass": bool(ok), "detail": detail})
        return bool(ok)

    if not bool(getattr(bpy.app, "background", False)) and not a.get("allow_gui"):
        return _j({"ok": False, "version": QC_RENDER_VERSION, "kind": "qc_render_selftest",
                   "error": "GUI 进程里默认不跑自检（会临时改当前场景的材质槽并渲几张）",
                   "hint": "无头隔离跑：blender_rt_headless(preload=\"qc,qc_render\", engine=\"eevee\", "
                           "factory_startup=True, timeout_ms=300000, "
                           "script=\"import json;print('HEADLESS ' + K.dsh_qc_render_api['selftest']())\")；"
                           "确要在 GUI 跑就传 {\"allow_gui\": true}"})
    outdir = _win(a.get("outdir") or _out_dir())
    rv = a.get("res") or [192, 192]
    if isinstance(rv, (int, float)):
        rv = [int(rv), int(rv)]
    res = [max(64, int(rv[0])), max(64, int(rv[1]))]
    samples = int(a.get("samples") or 16)
    try:
        os.makedirs(outdir, exist_ok=True)
    except Exception as e:
        return _j({"ok": False, "error": "outdir 建不出来: %s (%s)" % (outdir, e)})
    sc = bpy.context.scene
    snap0 = _scene_snapshot()
    made_objs, made_meshes, made_mats = [], [], []
    batch1 = batch2 = proj = None
    parts_n = legend_lines = None
    hits = None
    cleanup_diffs = None
    try:
        # ---- 临时部件：4 个（含 1 对**共享 mesh 的链接副本** + 1 个 0 槽对象）——
        # 沿 X 一字排开（俯视时一个都不缺），两条都踩在 parts_color 最容易出错的点上。
        # 注意：链接副本的两个对象**位置必须分开**（早先把它们放重叠 → 有一只被完全遮住，
        # 图例色在图上找不到 → A4 判不出"是颜色错了还是被挡住了"）。
        base_me = _st_box_mesh("DSH_QC_ST_B", 1.0)
        for m in (bpy.data.materials.new("DSH_QC_ST_M0"), bpy.data.materials.new("DSH_QC_ST_M1")):
            base_me.materials.append(m)
            made_mats.append(m)
        ob_a = bpy.data.objects.new("DSH_QC_ST_A", _st_box_mesh("DSH_QC_ST_A", 1.0))
        made_meshes.append(ob_a.data)
        mt = bpy.data.materials.new("DSH_QC_ST_MA")
        ob_a.data.materials.append(mt)
        made_mats.append(mt)
        ob_b = bpy.data.objects.new("DSH_QC_ST_B", base_me)          # 与 ob_c 共享 mesh（链接副本）
        ob_c = bpy.data.objects.new("DSH_QC_ST_C", base_me)
        made_meshes.append(base_me)
        ob_d_me = _st_box_mesh("DSH_QC_ST_D", 1.0)                   # 0 材质槽
        ob_d = bpy.data.objects.new("DSH_QC_ST_D", ob_d_me)
        made_meshes.append(ob_d_me)
        for ob, loc in ((ob_a, (3.2, 0.0, 0.5)), (ob_b, (4.8, 0.0, 0.5)),
                        (ob_c, (6.4, 0.0, 0.5)), (ob_d, (8.0, 0.0, 0.5))):
            ob.location = loc
            sc.collection.objects.link(ob)
            made_objs.append(ob)
        try:
            bpy.context.view_layer.update()
        except Exception:
            pass
        parts_n = len(_collect_objs(None)[0])          # 部件数 = 全部可见 mesh（含默认场景自带的）
        snap1 = _scene_snapshot()
        outdir_msgs = []

        # ---- 批次 1：parts_color（flat；top 视角让 5 个部件一个都不缺 —— A4 就靠它判"图例对不对得上图"）
        b1_views = a.get("views_batch1") or ["iso", "front", "top", "totally_bogus_view_xyz"]
        r1 = json.loads(qc_render_views({
            "mode": "parts_color", "parts_flat": True, "views": b1_views, "res": res, "samples": samples,
            "outdir": outdir, "tag": "selftest_pc", "warmup": True, "budget_s": 240,
            "jsonl": os.path.join(outdir, "selftest_pc.jsonl"), "lights_mode": "only"}))
        snap2 = _scene_snapshot()
        restore_diffs = _snap_diff(snap1, snap2)
        files = [e.get("path") for e in (r1.get("views") or [])]
        sizes = [int(e.get("bytes") or 0) for e in (r1.get("views") or [])]
        _chk("A1 parts_color 出图", bool(r1.get("ok")) and len(files) >= 1 and all(s > 0 for s in sizes),
             "ok=%s count=%s bytes=%s" % (r1.get("ok"), len(files), sizes))
        _chk("A2 渲后场景逐项还原（对象/材质槽/引擎/分辨率/采样/色彩变换）", not restore_diffs,
             ("逐项相等" if not restore_diffs else "差异：%s" % restore_diffs[:6]) + "；还原核对=%s"
             % _j((r1.get("parts_color") or {}).get("restore", {}).get("ok")))
        legend = (r1.get("parts_color") or {}).get("legend_path")
        legend_lines = (_legend_line_count(legend) if (legend and os.path.isfile(legend)) else None)
        _chk("A3 图例存在且行数 == 部件数", bool(legend and os.path.isfile(legend))
             and legend_lines == parts_n == (r1.get("parts_color") or {}).get("parts"),
             "legend=%s 行数=%s；独立数出来的可见 mesh=%s；结果里报的部件数=%s"
             % (legend, legend_lines, parts_n, (r1.get("parts_color") or {}).get("parts")))
        # A4：flat 图上色标必须能找到（用 top 视角那一张）
        used = []
        for row in ((r1.get("parts_color") or {}).get("assignments") or {}).values():
            if row.get("rgb"):
                used.append(row["rgb"])
        top_entry = None
        for e in (r1.get("views") or []):
            if str(e.get("view")) == "top":
                top_entry = e
        if top_entry is None and (r1.get("views") or []):
            top_entry = r1["views"][0]
        if top_entry and used:
            try:
                hits = _png_palette_hits(top_entry["path"], used)
            except Exception as e:
                hits = None
                outdir_msgs.append("读图失败：%s" % str(e)[:120])
            _chk("A4 图例色能在 flat 图上找到（颜色编码可对拍）", bool(hits) and all(h > 0 for h in hits),
                 "view=%s 各色像素命中=%s（tol=16/255；0 = 该部件被完全遮挡或材质/色彩变换不对）"
                 % (top_entry.get("view"), hits))
        # B1/B2：jsonl 每行有 device 键；device 是实测（null 也要给出处）
        jl = r1.get("jsonl")
        lines = []
        if jl and os.path.isfile(jl):
            with open(jl, encoding="utf-8") as f:
                for ln in f:
                    if ln.strip():
                        lines.append(json.loads(ln))
        has_dev = [("device" in ln) for ln in lines]
        dev_ok = [bool(ln.get("device")) and ln["device"].get("engine") for ln in lines]
        _chk("B1 jsonl 每行都有 device 键", bool(lines) and all(has_dev),
             "jsonl=%s 行数=%d 都有 device 键=%s；device=%s"
             % (jl, len(lines), all(has_dev), _j([ln.get("device") for ln in lines[:2]])))
        _chk("B2 device 是实测（null 时给 device_source）", bool(lines) and all(
            (d and d.get("device") and d.get("source")) or ((not d or not d.get("device")) and ln.get("device_source"))
            for d, ln in zip([ln.get("device") for ln in lines], lines)),
            "本批 device=%s / source=%s" % (_j((r1.get("device") or {}).get("device")),
                                            (r1.get("device") or {}).get("source")))
        # C1：未知名进 unknown_views，已知视角照常渲
        known = [str(e.get("view")) for e in (r1.get("views") or [])]
        _chk("C1 未知名进 unknown_views 且已知视角照常渲",
             (r1.get("unknown_views") == ["totally_bogus_view_xyz"]) and len(known) == 3
             and set(known) == set(["iso", "front", "top"]) and known == ["front", "top", "iso"],
             "unknown_views=%s render_order=%s（目录顺序）count=%s"
             % (_j(r1.get("unknown_views")), known, r1.get("count")))
        # C2/C3/C4：纯函数口径（不渲染，快）
        rr = resolve_view_names(["iso-FR-top", "isometric", "isoright", "front-left", "ISO_L"])
        _chk("C2 别名归一（iso-FR-top/isometric/isoright → iso；front-left/ISO_L 直接命中目录）",
             rr["names"] == ["iso", "iso_l", "front_left"] and not rr["unknown"]
             and len(rr["aliases"]) == 3 and [x["resolved"] for x in rr["aliases"]] == ["iso"] * 3,
             "names=%s aliases=%s unknown=%s" % (_j(rr["names"]), _j(rr["aliases"]), _j(rr["unknown"])))
        bad = [k for k, v in LEGACY_NAMED_VIEWS.items() if NAMED_VIEWS.get(k) != v]
        _chk("C3 老具名视角角度逐项未变", not bad and len(VIEW_CATALOG) >= len(LEGACY_NAMED_VIEWS),
             "老名 %d 个全等（差异 %s）；目录共 %d 名" % (len(LEGACY_NAMED_VIEWS), bad or "无", len(VIEW_CATALOG)))
        rr2 = resolve_view_names(["top", "front", "front", "iso"])
        _chk("C4 目录顺序 + 去重（catalog 口径）",
             rr2["names"] == ["front", "top", "iso"] and rr2["deduped"] == ["front"],
             "names=%s deduped=%s（目录顺序取目录，不是请求顺序）" % (_j(rr2["names"]), _j(rr2["deduped"])))
        # E1：材质覆盖确实进了渲染（同视角、同色彩变换，只有材质不同 → hash 必须不同）
        r2 = json.loads(qc_render_views({
            "views": ["iso"], "res": res, "samples": samples, "outdir": outdir, "tag": "selftest_plain",
            "warmup": False, "view_transform": "Standard",
            "jsonl": os.path.join(outdir, "selftest_plain.jsonl"), "lights_mode": "only"}))
        h_pc = None
        for e in (r1.get("views") or []):
            if str(e.get("view")) == "iso":
                h_pc = e.get("hash")
        h_plain = ((r2.get("views") or [{}])[0]).get("hash")
        _chk("E1 材质覆盖确实进了渲染（plain vs parts_color 同视角 hash 不同）",
             bool(h_pc) and bool(h_plain) and h_pc != h_plain,
             "parts_color.iso=%s plain.iso=%s" % (h_pc, h_plain))
        snap3 = _scene_snapshot()
        d3 = _snap_diff(snap1, snap3)
        _chk("E1b 第二批复用同一现场也还原（plain 批次）", not d3, ("逐项相等" if not d3 else "差异：%s" % d3[:6]))
        batch1, batch2 = r1, r2
    finally:
        for ob in made_objs:
            try:
                bpy.data.objects.remove(ob, do_unlink=True)
            except Exception:
                pass
        for me in made_meshes:
            try:
                if me.users == 0:
                    bpy.data.meshes.remove(me)
            except Exception:
                pass
        for m in made_mats:
            try:
                bpy.data.materials.remove(m, do_unlink=True)
            except Exception:
                pass
        try:
            bpy.context.view_layer.update()
        except Exception:
            pass
        cleanup_diffs = _snap_diff(snap0, _scene_snapshot())

    # ---- D1：投影对拍（老自检内容）
    try:
        proj = _selftest_projection()
    except Exception as e:
        proj = {"error": "%s: %s" % (type(e).__name__, str(e)[:200])}
    _chk("D1 投影对拍 proj_err_px ≈ 0（自算矩阵 vs Blender 真值）",
         bool(proj) and proj.get("all_inside") and (proj.get("proj_err_px_max") is None
                                                    or proj["proj_err_px_max"] < 0.05),
         "all_inside=%s proj_err_px_max=%s 视角数=%s" % (proj.get("all_inside"), proj.get("proj_err_px_max"),
                                                       len(proj.get("views") or [])))
    _chk("E2 自检对象/材质/mesh 清理干净（回到进入自检前的快照）", not cleanup_diffs,
         ("逐项相等" if not cleanup_diffs else "差异：%s" % cleanup_diffs[:8]))

    failed = [x["name"] for x in assertions if not x["pass"]]
    out = {"ok": not failed, "version": QC_RENDER_VERSION, "kind": "qc_render_selftest",
           "blender": str(bpy.app.version_string), "background": bool(bpy.app.background),
           "file": bpy.data.filepath, "outdir": outdir, "res": res, "samples": samples,
           "assertions": assertions, "failed": failed,
           "parts_color_batch": ({"ok": batch1.get("ok"), "count": batch1.get("count"),
                                  "mode": batch1.get("mode"), "jsonl": batch1.get("jsonl"),
                                  "legend": (batch1.get("parts_color") or {}).get("legend_path"),
                                  "legend_lines": legend_lines,
                                  "parts": (batch1.get("parts_color") or {}).get("parts"),
                                  "restore": (batch1.get("parts_color") or {}).get("restore"),
                                  "view_transform_defaulted":
                                      (batch1.get("parts_color") or {}).get("view_transform_defaulted"),
                                  "palette_hits": hits} if batch1 else None),
           "plain_batch": ({"ok": batch2.get("ok"), "count": batch2.get("count"),
                            "jsonl": batch2.get("jsonl")} if batch2 else None),
           "device": (batch1.get("device") if batch1 else None),
           "device_line": (batch1.get("device_line") if batch1 else None),
           "views": ({"unknown_views": batch1.get("unknown_views"), "render_order": batch1.get("render_order"),
                      "aliases": batch1.get("view_aliases_applied"), "menu": view_menu_text()} if batch1 else None),
           "projection": proj,
           "expect": "ok=true 表示 A1-A4/B1-B2/C1-C4/D1/E1-E2 全过；任一不过 → ok=false 且 failed 里点名"}
    # 自检报告落盘：证据要能被人和其他会话复核（工具返回值会被日志截断，磁盘上的不会）
    try:
        rp = _win(a.get("report") or os.path.join(outdir, "qc_render_selftest.json"))
        with open(rp, "w", encoding="utf-8") as f:
            f.write(_j(out))
        out["report_path"] = rp
    except Exception as e:
        out["report_error"] = "%s: %s" % (type(e).__name__, str(e)[:160])
    return _j(out)


def qc_render_view_menu():
    """纯文本速查（按组分列具名视角）：给提示词/人直接抄。"""
    return _j({"ok": True, "version": QC_RENDER_VERSION, "menu_text": view_menu_text(),
               "groups": [{"id": g, "label": lb} for g, lb in VIEW_GROUPS]})


def qc_render_device_info():
    """当前进程的渲染设备实测（不渲染、不改设置）—— 排查"是不是悄悄跑了 CPU"的第一站。"""
    info = _device_info()
    return _j({"ok": True, "version": QC_RENDER_VERSION, "device": info, "device_line": _device_line(info)})


def qc_render_dispatch(op, args=None):
    """op 两种拼写都认 —— engine 路由会把 `qc_render_<X>` 的前 3 个字符剥掉，于是收到的是 `render_<X>`：

        blender_rt_plan(op="qc_render_views", args={...})     → dispatch("render_views")
        blender_rt_plan(op="qc_render_help")                  → dispatch("render_help")
        blender_rt_plan(op="qc_render_selftest", args={...})  → dispatch("render_selftest")
        blender_rt_plan(op="qc_render_catalog")               → dispatch("render_catalog")
        blender_rt_plan(op="qc_render_device_info")           → dispatch("render_device_info")

    坑（v0.8.11 修）：老 dict 里只登记了 "render_views"，所以 docs/操作教程.md 里写的
    `op="qc_render_help"` 其实一直落到 unknown op（实际收到的是 "render_help"）—— 这里把
    render_* 别名补齐，老拼写（views/render_views/qc_render_views）一个不动。
    参数容忍 JSON 字符串 / {"args": {...}} 再包一层（照抄 qc_render_views 的既有解包口径）。
    """
    import inspect
    ops = {"views": qc_render_views, "render_views": qc_render_views, "qc_render_views": qc_render_views,
           "help": qc_render_help, "render_help": qc_render_help,
           "selftest": qc_render_selftest, "render_selftest": qc_render_selftest,
           "catalog": qc_render_view_catalog, "views_catalog": qc_render_view_catalog,
           "render_catalog": qc_render_view_catalog, "render_views_catalog": qc_render_view_catalog,
           "view_menu": qc_render_view_menu, "render_view_menu": qc_render_view_menu,
           "device_info": qc_render_device_info, "render_device_info": qc_render_device_info}
    fn = ops.get(str(op))
    if fn is None:
        return _j({"ok": False, "error": "unknown qc_render op", "op": op, "ops": sorted(ops)})
    # 按签名决定要不要喂参数：**不再用 "TypeError 就重试"** —— 渲染中途抛 TypeError 会被重试一遍（重复出图）
    try:
        params = inspect.signature(fn).parameters
        wants = any(p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD) for p in params.values())
    except Exception:
        wants = True
    try:
        if not wants:
            return fn()
        return fn(_unpack_args(args) if args is not None else {})
    except Exception as e:
        return _j({"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:200]), "op": op})


import sys as _sys
_K = _sys.modules.get("dsh_rt_kernel")
if _K is not None:
    _K.dsh_qc_render_api = {"version": QC_RENDER_VERSION, "dispatch": qc_render_dispatch,
                            "render_views": qc_render_views, "help": qc_render_help,
                            "selftest": qc_render_selftest,
                            # v0.8.11 新增（只加不改）：
                            "view_catalog": qc_render_view_catalog, "view_menu_text": view_menu_text,
                            "view_menu": qc_render_view_menu, "resolve_view_names": resolve_view_names,
                            "device_info": qc_render_device_info, "parts_palette": PARTS_PALETTE}
