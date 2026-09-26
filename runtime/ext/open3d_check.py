import json
import sys

def main():
    p = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        import numpy as np
        import open3d as o3d
    except Exception as e:
        print("EXT " + json.dumps({"ok": False, "installed": False,
              "error": "open3d 不可用：%s" % str(e)[:160],
              "install": "python3 -m venv ext/venv && ext/venv/bin/pip install open3d"}))
        return
    m = o3d.io.read_triangle_mesh(p)
    if len(m.triangles) == 0:
        print("EXT " + json.dumps({"ok": False, "error": "读不到三角形（OBJ/PLY 路径对吗？）", "path": p}))
        return
    aabb = m.get_axis_aligned_bounding_box()
    out = {
        "ok": True, "tool": "open3d", "version": getattr(o3d, "__version__", None), "path": p,
        "vertices": int(len(m.vertices)), "triangles": int(len(m.triangles)),
        "watertight": bool(m.is_watertight()), "edge_manifold": bool(m.is_edge_manifold()),
        "vertex_manifold": bool(m.is_vertex_manifold()), "orientable": bool(m.is_orientable()),
        "self_intersecting": bool(m.is_self_intersecting()),
        "self_intersecting_triangles": int(len(m.get_self_intersecting_triangles())),
        "volume_units3": float(abs(m.get_volume())) if m.is_watertight() else None,
        "aabb_extent": [round(float(x), 4) for x in aabb.get_extent()],
        "note": "OBJ 无单位：这里的长度是模型单位；与插件侧结论对照时只看布尔与计数",
    }
    print("EXT " + json.dumps(out, ensure_ascii=False))

main()