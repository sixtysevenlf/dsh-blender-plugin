#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""S1 · kit 迁移器：把模块自带的样板（_j / _DshApi / _api）替换成共享内核 K.dsh_kit。

为什么用迁移器而不是逐个手改：30 个模块的样板**形态高度一致**（26 份 def _j、25 份 class _DshApi），
机械替换比手抄安全，而且把"迁移规则"变成可复跑、可审计的代码（本轮做完就是文档）。

只做**等价替换**（同名、同签名、同行为），不动领域逻辑：
  ① 插入 kit 引导（_KIT 解析 + 缺失即报错，不静默降级）
  ② def _j(...) 定义体  →  _j = _KIT.j
  ③ class _DshApi(dict) 定义体  →  _DshApi = _KIT.Api（尾部注册行不用改）
  ④ def _api(name) 定义体  →  _api = _KIT.api

用法：python3 scripts/kit_migrate.py --dry human faceeval imgtools calib
      python3 scripts/kit_migrate.py human faceeval imgtools calib
      python3 scripts/kit_migrate.py --all --dry
"""
import argparse
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
RT = ROOT / "runtime"

BOOT = [
    "import sys as _sys_kit",
    "_KIT = getattr(_sys_kit.modules.get(\"dsh_rt_kernel\"), \"dsh_kit\", None)",
    "if _KIT is None:",
    "    raise RuntimeError(\"%s 需要共享内核 K.dsh_kit（由 KERNEL_BOOTSTRAP 注入）\")",
]


def _strip_block(lines, start):
    """从 start（def/class 起行）吃到下一个顶层语句之前；返回 (块, 下一行索引)。"""
    i = start + 1
    while i < len(lines):
        ln = lines[i]
        if ln.strip() and not ln[:1].isspace():
            break
        i += 1
    return lines[start:i], i


def migrate(path, dry=False, name=None):
    src = path.read_text(encoding="utf-8")
    name = name or path.stem
    if "dsh_kit" in src:
        return {"file": path.name, "skipped": "已经是 kit 版"}
    lines = src.split("\n")
    changed = []
    out = []
    i = 0
    inserted_boot = False
    while i < len(lines):
        ln = lines[i]
        # ① 顶部引导：插在第一个顶层 def/class 之前
        if (not inserted_boot) and re.match(r"^(def|class) ", ln):
            out.extend([b % name if "%s" in b else b for b in BOOT])
            out.append("")
            out.append("")
            inserted_boot = True
        # ②③④ 样板替换
        m_j = re.match(r"^def _j\(", ln)
        m_api = re.match(r"^def _api\(name\)", ln)
        m_cls = re.match(r"^class _DshApi\(dict\)", ln)
        if m_j or m_api or m_cls:
            block, nxt = _strip_block(lines, i)
            if m_j:
                out.append("_j = _KIT.j  # 共享内核（原自带实现已删，见 S1）")
                changed.append("_j:%d行" % len(block))
            elif m_api:
                out.append("_api = _KIT.api  # 共享内核")
                changed.append("_api:%d行" % len(block))
            else:
                out.append("_DshApi = _KIT.Api  # 共享内核（尾部注册行无需改）")
                changed.append("_DshApi:%d行" % len(block))
            i = nxt
            continue
        out.append(ln)
        i += 1
    if not inserted_boot:
        return {"file": path.name, "skipped": "没找到顶层 def/class（结构特殊，手工处理）"}
    new = "\n".join(out)
    res = {"file": path.name, "changed": changed,
           "lines": "%d -> %d" % (len(lines), len(out))}
    if changed and not dry:
        path.write_text(new, encoding="utf-8")
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("modules", nargs="*")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()
    names = a.modules or ([] if not a.all else sorted(p.stem for p in RT.glob("*.py")))
    if not names:
        ap.error("给模块名，或 --all")
    tot = 0
    for nm in names:
        p = RT / (nm if nm.endswith(".py") else nm + ".py")
        if not p.exists():
            print("  缺少模块: %s" % nm)
            continue
        r = migrate(p, dry=a.dry)
        if r.get("skipped"):
            print("  - %-18s %s" % (r["file"], r["skipped"]))
        else:
            print("  + %-18s %s (%s)" % (r["file"], " ".join(r["changed"]) or "无改动", r["lines"]))
            tot += len(r["changed"])
    print("%s：%d 处样板替换" % ("dry-run" if a.dry else "已迁移", tot))


if __name__ == "__main__":
    main()