# -*- coding: utf-8 -*-
"""Split from qc_render.py; behavior preserved by the facade."""
from .common import *  # noqa: F401,F403
from .parts import PARTS_PALETTE

def qc_render_help():
    return _j({
        "version": QC_RENDER_VERSION,
        "entry": ["blender_op(op='qc_render_views', args={...})",
                  "blender_op(op='qc_render_help')  # 本速查",
                  "blender_op(op='qc_render_catalog')  # 视角目录（22 名 / 4 组 / 别名）",
                  "blender_op(op='qc_render_device_info')  # 当前设备实测（不渲染）",
                  "blender_op(op='qc_render_selftest', args={...})  # 自检：GUI 里会拒绝，走无头",
                  "blender_run(kind='headless', file=..., preload='qc,qc_render', script=...)  # 长活/隔离"],
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
                 "strict_views": "true = 有未知视角名就 ok:false（默认只跳过并报 unknown_views）",
                 # ---- v0.9.1 ----
                 "view_transform": "\"Standard\"（默认，QC 口径）| \"scene\"（沿用场景设置，旧行为）| 具体名（AgX/Filmic/…）",
                 "lock": "默认 true：渲染前自动取渲染锁（跨进程串行化）；false 关掉",
                 "lock_wait_s": "等锁上限秒数（默认 120）；等不到 → ok:false 且一张不渲",
                 "lock_dir": "锁基址目录（默认 K.workdir）→ 锁文件 <lock_dir>/locks/render.lock",
                 "lock_holder": "持有者名（默认 DSH_SESSION→DSH_RUN_ID→pid-<pid>）；**同一会话并发多进程**要各自显式给唯一名字"},
        "views_policy": {
            "default": list(DEFAULT_VIEWS_FALLBACK),
            "default_note": "views 省略/空 = 默认 4 视角；v0.8.11 及以前是 %s（要老口径就显式传它）"
                            % ",".join(DEFAULT_VIEWS_LEGACY),
            "unknown_policy": "**非空且一个都没解析成功 → ok:false + 一张都不渲**（不兜底、不静默替换）；"
                              "部分未知名 → 已知名照渲 + requested_count/rendered_count/unknown_count",
            "counts": "返回体恒有 requested_count / rendered_count / unknown_count / no_guess_views / available_views",
            "hint": "未知名不会猜：用 22 个具名之一，或走别名表；*_end（muzzle_end/breech_end）一律不猜",
        },
        "named_views": NAMED_VIEWS,
        "view_groups": dict(VIEW_GROUPS),
        "view_menu": view_menu_text(),
        "view_menu_full": view_menu_full_text(),
        "view_aliases": dict(VIEW_ALIASES),
        "view_alias_note": "别名只做拼写容错（大小写/下划线/连字符/词序）；不做语义猜测。"
                           "v0.9.1 修正：isoright / isobackright → iso_br（后右上，与目录 desc 的 iso-BR-top 一致）",
        "no_guess": {"suffix": list(VIEW_NO_GUESS_SUFFIXES), "note": VIEW_NO_GUESS_NOTE,
                     "examples": ["muzzle_end", "breech_end", "side"]},
        "view_transform_note": "QC 默认 Standard（AgX/Filmic 把白底压到 ≈0.85，亮度阈值判读会整轮失效）；"
                               "\"scene\" = 旧行为；出图后一律还原并逐项核对；"
                               "返回体与每行 jsonl 都有 view_transform（实际生效值）",
        "lock": {"op": "render_lock / qc_render_lock",
                 "actions": {"acquire": "取锁（O_EXCL 原子创建；过期锁按**时间戳**打破 → stale_broken）",
                             "release": "放锁（holder 不匹配 → ok:false，绝不误删别人的锁）",
                             "status": "查持有者 / 已持有多久 / 剩余 TTL / 是否过期（只读）"},
                 "args": {"holder": "持有者名（默认 DSH_SESSION → DSH_RUN_ID → pid-<pid>）",
                          "ttl_s": "租约秒数（默认 600；过期判定只认 acquired_at 时间戳）",
                          "wait_s": "取不到时的等待上限（默认 0 = 立即失败；轮询 0.25s）",
                          "dir": "锁基址（默认 _work_dir()）→ <dir>/locks/render.lock"},
                 "auto": "qc_render_views 渲染前自动 acquire（lock_wait_s），finally release；"
                         "返回体与每行 jsonl 的 lock = {holder, waited_ms, acquired, stale_broken, path}"},
        "default_views_legacy": list(DEFAULT_VIEWS_LEGACY),
        "default_views_fallback": list(DEFAULT_VIEWS_FALLBACK),
        "parts_palette": [[int(c[0]), int(c[1]), int(c[2])] for c in PARTS_PALETTE],
        "device": "每行 jsonl 的 device = **实测**渲染设备（Cycles 静默回落 CPU 是 ≈7× 慢且无症状的坑）；"
                  "拿不到就 null + device_source，绝不猜。返回体顶部另有 device 汇总 + device_line 一行版",
        "outputs": ["<outdir>/<tag>_<view>.png",
                    "<outdir>/render_views.jsonl（每行 {view, ms, bytes, hash, device, view_transform, lock, ...}）",
                    "parts_color 模式：<outdir>/parts_color_meta.txt（每行 `名字<TAB>R,G,B<TAB>面数`，"
                    "行数 == 部件数）+ 同名 .json",
                    "<lock_dir>/locks/render.lock（渲染锁文件，JSON: holder/pid/acquired_at/ttl_s/note）"],
        "guarantees": ["相机/三点光只在进程内临时建，finally 删除", "渲染设置全部还原",
                       "parts_color 覆盖的材质槽/临时材质/链接副本 mesh 全部还原并**逐项核对**，"
                       "核对不过直接 ok:false",
                       "未知名视角原样进 unknown_views（绝不静默替换成 iso）；一个都没认出来 → ok:false + 空产出",
                       "view_settings.view_transform 出图后还原并**逐项核对**（没还回去 → ok:false）",
                       "渲染锁 finally release（异常也放；holder 匹配才删）",
                       "GUI 不打开别的 .blend", "预算超了立即停、已出的图保留"],
    })
