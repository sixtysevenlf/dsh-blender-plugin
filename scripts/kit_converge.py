import pathlib, sys
RT = pathlib.Path("/home/sixtyseven67/DSH/dsh-blender-plugin/runtime")
dry = "--dry" in sys.argv

def sub(path, old, new, why):
    p = RT / path
    t = p.read_text(encoding="utf-8")
    if old not in t:
        return "未命中: %s (%s)" % (path, why)
    if not dry:
        p.write_text(t.replace(old, new, 1), encoding="utf-8")
    return "✓ %s (%s)" % (path, why)

report = []

# ① qc.py _iou_pair：语义与 kit 完全一致，但空并集时返回 0.0（kit 返回 None）⇒ 适配
report.append(sub("qc.py",
    """def _iou_pair(a, b):\n    inter = int(np.logical_and(a, b).sum())\n    uni = int(np.logical_or(a, b).sum())\n    return (inter / uni) if uni else 0.0, inter, uni""",
    """def _iou_pair(a, b):\n    # S2：度量收敛到共享内核（唯一实现）；空并集时 kit 给 None，这里按旧契约返回 0.0\n    iou, inter, uni = _KIT.iou(a, b)\n    return (0.0 if iou is None else iou), inter, uni""",
    "IoU 收敛"))

# ② qc.py qc_iou：核心三个数交给 kit，保留自身的 dict 形状与 a_px/b_px
report.append(sub("qc.py",
    """    inter = int(np.logical_and(a2, b2).sum())\n    union = int(np.logical_or(a2, b2).sum())\n    return {"iou": (inter / union) if union else 0.0, "inter": inter, "union": union,\n            "a_px": int(a2.sum()), "b_px": int(b2.sum())}""",
    """    # S2：度量收敛到共享内核（唯一实现）\n    _iou, inter, union = _KIT.iou(a2, b2)\n    return {"iou": (0.0 if _iou is None else _iou), "inter": inter, "union": union,\n            "a_px": int(a2.sum()), "b_px": int(b2.sum())}""",
    "qc_iou 收敛"))

# ③ imgtools.py 内联 IoU（空并集返回 None，与 kit 一致）
report.append(sub("imgtools.py",
    """    inter = int((A & B).sum()); union = int((A | B).sum())\n    iou = (inter / union) if union else None""",
    """    # S2：度量收敛到共享内核（唯一实现）\n    iou, inter, union = _KIT.iou(A, B)""",
    "img_diff 收敛"))

# ④ 单位换算：精确形态 → _KIT.units()（公式完全相同）
OLD = "float(bpy.context.scene.unit_settings.scale_length or 1.0) * 1000.0"
cnt = 0
for p in sorted(RT.glob("*.py")):
    if p.name == "kit.py":
        continue
    t = p.read_text(encoding="utf-8")
    n = t.count(OLD)
    if not n:
        continue
    if not dry:
        p.write_text(t.replace(OLD, "_KIT.units()"), encoding="utf-8")
    report.append("✓ %s (单位换算 %d 处 → _KIT.units())" % (p.name, n))
    cnt += n

for r in report:
    print("  " + r)
print("%s：IoU 3 处 + 单位 %d 处" % ("dry-run" if dry else "已应用", cnt))